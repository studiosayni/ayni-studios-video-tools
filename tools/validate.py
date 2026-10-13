#!/usr/bin/env python3
"""Check a media drive (or one client, or one batch) against the folder convention before describing.

Reports, never changes anything. Exit code 1 when there are errors, 0 when only warnings or clean.
  errors    would make the skills misbehave or waste money: bad batch names, unreadable or tiny videos, duplicate exports,
            loose recordings, a missing directors note
  warnings  worth a look: placeholder dates, future dates, audio/unsorted, clips filed in two batches, missing sources.json

usage: validate.py /Volumes/media-root [--client NAME] [--batch PATH] [--quick] [--json OUT]
"""
import argparse, collections, datetime, json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medialib as ml

def duration(path):
    try: return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60).stdout)
    except Exception: return None

def probe_ok(path):
    try: return subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60).stdout.strip() not in ("", "N/A")
    except Exception: return False

def check_batch(b, client, exception, quick, out, seen):
    name = os.path.basename(b); rel = lambda *x: os.path.join(client, name, *x)
    E = lambda msg: out["errors"].append(f"{rel()}: {msg}"); W = lambda msg: out["warnings"].append(f"{rel()}: {msg}")
    if not ml.BATCH.match(name): E("name does not match YY.MM.DD.HH-lowercase-words")
    else:
        yy, mm, dd, hh = (int(x) for x in name[:11].split("."))
        if mm == 0 or dd == 0: W("placeholder date; put the real **Shoot date(s):** in the directors note")
        else:
            try:
                if datetime.date(2000 + yy, mm, dd) > datetime.date.today(): E("date is in the future")
            except ValueError: E("impossible date")
        if hh > 23: E("hour above 23")
    names = ml.visible(os.listdir(b)); files = [n for n in names if os.path.isfile(os.path.join(b, n))]; dirs = [n for n in names if os.path.isdir(os.path.join(b, n))]
    vids = [n for n in files if os.path.splitext(n)[1].lower() in ml.VIDEO_EXT]
    if not vids: W("no videos")
    if "directors_note.md" not in files: E("no directors_note.md")
    elif "**Status:** stub" in open(os.path.join(b, "directors_note.md"), encoding="utf-8", errors="replace").read(): W("directors note is still a stub (fill it in, then delete the Status line)")
    if "sources.json" not in files: W("no sources.json (run tools/build_source_map.py)")
    for d in dirs:
        if d not in ml.BATCH_SUBDIRS and not exception: W(f"unexpected subfolder {d}/")
    missing = [d for d in ml.SKELETON if d not in dirs]
    if missing: W("missing standard subfolder(s): " + ", ".join(missing) + " (tools/skeleton.py adds them)")
    if "audio" in dirs:
        for dev in ml.visible(os.listdir(os.path.join(b, "audio"))):
            if os.path.isfile(os.path.join(b, "audio", dev)): E(f"recording loose in audio/: {dev} (needs a device folder)")
    stems = collections.Counter()
    for n in files:
        ext = os.path.splitext(n)[1].lower(); stem = os.path.splitext(n)[0]
        if ext in ml.AUDIO_EXT: E(f"loose recording {n} (move to audio/<device>/ or outputs/processed_audio/)")
        elif re.search(r"\.\d{4,6}\.\d{8,10}\.(m4v|aac)(\.md0)?$", n, re.I): E(f"Media Encoder temp file {n}")
        elif ext in ml.IMAGE_EXT: W(f"image at batch level: {n} (belongs in images/, or people/ for a reference photo)") if out["_img"].setdefault(rel(), 0) == 0 else None; out["_img"][rel()] += 1
        elif ext in ml.SUBTITLE_EXT: W(f"subtitle at batch level: {n} (belongs in transcripts/)")
        elif ext == ".json" and n not in ml.ALLOWED_AT_BATCH and not any(os.path.splitext(v)[0] == stem for v in vids): W(f"JSON without a video: {n}")
        if ext in ml.VIDEO_EXT: stems[stem] += 1
    for v in vids:
        p = os.path.join(b, v); stem = os.path.splitext(v)[0]; size = os.path.getsize(p)
        m = re.match(r"^(.*)_(\d)$", stem)
        if m and m.group(1) in stems:
            base = next(x for x in vids if os.path.splitext(x)[0] == m.group(1))
            if quick: W(f"possible duplicate export {v} (base {m.group(1)} is also here; run without --quick to compare durations)")
            elif abs((duration(p) or -1) - (duration(os.path.join(b, base)) or -2)) < 0.12: E(f"duplicate export {v} (same duration as {base})")
        if size < 10_000: E(f"{v} is {size} bytes: failed encode")
        elif not quick and not probe_ok(p): E(f"{v} is unreadable")
        seen[(v.lower(), size)].append(rel(v))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drive"); ap.add_argument("--client"); ap.add_argument("--batch"); ap.add_argument("--quick", action="store_true", help="skip the ffprobe readability check"); ap.add_argument("--json")
    a = ap.parse_args(); root = os.path.abspath(a.drive); media = os.path.join(root, "media")
    out = {"errors": [], "warnings": [], "_img": {}}; seen = collections.defaultdict(list); nb = 0
    if a.batch:
        b = os.path.abspath(a.batch); parts = b.split(os.sep); client = parts[parts.index("media") + 1] if "media" in parts else ""
        check_batch(b, client, client in ml.EXCEPTION_CLIENTS, a.quick, out, seen); nb = 1
    else:
        if not os.path.isdir(media): sys.exit(f"{media} not found")
        for top in ml.visible(os.listdir(root)):
            if top not in {"media", "stock", "music", "sfx", "gfx", "exports", "libraries", "_index", "_quarantine"}: out["warnings"].append(f"unexpected item at the drive root: {top}")
        for c in ml.clients(media):
            if a.client and c != a.client: continue
            exception = c in ml.EXCEPTION_CLIENTS; cdir = os.path.join(media, c)
            if not ml.CLIENT.match(c): out["errors"].append(f"{c}: client folder must be lowercase words joined by hyphens")
            if not os.path.exists(os.path.join(cdir, "client_note.md")): out["warnings"].append(f"{c}: no client_note.md (shared people, languages and locations)")
            for n in ml.visible(os.listdir(cdir)):
                p = os.path.join(cdir, n)
                if os.path.isdir(p) and not ml.DATED.match(n) and not n.startswith("_") and not exception: out["warnings"].append(f"{c}/{n}: folder is neither a dated batch nor a _library")
                if os.path.isfile(p) and n not in ("client_note.md", "sources.json") and not exception: out["warnings"].append(f"{c}/{n}: file loose in the client folder")
            for b in ml.batches(cdir): check_batch(b, c, exception, a.quick, out, seen); nb += 1
    for (n, size), where in seen.items():
        if len(where) > 1: out["warnings"].append(f"same clip name and size in {len(where)} batches: " + " | ".join(where))
    for k, n in out.pop("_img").items():
        if n > 1: out["warnings"].append(f"{k}: {n} images at batch level in total")
    print(f"{nb} batch(es) checked: {len(out['errors'])} error(s), {len(out['warnings'])} warning(s)\n")
    for kind in ("errors", "warnings"):
        if out[kind]: print(kind.upper())
        for m in out[kind][:400]: print("  ", m)
        if len(out[kind]) > 400: print(f"   ... {len(out[kind]) - 400} more (use --json for the full list)")
        print() if out[kind] else None
    if a.json: json.dump(out, open(a.json, "w"), indent=1)
    sys.exit(1 if out["errors"] else 0)

if __name__ == "__main__":
    main()
