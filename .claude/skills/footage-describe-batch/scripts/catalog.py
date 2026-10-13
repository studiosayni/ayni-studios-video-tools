#!/usr/bin/env python3
"""footage-describe: write the media-library JSON for one clip or a whole batch folder, synchronously.

usage: catalog.py TARGET [TARGET ...] [--out DIR] [--note PATH] [--batch-name NAME] [--no-verify]
                  [--transcribe auto|off] [--language es] [--primary DEVICE] [--include-weak-sync] [--dry-run]
                  [--effort xhigh] [--verify-effort xhigh] [--model claude-opus-5-5] [--no-fallbacks] [--keep-work] [--force]

TARGET is a video file or a batch folder (every video in it, not recursive). The JSON lands next to the video as
<clip>.json unless --out is given. The directors note is <folder>/directors_note.md unless --note. If footage-sync has
written sync_map.json in the folder, the transcript comes from the main audio source named in the note (or --primary)
and the sync fields are filled. Existing JSONs are merged, not rewritten.
"""
import argparse, datetime, json, os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__)); SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline as pl, transcribe as tr, schema

SKIP_DIRS = {"audio", "outputs", "_describe_work"}

def batch_dirs(folder):
    """Dated batch folders under a client folder (or the whole media/ tree). Skips _library folders, audio/ and outputs/.
    A nested media/ folder is followed."""
    import re
    out = []
    for d in sorted(os.listdir(folder)):
        p = os.path.join(folder, d)
        if not os.path.isdir(p) or d.startswith((".", "_")) or d in SKIP_DIRS: continue
        if re.match(r"^\d\d\.\d\d\.\d\d", d): out.append(p)
        elif d == "media" or os.path.basename(folder) == "media": out += batch_dirs(p)
    return out

AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac")

def unsynced_batches(clips):
    """Batch folders that hold recordings under audio/ but no sync_map.json yet."""
    out = []
    for folder in sorted({os.path.dirname(os.path.abspath(c)) for c in clips}):
        if os.path.exists(os.path.join(folder, "sync_map.json")): continue
        recs = [f for r, _, fs in os.walk(os.path.join(folder, "audio")) if "outputs" not in r.split(os.sep)
                for f in fs if f.lower().endswith(AUDIO_EXT) and not f.startswith("._")]
        if recs: out.append((folder, len(recs)))
    return out

def require_sync(clips, skip=False):
    # Recorders carry the dialogue. Describing before footage-sync leaves every transcript on the camera mic, so refuse.
    missing = unsynced_batches(clips)
    if not missing or skip: return
    for folder, n in missing: print(f"{folder}: {n} recording(s) under audio/ but no sync_map.json", file=sys.stderr)
    sys.exit("run footage-sync on these batches first so the recorders are matched and transcribed:\n"
             "  .claude/skills/footage-sync/venv/bin/python .claude/skills/footage-sync/scripts/sync.py <batch>\n"
             "--no-sync-check skips this; use it only when the recordings are not meant for these clips")

def collect(targets):
    clips = []
    for t in targets:
        t = os.path.abspath(os.path.expanduser(t))
        if os.path.isdir(t):
            here = sorted(os.path.join(t, f) for f in os.listdir(t) if pl.is_video(f) and not f.startswith("."))
            if here: clips += here
            else:
                subs = batch_dirs(t)
                if subs: print(f"{os.path.basename(t)}: {len(subs)} batch folder(s)", file=sys.stderr)
                for b in subs: clips += sorted(os.path.join(b, f) for f in os.listdir(b) if pl.is_video(f) and not f.startswith("."))
        elif os.path.isfile(t) and pl.is_video(t): clips.append(t)
        else: print(f"skipping {t}: not a video file or folder", file=sys.stderr)
    return clips

def read_note(path):
    try: return open(path, encoding="utf-8").read()
    except Exception: return ""

def load_json(path):
    try: return json.load(open(path, encoding="utf-8"))
    except Exception: return None

def load_sync(folder):
    return load_json(os.path.join(folder, "sync_map.json"))

def role_of(device, roles):
    if device == roles.get("main"): return "main"
    for r, devs in roles.items():
        if r != "main" and isinstance(devs, list) and device in devs: return r
    return "unassigned"

