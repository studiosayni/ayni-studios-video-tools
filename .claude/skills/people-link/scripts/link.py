#!/usr/bin/env python3
"""people-link: link the same person across the clips of a batch, then name them once.

After footage-describe, every clip has its own person_1, person_2 with a clothing-and-role descriptor and a reference
still. This groups those entries into batch persons (bp_01, bp_02, ...) using the descriptors and, for uncertain groups,
the stills, compared on clothing, headwear, carried items and context. Faces are never compared. Two entries from the
same clip are never the same person. When unsure it leaves entries unlinked: a wrong merge is worse than a missed one.

It writes people/people.json (the batch registry), stamps person_key into every clip JSON, links recordings' speakers,
builds people/_people_sheet.html for a person to review, and keeps media/<client>/people.json (canonical people across
batches) up to date. Re-runs only place entries that are not in the registry yet, so names and manual merges survive.

usage:
  link.py BATCH [BATCH ...]                       group and write everything
  link.py BATCH --name bp_02 "Alex"               name a batch person; propagates to every linked clip and recording
  link.py BATCH --merge bp_02 bp_05               bp_05's appearances join bp_02
  link.py BATCH --split bp_02 PIN22805:person_2   take one appearance out of a group (it becomes its own person)
  link.py BATCH --speaker bp_02 "speaker 3"       tie a recording speaker id to a batch person
  link.py BATCH --regroup                         throw away unnamed groups and group again (named ones are kept)
  link.py BATCH --sheet-only                      rebuild the HTML sheet, no model calls
"""
import argparse, base64, datetime, glob, html, io, json, os, re, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

MODEL = os.environ.get("PEOPLE_LINK_MODEL", "claude-opus-5-5"); EFFORT = os.environ.get("PEOPLE_LINK_EFFORT", "high")
PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0)}
CHUNK = 350

GROUP_SYSTEM = """You link the same person across the clips of one documentary shoot. Each entry is one person seen in one clip, with the
generic id the describer gave them, a descriptor of clothing, headwear, carried items and their role in the action, and the clip
they appear in. Clips come from the same shoot, so the same person wears the same clothes throughout.

Rules:
- Group entries only when the descriptors agree on specific, distinctive details (a particular garment, colour, accessory, role).
  Generic descriptors ("man in white kandura" at an Emirati event, "person in dark shirt") are not enough on their own.
- Two entries from the same clip are never the same person.
- Clip names are in shooting order; the same person usually appears in runs of consecutive clips.
- When existing groups are given, assign to them where the details match; otherwise start a new group or leave the entry unlinked.
- Confidence high: several distinctive details agree and nothing contradicts. medium: details agree but are few or partly generic.
  low: plausible but uncertain. Leave an entry unlinked rather than guess.
- Never reason from faces, skin, age or body shape; you have no images here, only the descriptors.
Return JSON matching the schema."""

GROUP_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "assignments": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
        "entry": {"type": "integer"}, "group": {"type": "string", "description": "an existing group key, or 'new:<n>' to open a new group shared by entries in this answer, or 'none'"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}, "required": ["entry", "group", "confidence"]}},
    "new_groups": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
        "group": {"type": "string"}, "summary_descriptor": {"type": "string", "description": "the stable details shared by the members"}}, "required": ["group", "summary_descriptor"]}}},
    "required": ["assignments", "new_groups"]}

CONFIRM_SYSTEM = """You check whether several stills from one documentary shoot show the same person. Each still is a whole frame; the
descriptor under it says which person in that frame to look at. Compare clothing, headwear, glasses, carried items, and the
setting. Do not compare faces, skin, age or body shape. Say which stills clearly show a different person from the majority
(different garment, different headwear) and should be taken out of the group. When a still is too small or unclear to judge,
keep it. Return JSON matching the schema."""

