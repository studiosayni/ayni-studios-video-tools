#!/usr/bin/env python3
"""asset-describe: write the library JSON for music, sound effects and graphics.

TARGET is a file or a folder (recursive) under libraries/. For every asset without a JSON, or whose file changed since,
this writes <asset>.json next to it from the matching template:
  scripted layer  ffprobe, loudness, embedded tags, sha256, bpm and key (librosa)      no model
  listening layer Gemini, music and sfx only: genre, mood, pacing, energy, structure   GEMINI_API_KEY
  seeing layer    Claude, graphics only: type, colours, text, background safety        ant auth login or ANTHROPIC_API_KEY
Existing JSONs are merged: rights, review, usage and anything a person wrote survive. Rights are never filled by a model.
Also rebuilds index.json in each library root and writes a run log under outputs/asset_runs/.
"""
import argparse, datetime, glob, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe, models  # noqa: E402

TEMPLATES = os.path.join(os.path.dirname(HERE), "templates")
# library folder name -> asset type. Short names are the drive's top-level folders; _names are client-level libraries under media/<client>/.
LIBRARY_TYPES = {"music_library": "music", "music": "music", "_music": "music", "sfx": "sfx", "_sfx": "sfx", "gfx": "gfx", "_gfx": "gfx",
                 "stock_footage": None, "stock": None, "_stock": None}
SKIP_DIRS = {"outputs", "_describe_work", ".git", "__pycache__"}


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def template(asset_type):
    return json.load(open(os.path.join(TEMPLATES, f"{asset_type}_metadata_template.json")))


def library_of(path):
    """Walk up until a folder named like a library. Returns (library name, library root) or (None, None)."""
    p = os.path.abspath(path)
    while True:
        name = os.path.basename(p)
        if name in LIBRARY_TYPES: return name, p
        parent = os.path.dirname(p)
        if parent == p: return None, None
        p = parent


def asset_type_for(path, forced=None):
    if forced: return forced
    lib, _ = library_of(path); kind = probe.kind_of(path)
    t = LIBRARY_TYPES.get(lib)
    if t in ("music", "sfx"): return t if kind == "audio" else None
    if t == "gfx": return "gfx" if kind in ("image", "motion") else None
    if kind == "audio": return None  # ambiguous outside a library: ask for --type
    if kind in ("image", "motion"): return "gfx"
    return None


def collect(target):
    if os.path.isfile(target): return [target]
    out = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in sorted(files):
            if f.startswith("."): continue
            if probe.kind_of(os.path.join(root, f)): out.append(os.path.join(root, f))
    return out


def merge_existing(new, old):
    """Keep every human-owned block and any field a person filled that the new run left empty."""
    if not old: return new
    for block in ("rights", "review", "usage"):
        if isinstance(old.get(block), dict): new[block] = old[block]
    if old.get("notes"): new["notes"] = old["notes"]
    tb = new["asset_type"]
    for k, v in (old.get(tb) or {}).items():
        if k in new[tb] and (new[tb][k] in (None, "", [], False)) and v not in (None, "", []):
            new[tb][k] = v
    if old.get("tags") and not new.get("tags"): new["tags"] = old["tags"]
    return new


def scripted(path, asset_type, lib_root, skip_analysis=False):
    doc = template(asset_type)
    lib, _ = library_of(path)
    doc.update(file_name=os.path.basename(path), library=lib or doc["library"],
               path=os.path.relpath(path, os.path.dirname(lib_root)) if lib_root else os.path.basename(path),
               file_size_bytes=os.path.getsize(path), content_hash=probe.sha256(path), generated_at=now())
    doc["generated_by"] = {"skill": "asset-describe", "model": None, "effort": None}
    tags = {}
    if asset_type in ("music", "sfx"):
        doc["technical"], tags = probe.audio_technical(path)
        if asset_type == "music":
            m = doc["music"]
            m["title"] = tags.get("title", "") or os.path.splitext(os.path.basename(path))[0]
            m["artist"] = tags.get("artist", "") or tags.get("album_artist", "")
            m["album"] = tags.get("album") or None
            if tags.get("bpm"):
                try: m["bpm"] = float(tags["bpm"])
                except ValueError: pass
            if not skip_analysis:
                bpm, key, conf = probe.bpm_and_key(path)
                if bpm and not m["bpm"]: m["bpm"] = bpm
                if key: m["key"] = key
        else:
            doc["sfx"]["description"] = tags.get("comment", "") or tags.get("description", "") or ""
    else:
        doc["technical"] = probe.image_technical(path) if probe.kind_of(path) == "image" else probe.motion_technical(path)
        doc["gfx"]["title"] = os.path.splitext(os.path.basename(path))[0]
    if tags.get("copyright"): doc["rights"]["notes"] = f"embedded copyright tag: {tags['copyright']}"
    return doc, tags