def resolve_sync(smap, stem, folder, roles, primary=None, include_weak=False):
    """Turn the sync map entry for a clip into JSON fields and a transcript plan.
    Returns None when there is no map; otherwise {"fields", "matches", "main", "note"}."""
    if not smap: return None
    e = (smap.get("clips") or {}).get(stem)
    if not e: return None
    ok_status = ["confident", "weak"] if include_weak else ["confident"]
    matches = [m for m in e.get("matches", []) if m.get("status") in ok_status]
    for m in matches: m["path"] = os.path.join(folder, m["recording"]) if os.path.exists(os.path.join(folder, m["recording"])) else None
    main = tr.norm_device(primary) if primary else (roles.get("main") or "camera")
    main_match = next((m for m in matches if m["device"] == main), None)
    best = main_match or (max(matches, key=lambda m: m["ncc"]) if matches else None)
    this_off = {m["recording"]: m["offset_seconds"] for m in matches}
    fields = {"has_external_audio": e.get("has_external_audio"), "sync_status": e.get("sync_status", "not_run"),
              "sync_relationships": {"primary_audio": {"file": best["recording"] if best else None, "device": best["device"] if best else None, "offset_seconds": best["offset_seconds"] if best else None},
                                     "linked_camera_angles": e.get("linked", []), "visual_sync_group": e.get("group"),
                                     "audio_sources": [{"file": m["recording"], "device": m["device"], "role": role_of(m["device"], roles), "offset_seconds": m["offset_seconds"], "confidence": m["ncc"], "drift_seconds": m.get("drift_seconds"), "method": "correlation"} for m in matches],
                                     "audio_roles": {k: v for k, v in roles.items()}, "method": "correlation" if matches else None, "synced_at": smap.get("generated")}}
    note = None
    if matches: note = "Synced audio for this clip (offsets are positions in each recording where the video starts): " + "; ".join(f"{m['recording']} ({m['device']}, {role_of(m['device'], roles)}) at {m['offset_seconds']:.3f} s" for m in matches) + f". Main audio source for this batch: {main}. The transcript below comes from the source named in its label and its timestamps are already in video time."
    return {"fields": fields, "matches": matches, "main": main, "note": note}

def _words(runs):
    n = 0
    for _, text in runs:
        for seg in tr.parse_srt(text): n += len(seg[2].split())
    return n

