#!/usr/bin/env python3
"""batch-notes: fill directors notes and client notes by answering questions in chat, no text editor.

  notes.py list <drive> [--client NAME]           batches whose note is still a stub, in order, with clip counts
  notes.py show <batch>                           current fields, hints (original folders, devices, clips, minutes) and a
                                                  preview sheet at <batch>/outputs/note_preview.jpg (one frame per clip, up to 24)
  notes.py show-client <drive> <client>           current client note fields and the batches it covers
  notes.py write <batch> [--location ..] [--languages ..] [--audio ..] [--date ..] [--overview ..] [--people ..] [--notes ..] [--confirm]
  notes.py write-client <drive> <client> [--owner ..] [--languages ..] [--locations ..] [--about ..] [--people ..] [--confirm]
Empty values leave a field as it is. --confirm removes the "Status: stub" line. Nothing else in the note is touched.
"""
import argparse, os, re, subprocess, sys, json

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mxf", ".mkv"); DATED = re.compile(r"^\d\d\.\d\d\.\d\d")

def field(text, label):
    m = re.search(r"\*\*" + re.escape(label) + r":\*\*[ \t]*([^\n]*)", text); return (m.group(1).strip() if m else "")

def set_field(text, label, value):
    if value is None or value == "": return text
    pat = r"(\*\*" + re.escape(label) + r":\*\*)[ \t]*[^\n]*"
    return re.sub(pat, lambda m: f"{m.group(1)} {value}", text, count=1) if re.search(pat, text) else text.replace("\n\n## ", f"\n**{label}:** {value}\n\n## ", 1)

def set_section(text, heading, body):
    if body is None or body == "": return text
    pat = r"(## " + re.escape(heading) + r"\s*\n)(.*?)(?=\n## |\Z)"
    if re.search(pat, text, re.S): return re.sub(pat, lambda m: m.group(1) + "\n" + body.strip() + "\n", text, count=1, flags=re.S)
    return text.rstrip() + f"\n\n## {heading}\n\n{body.strip()}\n"

def bullets(value):
    if not value: return value
    lines = [x.strip() for x in re.split(r"\s*\|\s*|\n", value) if x.strip()]   # one person per line or separated by |
    return "\n".join(x if x.startswith("-") else "- " + x for x in lines)

def batches(drive, client=None):
    media = os.path.join(drive, "media"); out = []
    for c in sorted(os.listdir(media)):
        if c.startswith(".") or not os.path.isdir(os.path.join(media, c)) or (client and c != client): continue
        def walk(d):
            for b in sorted(os.listdir(d)):
                p = os.path.join(d, b)
                if b.startswith(".") or not os.path.isdir(p): continue
                if DATED.match(b): out.append((c, p))
                elif b == "media": walk(p)
        walk(os.path.join(media, c))
    return out

def note_path(b): return os.path.join(b, "directors_note.md")
def is_stub(p):
    try: return "**Status:** stub" in open(p, encoding="utf-8").read()
    except Exception: return False

def clips(b): return sorted(f for f in os.listdir(b) if f.lower().endswith(VIDEO_EXT) and not f.startswith("."))

def duration(p):
    try: return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p], capture_output=True, text=True, timeout=30).stdout)
    except Exception: return 0.0