CONFIRM_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "remove": {"type": "array", "items": {"type": "integer"}, "description": "labels of stills that show a different person"},
    "verdict": {"type": "string", "enum": ["same", "mixed", "unclear"]}, "note": {"type": "string"}}, "required": ["remove", "verdict", "note"]}


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return None
def save(p, j):
    os.makedirs(os.path.dirname(p), exist_ok=True); json.dump(j, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
def cost(msg):
    i, o = PRICE.get(msg.model, (4.0, 20.0)); u = msg.usage
    return round(u.input_tokens / 1e6 * i + u.output_tokens / 1e6 * o, 4)
class Pending(Exception):
    """A Claude Code agent still has to answer a request file."""

class FileExchange:
    """Claude Code mode: instead of calling the API, write each request (system text, the text, any contact sheet as a JPEG,
    the JSON schema) to people/_link_work/ and read the agent's response file on the next run. Cost is $0."""
    def __init__(self, work): self.work = work; self.pending = []; os.makedirs(work, exist_ok=True)
    def exchange(self, system, content, out_schema, tag):
        import hashlib
        text = content if isinstance(content, str) else "\n".join(b["text"] for b in content if b.get("type") == "text")
        imgs = [b for b in content if b.get("type") == "image"] if isinstance(content, list) else []
        h = hashlib.sha1((system + text).encode("utf-8")).hexdigest()[:10]
        req = os.path.join(self.work, f"{tag}_{h}_request.json"); resp = os.path.join(self.work, f"{tag}_{h}_response.json")
        if os.path.exists(resp):
            try:
                r = json.load(open(resp, encoding="utf-8"))
                if isinstance(r, dict) and "result" in r and isinstance(r["result"], dict): r = r["result"]
                missing = set(out_schema.get("required", [])) - set(r)
                if missing: raise ValueError(f"missing keys {sorted(missing)}")
                return r
            except Exception as e:
                os.replace(resp, resp + ".bad"); print(f"  {os.path.basename(resp)} is not a valid response ({e}); moved aside, request stays pending", file=sys.stderr)
        image_files = []
        for i, b in enumerate(imgs, 1):
            p = os.path.join(self.work, f"{tag}_{h}_sheet{i}.jpg"); open(p, "wb").write(base64.b64decode(b["source"]["data"]))
            image_files.append({"caption": "Contact sheet of reference stills. Each tile is labelled [n] clip id: descriptor; compare clothing, headwear, carried items and setting only, never faces.", "path": p})
        json.dump({"task": tag, "system": system, "text": text, "images": image_files, "schema": out_schema, "write_response_to": resp}, open(req, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        if req not in self.pending: self.pending.append(req)
        return None

def call(client, system, content, out_schema, max_tokens=16000, tag="group"):
    if isinstance(client, FileExchange):
        r = client.exchange(system, content, out_schema, tag)
        if r is None: raise Pending(tag)
        return r, 0.0
    with client.messages.stream(model=MODEL, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": content}],
                                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": out_schema}}) as s:
        msg = s.get_final_message()
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if not text.strip(): raise RuntimeError(f"empty response ({msg.stop_reason})")
    return json.loads(text), cost(msg)

# ---------- batch data ----------
def clip_jsons(batch):
    out = {}
    for p in sorted(glob.glob(os.path.join(batch, "*.json"))):
        if os.path.basename(p) in ("sources.json", "sync_map.json"): continue
        j = load(p)
        if isinstance(j, dict) and j.get("file_name") and "people" in j: out[os.path.splitext(os.path.basename(p))[0]] = (p, schema.migrate_video(j))
    return out

def entries(clips):
    out = []
    for stem, (p, j) in clips.items():
        for q in j.get("people") or []:
            if not q.get("id"): continue
            shot = ((j.get("content") or {}).get("shot") or {}).get("shot_type")
            out.append({"ref": f"{stem}:{q['id']}", "clip": stem, "id": q["id"], "shot_type": shot, "descriptor": q.get("descriptor") or "", "on_camera": bool(q.get("on_camera")),
                        "voice_heard": bool(q.get("voice_heard")), "best_seen_seconds": q.get("best_seen_seconds"), "reference_still": q.get("reference_still"),
                        "canonical_name": q.get("canonical_name"), "person_key": q.get("person_key")})
    return out

def blank_registry(batch):
    return {"schema_version": "1.0", "batch": os.path.basename(batch), "client": schema.client_of(batch), "generated_at": None, "generated_by": None,
            "people": [], "unlinked": [], "manual": []}

def next_key(reg):
    n = max([int(m.group(1)) for p in reg["people"] for m in [re.match(r"bp_(\d+)$", p["person_key"])] if m], default=0) + 1
    return f"bp_{n:02d}"

def placed(reg):
    return {a["ref"] for p in reg["people"] for a in p["appearances"]} | set(reg.get("unlinked") or [])

def appearance(e):
    return {"ref": e["ref"], "clip": e["clip"], "id": e["id"], "descriptor": e["descriptor"], "shot_type": e.get("shot_type"), "best_seen_seconds": e.get("best_seen_seconds"), "reference_still": e.get("reference_still")}

# closer shots make better reference stills; a person in a wide shot is a speck
SHOT_RANK = {"extreme_close_up": 0, "close_up": 1, "medium_close": 2, "medium": 3, "mixed": 4, "medium_wide": 5, "wide": 6, "extreme_wide": 7}
def by_closeness(apps): return sorted(apps, key=lambda a: (a.get("reference_still") is None, SHOT_RANK.get(a.get("shot_type"), 4)))

# ---------- grouping ----------
def group(client, reg, new_entries, log):
    spent = 0.0
    for i in range(0, len(new_entries), CHUNK):
        chunk = new_entries[i:i + CHUNK]
        existing = [{"key": p["person_key"], "summary_descriptor": p["summary_descriptor"], "members": len(p["appearances"]), "name": p.get("canonical_name")} for p in reg["people"]]
        lines = ["Existing groups (assign to these when the details match):" if existing else "No existing groups yet."]
        lines += [f"  {g['key']}: {g['summary_descriptor']} ({g['members']} clips{', named ' + g['name'] if g['name'] else ''})" for g in existing]
        lines += ["", "Entries to place (index, clip, id, descriptor):"]
        lines += [f"  {n}. clip {e['clip']}, {e['id']}: {e['descriptor']}" + ("" if e["on_camera"] else " [voice only, never on camera]") for n, e in enumerate(chunk)]
        res, c = call(client, GROUP_SYSTEM, "\n".join(lines), GROUP_SCHEMA); spent += c
        newmap = {}
        for g in res.get("new_groups") or []: newmap[g["group"]] = {"summary": g["summary_descriptor"], "key": None}
        for a in res.get("assignments") or []:
            n = a.get("entry")
            if not isinstance(n, int) or not 0 <= n < len(chunk): continue
            e = chunk[n]; g = a.get("group") or "none"
            if g == "none": reg["unlinked"].append(e["ref"]); continue
            if g.startswith("new:"):
                if g not in newmap: newmap[g] = {"summary": e["descriptor"], "key": None}
                if newmap[g]["key"] is None:
                    newmap[g]["key"] = next_key(reg)
                    reg["people"].append({"person_key": newmap[g]["key"], "summary_descriptor": newmap[g]["summary"], "canonical_name": None, "confidence": a["confidence"],
                                          "reference_still": None, "appearances": [], "speakers": [], "clips": []})
                key = newmap[g]["key"]
            else: key = g
            person = next((p for p in reg["people"] if p["person_key"] == key), None)
            if person is None: reg["unlinked"].append(e["ref"]); continue
            if any(x["clip"] == e["clip"] for x in person["appearances"]):   # same clip, different id: cannot be the same person
                reg["unlinked"].append(e["ref"]); continue
            person["appearances"].append({**appearance(e), "confidence": a["confidence"]})
            if a["confidence"] == "low" and person["confidence"] != "low": person["confidence"] = "low"
            elif a["confidence"] == "medium" and person["confidence"] == "high": person["confidence"] = "medium"
        for e in chunk:
            if e["ref"] not in placed(reg): reg["unlinked"].append(e["ref"])
        log(f"  grouped {len(chunk)} entr{'y' if len(chunk) == 1 else 'ies'}: {len(reg['people'])} people so far, {len(reg['unlinked'])} unlinked, ${c:.3f}")
    return spent

# ---------- visual confirm ----------
def contact_sheet(batch, apps, max_tiles=8):
    from PIL import Image, ImageDraw
    tiles = []
    for n, a in enumerate(apps):
        if len(tiles) >= max_tiles: break
        p = os.path.join(batch, a["reference_still"] or "")
        if not a.get("reference_still") or not os.path.exists(p): continue
        try: im = Image.open(p).convert("RGB")
        except Exception: continue
        im.thumbnail((640, 480)); canvas = Image.new("RGB", (640, 520), (20, 20, 20)); canvas.paste(im, ((640 - im.width) // 2, 0))
        d = ImageDraw.Draw(canvas); d.rectangle([0, 480, 640, 520], fill=(0, 0, 0)); d.text((6, 484), f"[{n}] {a['clip']} {a['id']}: {a['descriptor'][:80]}", fill=(255, 255, 255))
        tiles.append((n, canvas))
    if len(tiles) < 2: return None, []
    cols = 2 if len(tiles) <= 4 else 3; rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 640, rows * 520), (0, 0, 0))
    for k, (n, t) in enumerate(tiles): sheet.paste(t, ((k % cols) * 640, (k // cols) * 520))
    buf = io.BytesIO(); sheet.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode(), [n for n, _ in tiles]

def confirm(client, batch, reg, which, log):
    spent = 0.0; pending = False
    for p in reg["people"]:
        if len(p["appearances"]) < 2 or p.get("canonical_name"): continue
        if which == "uncertain" and p["confidence"] == "high": continue
        b64, labels = contact_sheet(batch, p["appearances"])
        if not b64: continue
        content = [{"type": "text", "text": f"Group summary: {p['summary_descriptor']}. Stills labelled {labels}. Which labels show a different person?"},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}]
        try: res, c = call(client, CONFIRM_SYSTEM, content, CONFIRM_SCHEMA, 4000, tag=f"confirm_{p['person_key']}")
        except Pending: pending = True; continue
        except Exception as e: log(f"  confirm failed for {p['person_key']}: {type(e).__name__}"); continue
        spent += c; removed = [n for n in res.get("remove") or [] if n in labels]
        for n in sorted(removed, reverse=True):
            a = p["appearances"].pop(n); reg["unlinked"].append(a["ref"])
        if res.get("verdict") == "same" and p["confidence"] != "high": p["confidence"] = "medium" if p["confidence"] == "low" else p["confidence"]
        log(f"  {p['person_key']}: stills say {res.get('verdict')}{', removed ' + str(len(removed)) if removed else ''} (${c:.3f})")
    if pending: raise Pending("confirm")
    return spent

# ---------- finishing ----------
def finish(reg):
    for p in reg["people"]:
        p["clips"] = sorted({a["clip"] for a in p["appearances"]})
        best = next((a for a in by_closeness(p["appearances"]) if a.get("reference_still")), None)
        p["reference_still"] = best["reference_still"] if best else None
    reg["people"] = [p for p in reg["people"] if p["appearances"]]
    reg["unlinked"] = sorted(set(reg["unlinked"]) - {a["ref"] for p in reg["people"] for a in p["appearances"]})

def propagate(batch, reg, clips, log):
    key_of = {a["ref"]: p for p in reg["people"] for a in p["appearances"]}; n = 0
    for stem, (path, j) in clips.items():
        changed = False
        for q in j.get("people") or []:
            ref = f"{stem}:{q.get('id')}"; p = key_of.get(ref)
            new_key = p["person_key"] if p else None; new_name = (p.get("canonical_name") if p else None) or (q.get("canonical_name") if not p else p.get("canonical_name"))
            if q.get("person_key") != new_key or q.get("canonical_name") != new_name:
                q["person_key"] = new_key; q["canonical_name"] = new_name; changed = True
        if changed: save(path, j); n += 1
    # recordings: speaker ids tied to a batch person
    sp = {s: p for p in reg["people"] for s in p.get("speakers") or []}
    for rp in glob.glob(os.path.join(batch, "audio", "*", "*.json")):
        j = load(rp)
        if not isinstance(j, dict) or not isinstance(j.get("speaker"), dict): continue
        p = sp.get(j["speaker"].get("id"))
        if p and (j["speaker"].get("person_key") != p["person_key"] or (p.get("canonical_name") and j["speaker"].get("canonical_name") != p["canonical_name"])):
            j["speaker"]["person_key"] = p["person_key"]
            if p.get("canonical_name"): j["speaker"]["canonical_name"] = p["canonical_name"]
            save(rp, j); n += 1
    log(f"  propagated to {n} JSON(s)")

def client_registry(batch, reg, log):
    parts = os.path.abspath(batch).split(os.sep)
    if "media" not in parts: return
    cdir = os.sep.join(parts[:parts.index("media") + 2]); p = os.path.join(cdir, "people.json")
    cr = load(p) or {"schema_version": "1.0", "client": os.path.basename(cdir), "generated_at": None, "people": []}
    byname = {x["canonical_name"]: x for x in cr["people"]}
    for bp in reg["people"]:
        nm = bp.get("canonical_name")
        if not nm: continue
        x = byname.setdefault(nm, {"canonical_name": nm, "role": None, "batches": {}, "clip_count": 0})
        x["batches"][reg["batch"]] = sorted(set((x["batches"].get(reg["batch"]) or []) + [bp["person_key"]]))
    for x in byname.values(): x["clip_count"] = 0
    # recount from every batch registry of the client
    for rp in glob.glob(os.path.join(cdir, "**", "people", "people.json"), recursive=True):
        r = load(rp) or {}
        for bp in r.get("people") or []:
            if bp.get("canonical_name") in byname: byname[bp["canonical_name"]]["clip_count"] += len(bp.get("clips") or [])
    cr["people"] = sorted(byname.values(), key=lambda x: x["canonical_name"].lower()); cr["generated_at"] = now()
    if cr["people"]: save(p, cr); log(f"  client registry: {len(cr['people'])} named people in {p}")

def sheet(batch, reg):
    def esc(x): return html.escape(str(x or ""))
    def tile(a):
        img = f'<img src="../{esc(a["reference_still"])}" loading="lazy">' if a.get("reference_still") else '<div class="noimg">no still</div>'
        return f'<figure>{img}<figcaption><b>{esc(a["clip"])}</b> {esc(a["id"])}<br>{esc(a["descriptor"])}</figcaption></figure>'
    parts = [f"<!doctype html><meta charset=utf-8><title>People: {esc(reg['batch'])}</title><style>body{{font:14px system-ui;margin:20px;background:#111;color:#ddd}}"
             "h2{margin:28px 0 6px}figure{display:inline-block;width:300px;margin:6px;vertical-align:top}img{width:300px;background:#000}figcaption{font-size:12px;color:#aaa}"
             ".noimg{width:300px;height:170px;background:#222;display:flex;align-items:center;justify-content:center}code{background:#222;padding:2px 5px}.meta{color:#8a8}</style>",
             f"<h1>{esc(reg['batch'])}</h1><p>{len(reg['people'])} people linked, {len(reg['unlinked'])} entries unlinked. Generated {esc(reg.get('generated_at'))}.</p>",
             "<p>To name someone, run: <code>link.py &lt;batch&gt; --name bp_XX \"Name\"</code>. To fix a wrong group: <code>--split bp_XX CLIP:person_N</code> or <code>--merge bp_XX bp_YY</code>.</p>"]
    for p in reg["people"]:
        parts.append(f"<h2>{esc(p['person_key'])} {('&mdash; ' + esc(p['canonical_name'])) if p.get('canonical_name') else ''} <span class=meta>({len(p['appearances'])} clips, confidence {esc(p['confidence'])}"
                     f"{', speakers ' + esc(', '.join(p['speakers'])) if p.get('speakers') else ''})</span></h2><p>{esc(p['summary_descriptor'])}</p>")
        parts += [tile(a) for a in by_closeness(p["appearances"])]
    if reg["unlinked"]:
        parts.append("<h2>Unlinked</h2><p>Seen once, or too generic to link safely. Use --merge with a bp key after --split, or leave them.</p>")
        look = {a["ref"]: a for a in reg.get("_all", [])}
        parts += [tile(look[r]) if r in look else f"<figure><figcaption>{esc(r)}</figcaption></figure>" for r in reg["unlinked"]]
    p = os.path.join(batch, "people", "_people_sheet.html"); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write("\n".join(parts)); return p

# ---------- manual edits ----------
def find(reg, key):
    p = next((p for p in reg["people"] if p["person_key"] == key), None)
    if not p: sys.exit(f"no batch person {key}; keys: {[x['person_key'] for x in reg['people']]}")
    return p

def manual(reg, a, all_entries):
    if a.name:
        p = find(reg, a.name[0]); p["canonical_name"] = a.name[1].strip() or None; reg["manual"].append({"op": "name", "key": a.name[0], "value": p["canonical_name"], "at": now()})
    if a.merge:
        keep, gone = find(reg, a.merge[0]), find(reg, a.merge[1])
        for x in gone["appearances"]:
            if any(y["clip"] == x["clip"] for y in keep["appearances"]): reg["unlinked"].append(x["ref"])
            else: keep["appearances"].append(x)
        keep["speakers"] = sorted(set(keep.get("speakers") or []) | set(gone.get("speakers") or [])); keep["canonical_name"] = keep.get("canonical_name") or gone.get("canonical_name")
        reg["people"].remove(gone); reg["manual"].append({"op": "merge", "keep": keep["person_key"], "gone": gone["person_key"], "at": now()})
    if a.split:
        p = find(reg, a.split[0]); ref = a.split[1]
        x = next((x for x in p["appearances"] if x["ref"] == ref), None)
        if not x: sys.exit(f"{ref} is not in {p['person_key']}: {[y['ref'] for y in p['appearances']]}")
        p["appearances"].remove(x); k = next_key(reg)
        reg["people"].append({"person_key": k, "summary_descriptor": x["descriptor"], "canonical_name": None, "confidence": "high", "reference_still": x.get("reference_still"),
                              "appearances": [x], "speakers": [], "clips": [x["clip"]]})
        reg["manual"].append({"op": "split", "from": p["person_key"], "ref": ref, "new": k, "at": now()})
    if a.speaker:
        p = find(reg, a.speaker[0]); p["speakers"] = sorted(set(p.get("speakers") or []) | {a.speaker[1]}); reg["manual"].append({"op": "speaker", "key": a.speaker[0], "speaker": a.speaker[1], "at": now()})
    if a.link:
        p = find(reg, a.link[0]); e = next((e for e in all_entries if e["ref"] == a.link[1]), None)
        if not e: sys.exit(f"no entry {a.link[1]}")
        for q in reg["people"]: q["appearances"] = [x for x in q["appearances"] if x["ref"] != e["ref"]]
        p["appearances"].append({**appearance(e), "confidence": "high"}); reg["manual"].append({"op": "link", "key": a.link[0], "ref": e["ref"], "at": now()})

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batches", nargs="+"); ap.add_argument("--confirm", choices=["all", "uncertain", "none"], default="uncertain", help="check groups against the stills (default: groups not rated high)")
    ap.add_argument("--regroup", action="store_true", help="drop unnamed groups and group everything again"); ap.add_argument("--sheet-only", action="store_true")
    ap.add_argument("--name", nargs=2, metavar=("KEY", "NAME")); ap.add_argument("--merge", nargs=2, metavar=("KEEP", "GONE")); ap.add_argument("--split", nargs=2, metavar=("KEY", "CLIP:ID"))
    ap.add_argument("--speaker", nargs=2, metavar=("KEY", "SPEAKER_ID")); ap.add_argument("--link", nargs=2, metavar=("KEY", "CLIP:ID"), help="put an unlinked entry into a group")
    ap.add_argument("--claude-code", action="store_true", help="no API calls: write request files under people/_link_work for a Claude Code agent, apply its responses, re-run until done")
    a = ap.parse_args(); edit = any([a.name, a.merge, a.split, a.speaker, a.link]); total = 0.0; all_pending = []
    client = None
    for batch in a.batches:
        batch = os.path.abspath(batch); print(os.path.basename(batch))
        clips = clip_jsons(batch)
        if not clips: print("  no clip JSONs here yet (run footage-describe first)"); continue
        all_entries = entries(clips); rp = os.path.join(batch, "people", "people.json")
        reg = load(rp) or blank_registry(batch); reg.setdefault("manual", []); reg.setdefault("unlinked", [])
        if a.regroup:
            reg["people"] = [p for p in reg["people"] if p.get("canonical_name")]; reg["unlinked"] = []
        if edit: manual(reg, a, all_entries)
        elif not a.sheet_only:
            have = placed(reg); todo = [e for e in all_entries if e["ref"] not in have]
            if todo:
                if a.claude_code: client = FileExchange(os.path.join(batch, "people", "_link_work"))
                elif client is None:
                    import anthropic; client = anthropic.Anthropic()
                print(f"  {len(todo)} entr{'y' if len(todo) == 1 else 'ies'} to place across {len(clips)} clips")
                try:
                    total += group(client, reg, todo, print)
                    if a.confirm != "none": total += confirm(client, batch, reg, a.confirm, print)
                except Pending:
                    all_pending += client.pending; print(f"  {len(client.pending)} request(s) pending for a Claude Code agent; registry not written yet"); continue
                if a.claude_code: shutil.rmtree(os.path.join(batch, "people", "_link_work"), ignore_errors=True)
            else: print("  nothing new to place")
        finish(reg); reg["generated_at"] = now(); reg["generated_by"] = {"skill": "people-link", "model": ("claude-code" if a.claude_code else MODEL) if not (edit or a.sheet_only) else None, "effort": None if a.claude_code else EFFORT}
        reg["_all"] = all_entries; sp = sheet(batch, reg); reg.pop("_all", None)
        save(rp, reg); propagate(batch, reg, clips, print); client_registry(batch, reg, print)
        print(f"  {len(reg['people'])} people, {len(reg['unlinked'])} unlinked; registry {rp}; sheet {sp}")
    if total: print(f"model cost ${total:.3f} at list prices")
    if all_pending:
        print("\npending requests (have a Claude Code agent answer each with scripts/claude_code_prompt.md, then run again):")
        for p in all_pending: print(f"  {p}")

if __name__ == "__main__":
    main()