def plan_transcripts(clip, stats, duration, sync, roles, a, note_text, log, thin_words=8):
    """Transcribe the main source. When it is thin or empty, add every other available source, labelled by device and role,
    and let the model choose. Returns (runs, planned_source, provided_sources)."""
    expected = tr.expected_languages(note_text)
    if a.transcribe == "off": return [], None, []
    main = (tr.norm_device(a.primary) if a.primary else None) or (sync["main"] if sync else None) or roles.get("main") or "camera"
    matches = sync["matches"] if sync else []
    order = [main] + [d for d in (["camera"] + [m["device"] for m in matches]) if d != main]
    def runs_for(src):
        if src == "camera":
            r = tr.ensure(clip, a.transcribe, SKILL_DIR, stats, log=log, language=a.language, expected=expected)
        else:
            m = next((m for m in matches if m["device"] == src and m.get("path")), None)
            if not m: return []
            r = tr.external_for_clip(m["path"], m["offset_seconds"], duration, SKILL_DIR, log=log, language=a.language, expected=expected)
        r = [x for x in r if x[1].strip() and "(no speech in this clip's span)" not in x[1]]
        return [(f"{src} ({role_of(src, roles)}): {lab}", text) for lab, text in r]
    provided = []; runs = []
    for src in dict.fromkeys(order):
        r = runs_for(src)
        if r: runs += r; provided.append(src)
        if src == main and provided and provided[0] == main and _words(runs) >= thin_words: break  # main is solid, stop here
        if len(provided) >= 3: break
    planned = provided[0] if provided else None
    if planned and planned != main: log(f"  main source {main} had no usable speech; {planned} used instead")
    elif planned and len(provided) > 1: log(f"  main source {main} is thin; also providing {', '.join(provided[1:])}")
    return runs, planned, provided

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("targets", nargs="+")
    ap.add_argument("--out"); ap.add_argument("--note"); ap.add_argument("--batch-name")
    ap.add_argument("--no-verify", action="store_true"); ap.add_argument("--transcribe", choices=["auto", "off"], default="auto"); ap.add_argument("--language")
    ap.add_argument("--primary", help="main audio source for this run: camera or a device folder name under audio/ (overrides the note)")
    ap.add_argument("--include-weak-sync", action="store_true", help="also use weak sync matches from sync_map.json")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--effort", default="xhigh"); ap.add_argument("--verify-effort", default="xhigh")
    ap.add_argument("--model", default="claude-opus-5-5"); ap.add_argument("--no-fallbacks", action="store_true")
    ap.add_argument("--keep-work", action="store_true"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-sync-check", action="store_true", help="describe even though audio/ holds recordings and sync_map.json is missing")
    ap.add_argument("--camera-id", help="camera name for multi-camera shoots (camA, camB), stored in camera_metadata.camera_id")
    a = ap.parse_args()
    clips = collect(a.targets)
    if clips: require_sync(clips, a.no_sync_check)
    if not clips: sys.exit("no video files found")
    client = None
    if not a.dry_run:
        import anthropic; client = anthropic.Anthropic()
    total = 0.0; done = 0
    for clip in clips:
        folder = os.path.dirname(clip); stem = os.path.splitext(os.path.basename(clip))[0]
        batch = a.batch_name or os.path.basename(folder)
        out_dir = os.path.abspath(a.out) if a.out else folder; os.makedirs(out_dir, exist_ok=True)
        final = os.path.join(out_dir, f"{stem}.json"); existing = load_json(final)
        if existing and not a.force: print(f"[{stem}] exists, skipping (use --force to redo)"); continue
        note_path = a.note or os.path.join(folder, "directors_note.md"); note_text = schema.with_client_note(read_note(note_path), folder)
        if not note_text: print(f"[{stem}] no directors note at {note_path}; continuing with the folder date only", file=sys.stderr)
        roles = tr.default_main(tr.audio_sources(note_text), folder)
        work = os.path.join(out_dir, "outputs", "_describe_work", stem); runs_dir = os.path.join(out_dir, "outputs", "describe_runs"); os.makedirs(runs_dir, exist_ok=True)
        print(f"[{stem}] preparing")
        stats = pl.audio_stats(clip); duration = pl.probe(clip)["duration"]
        sync = resolve_sync(load_sync(folder), stem, folder, roles, a.primary, a.include_weak_sync)
        transcripts, source, provided = plan_transcripts(clip, stats, duration, sync, roles, a, note_text, print)
        sync_arg = {"fields": sync["fields"], "note": sync["note"], "transcript_source": source} if sync else {"fields": None, "note": None, "transcript_source": source}
        prep = pl.prepare(clip, batch, note_text, transcripts, work, sync_arg)
        if a.camera_id: prep["out"]["camera_metadata"]["camera_id"] = a.camera_id
        m = prep["manifest"]
        print(f"[{stem}] {m['images']} images, ~{m['estimated_input_tokens']} input tokens, transcript from {source or 'nothing'}" + (", dark clip (brightened sheets added)" if m["dark"] else ""))
        if a.dry_run:
            json.dump({"manifest": m, "deterministic": prep["out"], "transcript_labels": [l for l, _ in transcripts]}, open(os.path.join(runs_dir, f"{stem}.dryrun.json"), "w"), indent=2)
            if not a.keep_work: shutil.rmtree(work, ignore_errors=True)
            continue
        t_start = datetime.datetime.now().isoformat(timespec="seconds")
        res, msg, wall, path = pl.call(client, a.model, a.effort, pl.SYSTEM, prep["content"], pl.CONTENT_SCHEMA, not a.no_fallbacks)
        cost = pl.cost_of(msg.usage, a.model) or 0.0
        out = pl.apply_content(prep["out"], res, source, provided)
        runlog = {"clip": clip, "batch": batch, "started": t_start, "manifest": m,
                  "describe": {"model": msg.model, "effort": a.effort, "path": path, "wall_s": round(wall, 1), "usage": pl.usage_dict(msg.usage), "cost_usd": cost, "notes": res.get("notes"), "request_id": getattr(msg, "_request_id", None)}}
        print(f"[{stem}] described in {wall:.0f}s, {msg.usage.input_tokens} in / {msg.usage.output_tokens} out, ${cost:.3f}")
        if not a.no_verify:
            runlog["draft_visual_description"] = out["content"]["visual_description"]
            vres, vmsg, vwall, vpath = pl.call(client, a.model, a.verify_effort, pl.VERIFY_SYSTEM, pl.verify_content(prep, out["content"]["visual_description"]), pl.VERIFY_SCHEMA, not a.no_fallbacks)
            vcost = pl.cost_of(vmsg.usage, a.model) or 0.0
            out["content"]["visual_description"] = vres["visual_description"]
            runlog["verify"] = {"model": vmsg.model, "effort": a.verify_effort, "path": vpath, "wall_s": round(vwall, 1), "usage": pl.usage_dict(vmsg.usage), "cost_usd": vcost, "corrections": vres.get("corrections", []), "request_id": getattr(vmsg, "_request_id", None)}
            cost += vcost
            print(f"[{stem}] verified in {vwall:.0f}s, {len(vres.get('corrections', []))} corrections, ${vcost:.3f}")
        runlog["total_cost_usd"] = round(cost, 4)
        out["generated_at"] = schema.now(); out["generated_by"] = {"skill": "footage-describe", "model": msg.model, "effort": a.effort, "verify_model": (vmsg.model if not a.no_verify else None), "verify_effort": (a.verify_effort if not a.no_verify else None)}
        out = pl.merge_existing(out, existing)
        ns = pl.save_reference_stills(clip, out, out_dir, print)
        if ns: print(f"[{stem}] saved {ns} reference still(s) to people/")
        json.dump(out, open(final, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        json.dump(runlog, open(os.path.join(runs_dir, f"{stem}.run.json"), "w"), indent=2)
        if not a.keep_work: shutil.rmtree(work, ignore_errors=True)
        print(f"[{stem}] wrote {final}  (${cost:.3f})"); total += cost; done += 1
    if not a.dry_run: print(f"done: {done} clip(s), estimated cost ${total:.2f} at list prices")

if __name__ == "__main__":
    main()