def preview(b, n=24):
    """One frame from each of up to n clips spread across the batch, tiled with the clip name. Written to outputs/."""
    from PIL import Image, ImageDraw
    cs = clips(b)
    if not cs: return None
    pick = [cs[int(i * len(cs) / min(n, len(cs)))] for i in range(min(n, len(cs)))]
    tiles = []
    for f in pick:
        p = os.path.join(b, f); d = duration(p); tmp = os.path.join(b, "outputs", "_note_frame.jpg"); os.makedirs(os.path.dirname(tmp), exist_ok=True)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, d * 0.4):.2f}", "-i", p, "-frames:v", "1", "-vf", "scale=320:-2", tmp], capture_output=True)
        if r.returncode or not os.path.exists(tmp): continue
        im = Image.open(tmp).convert("RGB"); canvas = Image.new("RGB", (320, 200), (0, 0, 0)); canvas.paste(im, (0, 0))
        ImageDraw.Draw(canvas).text((4, 184), f"{os.path.splitext(f)[0]}  {int(d // 60)}:{int(d % 60):02d}", fill=(255, 255, 0)); tiles.append(canvas)
    try: os.remove(tmp)
    except Exception: pass
    if not tiles: return None
    cols = 4; rows = (len(tiles) + cols - 1) // cols; sheet = Image.new("RGB", (cols * 320, rows * 200), (0, 0, 0))
    for i, t in enumerate(tiles): sheet.paste(t, ((i % cols) * 320, (i // cols) * 200))
    out = os.path.join(b, "outputs", "note_preview.jpg"); sheet.save(out, "JPEG", quality=80); return out

def hints(b):
    src = os.path.join(b, "sources.json"); dirs = {}
    try:
        for e in json.load(open(src)).get("files", {}).values():
            p = (e.get("source_path") or "").split("/")
            if len(p) > 4: dirs["/".join(p[3:-1])] = dirs.get("/".join(p[3:-1]), 0) + 1
    except Exception: pass
    adir = os.path.join(b, "audio"); devs = {}
    if os.path.isdir(adir):
        for d in sorted(os.listdir(adir)):
            if not d.startswith(".") and os.path.isdir(os.path.join(adir, d)): devs[d] = len([f for f in os.listdir(os.path.join(adir, d)) if not f.startswith(".") and not f.endswith(".json")])
    cs = clips(b); mins = sum(duration(os.path.join(b, f)) for f in cs) / 60 if len(cs) <= 400 else None
    return {"original_folders": sorted(dirs, key=dirs.get, reverse=True)[:5], "devices": devs, "clips": len(cs), "minutes": round(mins) if mins is not None else None}

def cmd_list(a):
    rows = [(c, p) for c, p in batches(a.drive, a.client)]
    for c, p in rows:
        n = note_path(p); state = "stub" if is_stub(n) else ("done" if os.path.exists(n) else "missing")
        if a.all or state != "done": print(f"{state:8s} {c}/{os.path.basename(p)}  ({len(clips(p))} clips)")
    print(f"{sum(1 for c, p in rows if is_stub(note_path(p)))} stub(s) of {len(rows)} batches")

def cmd_show(a):
    b = os.path.abspath(a.batch); n = note_path(b); text = open(n, encoding="utf-8").read() if os.path.exists(n) else ""
    h = hints(b)
    print(f"batch: {os.path.basename(b)}   clips: {h['clips']}" + (f"   minutes: {h['minutes']}" if h["minutes"] is not None else ""))
    print("original folders:", "; ".join(h["original_folders"]) or "-"); print("audio devices:", h["devices"] or "none")
    for lab in ("Shoot date(s)", "Location", "Language(s)", "Audio sources"): print(f"  {lab}: {field(text, lab) or '(empty)'}")
    ov = re.search(r"## Overview\s*\n(.*?)(?=\n## |\Z)", text, re.S); print("  Overview:", (ov.group(1).strip().replace("\n", " ")[:300] if ov else "(none)"))
    kp = re.search(r"## Key people\s*\n(.*?)(?=\n## |\Z)", text, re.S); print("  Key people:", (kp.group(1).strip().replace("\n", " ")[:200] if kp else "(none)"))
    print("  status:", "stub" if is_stub(n) else "confirmed")
    if not a.no_preview:
        p = preview(b); print("preview:", p or "none")

def cmd_write(a):
    b = os.path.abspath(a.batch); n = note_path(b); text = open(n, encoding="utf-8").read()
    for lab, val in (("Shoot date(s)", a.date), ("Location", a.location), ("Language(s)", a.languages), ("Audio sources", a.audio)): text = set_field(text, lab, val)
    if a.overview:
        m = re.search(r"## Overview\s*\n(.*?)(?=\n## |\Z)", text, re.S); first = ""
        if m:
            fl = m.group(1).strip().splitlines(); first = fl[0] if fl and re.match(r"^\d+ clip", fl[0]) else ""
        text = set_section(text, "Overview", (first + "\n\n" if first else "") + a.overview.strip())
    if a.people: text = set_section(text, "Key people", bullets(a.people))
    if a.notes: text = set_section(text, "Notes", a.notes)
    text = re.sub(r"<!--.*?-->\n*", "", text, flags=re.S) if a.confirm else text
    if a.confirm: text = text.replace("**Status:** stub\n", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    open(n, "w", encoding="utf-8").write(text); print(f"wrote {n}" + (" (confirmed)" if a.confirm else ""))

def client_note(drive, client): return os.path.join(drive, "media", client, "client_note.md")

def cmd_show_client(a):
    p = client_note(a.drive, a.client); text = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    bs = [os.path.basename(b) for c, b in batches(a.drive, a.client)]
    print(f"client: {a.client}   batches: {len(bs)}"); print("  ", ", ".join(bs[:12]) + (" ..." if len(bs) > 12 else ""))
    for lab in ("Owner", "Location(s)", "Language(s)"): print(f"  {lab}: {field(text, lab) or '(empty)'}")
    for h in ("What this work is", "Key people", "Recurring locations"):
        m = re.search(r"## " + re.escape(h) + r"\s*\n(.*?)(?=\n## |\Z)", text, re.S); print(f"  {h}:", (m.group(1).strip().replace(chr(10), ' ')[:400] if m else "(none)"))
    print("  status:", "stub" if is_stub(p) else "confirmed")

def cmd_write_client(a):
    p = client_note(a.drive, a.client); text = open(p, encoding="utf-8").read()
    for lab, val in (("Owner", a.owner), ("Location(s)", a.locations), ("Language(s)", a.languages)): text = set_field(text, lab, val)
    if a.about: text = set_section(text, "What this work is", a.about)
    if a.people: text = set_section(text, "Key people", bullets(a.people))
    if a.recurring: text = set_section(text, "Recurring locations", bullets(a.recurring))
    if a.confirm: text = re.sub(r"<!--.*?-->\n*", "", text, flags=re.S).replace("**Status:** stub\n", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    open(p, "w", encoding="utf-8").write(text); print(f"wrote {p}" + (" (confirmed)" if a.confirm else ""))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sp = ap.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("list"); x.add_argument("drive"); x.add_argument("--client"); x.add_argument("--all", action="store_true"); x.set_defaults(f=cmd_list)
    x = sp.add_parser("show"); x.add_argument("batch"); x.add_argument("--no-preview", action="store_true"); x.set_defaults(f=cmd_show)
    x = sp.add_parser("write"); x.add_argument("batch")
    for k in ("date", "location", "languages", "audio", "overview", "people", "notes"): x.add_argument("--" + k)
    x.add_argument("--confirm", action="store_true"); x.set_defaults(f=cmd_write)
    x = sp.add_parser("show-client"); x.add_argument("drive"); x.add_argument("client"); x.set_defaults(f=cmd_show_client)
    x = sp.add_parser("write-client"); x.add_argument("drive"); x.add_argument("client")
    for k in ("owner", "locations", "languages", "about", "people", "recurring"): x.add_argument("--" + k)
    x.add_argument("--confirm", action="store_true"); x.set_defaults(f=cmd_write_client)
    a = ap.parse_args(); a.f(a)

if __name__ == "__main__":
    main()