def apply_listen(doc, result):
    tb = doc["asset_type"]; block = doc[tb]
    for k, v in result.items():
        if k in ("tags", "bpm_estimate", "key_estimate"): continue
        if k in block: block[k] = v
    if tb == "music":
        if block.get("bpm") is None and result.get("bpm_estimate"): block["bpm"] = result["bpm_estimate"]
        if not block.get("key") and result.get("key_estimate"): block["key"] = result["key_estimate"]
    doc["tags"] = sorted({t.strip().lower() for t in (result.get("tags") or []) if t.strip()})
    return doc


def apply_see(doc, result):
    g = doc["gfx"]
    for k in ("type", "title", "description", "colors", "safe_for_dark_background", "safe_for_light_background"):
        if k in result: g[k] = result[k]
    g["text_fields"] = result.get("text_content") or []
    doc["tags"] = sorted({t.strip().lower() for t in (result.get("tags") or []) if t.strip()})
    return doc


def rebuild_index(lib_root):
    rows = []
    for j in sorted(glob.glob(os.path.join(lib_root, "**", "*.json"), recursive=True)):
        if os.path.basename(j) == "index.json" or "/outputs/" in j: continue
        try: d = json.load(open(j))
        except Exception: continue
        if not d.get("asset_type"): continue
        tb = d.get(d["asset_type"], {}) or {}
        rows.append({"file_name": d.get("file_name"), "path": d.get("path"), "asset_type": d["asset_type"],
                     "duration_seconds": (d.get("technical") or {}).get("duration_seconds"),
                     "bpm": tb.get("bpm"), "key": tb.get("key"), "mood": tb.get("mood"), "genre": tb.get("genre"), "pacing": tb.get("pacing"), "energy": tb.get("energy"),
                     "category": tb.get("category"), "kind": tb.get("kind"), "type": tb.get("type"),
                     "description": tb.get("description"), "tags": d.get("tags"),
                     "license_type": (d.get("rights") or {}).get("license_type"), "allowed_clients": (d.get("rights") or {}).get("allowed_clients"),
                     "expires_at": (d.get("rights") or {}).get("expires_at"), "rating": (d.get("review") or {}).get("rating")})
    out = {"schema_version": "1.0", "library": os.path.basename(lib_root), "generated_at": now(), "count": len(rows), "assets": rows}
    json.dump(out, open(os.path.join(lib_root, "index.json"), "w"), indent=2, ensure_ascii=False)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="+")
    ap.add_argument("--type", choices=["music", "sfx", "gfx"], help="force the asset type (needed for audio outside a library folder)")
    ap.add_argument("--no-listen", action="store_true", help="skip the Gemini layer")
    ap.add_argument("--no-see", action="store_true", help="skip the Claude layer")
    ap.add_argument("--no-analysis", action="store_true", help="skip bpm and key detection")
    ap.add_argument("--force", action="store_true", help="rewrite even when the JSON exists and the file is unchanged")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, no writes")
    ap.add_argument("--gemini-model", default=models.GEMINI_DEFAULT)
    ap.add_argument("--claude-model", default=models.CLAUDE_DEFAULT)
    ap.add_argument("--claude-effort", default=models.CLAUDE_EFFORT)
    ap.add_argument("--no-index", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=15, help="skip the Gemini layer for audio longer than this (field recordings are not library assets); 0 = no limit")
    a = ap.parse_args()

    files = [f for t in a.target for f in collect(t)]
    if not files: raise SystemExit("no audio, image or motion files found")
    plan = []
    for f in files:
        at = asset_type_for(f, a.type)
        if not at:
            print(f"skip (unknown type, use --type): {f}", file=sys.stderr); continue
        jpath = os.path.splitext(f)[0] + ".json"
        old = json.load(open(jpath)) if os.path.exists(jpath) else None
        if old and not a.force:
            if old.get("content_hash") == probe.sha256(f) and old.get("generated_by", {}).get("model"):
                print(f"skip (up to date): {os.path.basename(f)}"); continue
        plan.append((f, at, jpath, old))
    print(f"{len(plan)} asset(s) to describe")
    if a.dry_run:
        for f, at, _, old in plan: print(f"  {at:5s} {'update' if old else 'new   '} {f}")
        return

    gclient = None if a.no_listen else models.gemini_client()
    gmodel = None
    if not a.no_listen and any(at in ("music", "sfx") for _, at, _, _ in plan):
        if gclient is None:
            print("GEMINI_API_KEY not set: listening fields (genre, mood, pacing, sfx description) will stay empty. Export the key and re-run to fill them.", file=sys.stderr)
        else:
            gmodel = models.resolve_gemini_model(gclient, a.gemini_model)
    cclient = None if a.no_see else (models.anthropic_client() if any(at == "gfx" for _, at, _, _ in plan) else None)

    run = {"started": now(), "gemini_model": gmodel, "claude_model": a.claude_model if cclient else None, "items": [], "total_cost_usd": 0.0}
    work = tempfile.mkdtemp(prefix="asset-describe-")
    lib_roots = set()
    for f, at, jpath, old in plan:
        lib, lib_root = library_of(f)
        if lib_root: lib_roots.add(lib_root)
        print(f"\n{at}: {os.path.basename(f)}")
        doc, _ = scripted(f, at, lib_root, a.no_analysis)
        item = {"file": f, "asset_type": at, "cost_usd": 0.0}
        try:
            dur = (doc.get("technical") or {}).get("duration_seconds") or 0
            if at in ("music", "sfx") and gclient is not None and a.max_minutes and dur > a.max_minutes * 60:
                print(f"  {dur/60:.0f} min long, over --max-minutes {a.max_minutes:g}: listening layer skipped (is this a field recording rather than a library asset?)", file=sys.stderr)
                item["skipped"] = "too long"
            elif at in ("music", "sfx") and gclient is not None:
                schema = models.MUSIC_SCHEMA if at == "music" else models.SFX_SCHEMA
                prompt = models.MUSIC_PROMPT if at == "music" else models.SFX_PROMPT
                res, usage, wall, cost = models.gemini_listen(gclient, gmodel, f, schema, prompt)
                doc = apply_listen(doc, res)
                doc["generated_by"].update(model=gmodel, effort=None, fields="listening")
                item.update(usage=usage, wall_seconds=round(wall, 1), cost_usd=cost)
                print(f"  gemini {wall:.0f}s ${cost:.4f}: {', '.join(doc[at].get('mood') or []) or doc[at].get('category') or ''}")
            elif at == "gfx" and cclient is not None:
                if probe.kind_of(f) == "image":
                    ext = os.path.splitext(f)[1].lower(); imgs = []
                    from PIL import Image
                    try:
                        im = Image.open(f).convert("RGBA")
                        if im.getchannel("A").getextrema()[0] < 255:
                            # transparent graphics are shown on mid grey so white and black elements both stay visible
                            bg = Image.new("RGBA", im.size, (128, 128, 128, 255)); bg.alpha_composite(im); im = bg
                            label = "The graphic, composited on neutral grey (the grey is not part of the asset):"
                        else: label = "The graphic:"
                        p = os.path.join(work, os.path.basename(f) + ".png"); im.convert("RGB").save(p); imgs = [(p, label)]
                    except Exception as e: print(f"  cannot rasterise {ext} ({type(e).__name__}); scripted fields only", file=sys.stderr)
                    hint = "still graphic"
                else:
                    imgs = [(p, f"Frame at {t}s:") for p, t in probe.frames_for_motion(f, os.path.join(work, os.path.basename(f)))]; hint = "motion graphic, several frames shown"
                if imgs:
                    res, usage, wall, cost = models.claude_see(cclient, a.claude_model, a.claude_effort, imgs, os.path.basename(f), hint)
                    doc = apply_see(doc, res)
                    doc["generated_by"].update(model=a.claude_model, effort=a.claude_effort, fields="seeing")
                    item.update(usage=usage, wall_seconds=round(wall, 1), cost_usd=cost)
                    print(f"  claude {wall:.0f}s ${cost:.4f}: {doc['gfx']['type']}, {doc['gfx']['description'][:80]}")
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {str(e)[:200]}"; print(f"  model layer failed: {item['error']}", file=sys.stderr)
        doc = merge_existing(doc, old)
        json.dump(doc, open(jpath, "w"), indent=2, ensure_ascii=False)
        run["items"].append(item); run["total_cost_usd"] = round(run["total_cost_usd"] + item["cost_usd"], 5)
        print(f"  wrote {os.path.basename(jpath)}")
    shutil.rmtree(work, ignore_errors=True)

    if not a.no_index:
        for r in sorted(lib_roots): print(f"index: {os.path.basename(r)} {rebuild_index(r)} assets")
    run["finished"] = now()
    common = os.path.commonpath([os.path.dirname(r) for r in lib_roots]) if lib_roots else os.path.dirname(plan[0][0])
    log_root = os.path.join(common, "outputs", "asset_runs")
    os.makedirs(log_root, exist_ok=True)
    log = os.path.join(log_root, f"{run['started'].replace(':', '').replace('+0000', 'Z')}.json")
    json.dump(run, open(log, "w"), indent=2)
    missing = [os.path.basename(f) for f, at, jpath, _ in plan if not json.load(open(jpath)).get("rights", {}).get("license_type")]
    print(f"\ndone: {len(plan)} asset(s), ${run['total_cost_usd']:.4f}, log {log}")
    if missing: print(f"rights still empty on {len(missing)} asset(s): {', '.join(missing[:8])}{' ...' if len(missing) > 8 else ''}")


if __name__ == "__main__":
    main()
