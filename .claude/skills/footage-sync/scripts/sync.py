#!/usr/bin/env python3
"""footage-sync: recorder-first sync, the way an editor syncs a shoot. First every recording is matched against every
other (two lavs, a room recorder, the files a recorder split into when paused) and those that overlap are put on one
clock as a session. Then every camera clip is placed on its session's timeline using all of that session's recordings at
once, and each camera's shooting order settles the clips in between. Matching is by speech-band onset envelopes
(engine.py); no model is involved except in --refresh-transcripts. File-name dates and camera clocks are never used.

Layout: recordings live in <batch>/audio/<device>/ (the folder name is the device: lav1, recorder1, ...). Files placed
directly in <batch>/ or <batch>/audio/ get the device name "recorder". The directors note names roles:
    **Audio sources:** main: lav1, scratch: camera, recorder1; ambience: camera

Offset convention: an offset is the position in the reference file where the other file's first sample or frame occurs
(reference_time = other_time + offset). For a video match the reference is the recording. For a recording pair the JSON
says which file is the reference. Positive means the reference started first. Each clip also gets its session and its
position on that session's clock (master_offset_s).

Outputs: <batch>/sync_map.json (sessions, recording links, clips), <batch>/sync_timeline.fcpxml (the synced timeline for
Final Cut), <recording>.json next to every recording, and with --relink the sync fields inside every existing
<clip>.json. --refresh-transcripts re-does transcript, audio description and speaker flags from the main audio source
with a text-only model call, for clips with a confident recorder match only.

usage: sync.py BATCH_FOLDER [BATCH_FOLDER ...] [--audio DIR ...] [--relink] [--include-weak] [--refresh-transcripts]
                [--primary DEVICE] [--language es] [--refresh-model claude-sonnet-5] [--refresh-clips A,B] [--json-only]
"""
import argparse, datetime, json, math, os, re, subprocess, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); SKILL_DIR = os.path.dirname(HERE); sys.path.insert(0, HERE)
import transcribe as tr, schema, engine as E, timeline
import hashlib

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mkv"}
AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".caf"}
SR = 8000

def sh(cmd): return subprocess.run(cmd, capture_output=True)

def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout else {"format": {}, "streams": []}

def decode(path, start=None, length=None, sr=SR):
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{start:.3f}"] if start else []) + ["-i", path] + (["-t", f"{length:.3f}"] if length else []) + ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    r = sh(cmd)
    if r.returncode != 0 or len(r.stdout) < 4: return None
    return np.frombuffer(r.stdout, dtype=np.float32)

def rms_db(x):
    if x is None or len(x) == 0: return None
    return float(20 * math.log10(float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) + 1e-12))

def tags_of(meta):
    t = {k.lower(): v for k, v in (meta.get("format", {}).get("tags") or {}).items()}
    for s in meta.get("streams", []):
        for k, v in (s.get("tags") or {}).items(): t.setdefault(k.lower(), v)
    return t

def parse_time(s):
    if not s: return None
    try: return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception: return None

def tod_str(tod):
    if tod is None: return None
    h = int(tod // 3600); m = int(tod % 3600 // 60); s = tod % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"

def audio_info(path, rel, device):
    meta = probe(path); t = tags_of(meta); fmt = meta.get("format", {})
    st = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), {})
    sr = int(st.get("sample_rate") or 0) or None
    tref = t.get("time_reference"); tod = (int(tref) / sr) if (tref and str(tref).isdigit() and sr) else None
    bits = st.get("bits_per_raw_sample") or st.get("bits_per_sample"); bits = int(bits) if str(bits).isdigit() and int(bits) > 0 else None
    return {"file": rel, "abs": path, "device": device, "duration": float(fmt.get("duration") or 0), "sample_rate": sr, "bit_depth": bits,
            "channels": int(st.get("channels") or 0) or None, "codec": st.get("codec_name"), "creation_time": t.get("creation_time") or t.get("date"), "time_of_day_s": tod,
            "bwf_time_reference": int(tref) if (tref and str(tref).isdigit()) else None, "recorder_model": t.get("originator") or t.get("encoded_by") or None,
            "channels_detail": ixml_tracks(path), "file_size_bytes": os.path.getsize(path)}

def ixml_tracks(path, max_bytes=4 * 1024 * 1024):
    """Track names from an iXML chunk (multitrack recorders name their inputs there)."""
    try:
        with open(path, "rb") as f: data = f.read(max_bytes)
        i = data.find(b"<BWFXML>")
        if i < 0: return []
        x = data[i:i + 512 * 1024].decode("utf-8", "ignore")
        out = []
        for tr_ in re.findall(r"<TRACK>(.*?)</TRACK>", x, re.S):
            idx = re.search(r"<CHANNEL_INDEX>\s*(\d+)", tr_); name = re.search(r"<NAME>\s*(.*?)\s*</NAME>", tr_, re.S)
            out.append({"index": int(idx.group(1)) if idx else None, "name": name.group(1).strip() if name else None})
        return out
    except Exception: return []

def sha256(path, chunk=8 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""): h.update(block)
    return h.hexdigest()

def xmp_timecode(path, max_bytes=64 * 1024 * 1024):
    try:
        with open(path, "rb") as f: data = f.read(max_bytes)
        m = re.search(rb"<xmpDM:startTimecode[^>]*?xmpDM:timeValue=\"([^\"]+)\"", data) or re.search(rb"<xmpDM:startTimecode>.*?<xmpDM:timeValue>([^<]+)</xmpDM:timeValue>", data, re.S)
        tc = m.group(1).decode("utf-8", "ignore").strip() if m else None
        return tc if tc and re.match(r"^\d{2}[:;]\d{2}[:;]\d{2}[:;]\d{2}$", tc) else None
    except Exception: return None

def video_info(path):
    meta = probe(path); t = tags_of(meta); fmt = meta.get("format", {})
    tc = t.get("timecode") or xmp_timecode(path); tod = None
    if tc and re.match(r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$", tc):
        h, m, s, f = re.split(r"[:;]", tc); v = next((s2 for s2 in meta.get("streams", []) if s2.get("codec_type") == "video"), {})
        num, den = (v.get("r_frame_rate") or "25/1").split("/"); fps = int(num) / int(den) if int(den) else 25
        tod = int(h) * 3600 + int(m) * 60 + int(s) + int(f) / fps
    return {"file": path, "duration": float(fmt.get("duration") or 0), "creation_time": t.get("creation_time"), "timecode": tc, "time_of_day_s": tod}

def find_recordings(folder, extra_dirs):
    out = []
    def add(d, device, base):
        if not os.path.isdir(d): return
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if os.path.isfile(p) and os.path.splitext(f)[1].lower() in AUDIO_EXT and not f.startswith("."):
                out.append((p, os.path.relpath(p, base) if base else f, device))
    add(folder, "recorder", folder)
    audio = os.path.join(folder, "audio"); add(audio, "recorder", folder)
    if os.path.isdir(audio):
        for d in sorted(os.listdir(audio)):
            if os.path.isdir(os.path.join(audio, d)) and not d.startswith("."): add(os.path.join(audio, d), tr.norm_device(d), folder)
    for x in extra_dirs:   # recordings outside the batch keep a path relative to it ("../DAY 05/B1/x.mp3"), so every reader can open them
        x = os.path.abspath(x); add(x, tr.norm_device(os.path.basename(x)), folder)
    return out

def role_of(device, roles):
    if device == roles.get("main") or device in roles.get("main_also", []): return "main"
    for r, devs in roles.items():
        if r != "main" and isinstance(devs, list) and device in devs: return r
    return "unassigned"

def resolve_main(entry, roles, primary=None):
    """Decide which source feeds the transcript for a clip: the main device, else fallbacks."""
    main = tr.norm_device(primary) if primary else (roles.get("main") or "camera")
    ok = [m for m in entry["matches"] if m["status"] == "confident"]
    cam_ok = entry["camera_track"] == "ok"
    order = [main] + [d for d in ["camera"] + [m["device"] for m in ok] if d != main]
    for src in order:
        if src == "camera" and cam_ok: return src, ("ok" if src == main else f"fallback from {main}")
        if src != "camera" and any(m["device"] == src for m in ok): return src, ("ok" if src == main else f"fallback from {main}")
    return None, "silent"

def sync_fields(entry, roles, include_weak, primary=None, synced_at=None, clips=None):
    ok_status = ["confident", "weak"] if include_weak else ["confident"]
    matches = [m for m in entry["matches"] if m["status"] in ok_status]
    main = tr.norm_device(primary) if primary else (roles.get("main") or "camera")
    main_match = next((m for m in matches if m["device"] == main), None)
    best = main_match or (max(matches, key=lambda m: m["ncc"]) if matches else None)
    linked = []
    for other in entry.get("linked", []):
        off = None
        if clips:
            o = next((c for c in clips.values() if c["video"] == other), None)
            if o:
                shared = [(m["recording"], m["offset_seconds"]) for m in entry["matches"] if m["status"] == "confident"]
                for rec, my_off in shared:
                    om = next((m for m in o["matches"] if m["recording"] == rec and m["status"] == "confident"), None)
                    if om: off = round(om["offset_seconds"] - my_off, 3); break
        linked.append({"file": other, "offset_seconds": off})
    return {"has_external_audio": entry["has_external_audio"], "sync_status": entry["sync_status"],
            "sync_relationships": {"primary_audio": {"file": best["recording"] if best else None, "device": best["device"] if best else None, "offset_seconds": best["offset_seconds"] if best else None},
                                   "linked_camera_angles": linked, "visual_sync_group": entry.get("group"),
                                   "audio_sources": [{"file": m["recording"], "device": m["device"], "role": role_of(m["device"], roles), "offset_seconds": m["offset_seconds"], "confidence": m["ncc"], "drift_seconds": m.get("drift_seconds"), "method": "correlation"} for m in matches],
                                   "audio_roles": dict(roles), "method": "correlation" if matches else None, "synced_at": synced_at}}

def load_json(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return None

def write_recording_jsons(folder, recordings, clips, pairs, roles, batch, log, synced_at):
    prior = {}
    for r in recordings:
        o = load_json(os.path.splitext(r["abs"])[0] + ".json") or {}
        if isinstance(o.get("speaker"), dict) and o["speaker"].get("id"): prior[r["file"]] = o["speaker"]
    speakers = schema.assign_speakers([(r["file"], r["device"]) for r in recordings], prior)
    for r in recordings:
        p = os.path.splitext(r["abs"])[0] + ".json"
        old = schema.migrate_recording(load_json(p) or {})
        vids = [{"video": c["video"], "offset_seconds": m["offset_seconds"], "confidence": m["ncc"], "drift_seconds": m.get("drift_seconds"), "status": m["status"], "method": "correlation"}
                for stem, c in clips.items() for m in c["matches"] if m["recording"] == r["file"] and m["status"] in ("confident", "weak")]
        recs = []
        for pr in pairs:
            if pr["status"] == "none": continue
            if pr["reference"] == r["file"]: recs.append({"file": pr["other"], "device": pr["other_device"], "offset_seconds": pr["offset_seconds"], "confidence": pr["ncc"], "status": pr["status"], "method": "correlation"})
            elif pr["other"] == r["file"]: recs.append({"file": pr["reference"], "device": pr["reference_device"], "offset_seconds": round(-pr["offset_seconds"], 3), "confidence": pr["ncc"], "status": pr["status"], "method": "correlation"})
        new = schema.blank_recording()
        new.update({"file_name": os.path.basename(r["file"]), "path": r["file"], "device": r["device"], "role": role_of(r["device"], roles), "client": schema.client_of(r["abs"]), "project": schema.project_of(batch, r["abs"]), "batch": batch,
                    "file_size_bytes": r.get("file_size_bytes"), "content_hash": "sha256:" + sha256(r["abs"]),
                    "duration_seconds": round(r["duration"], 3), "sample_rate": r["sample_rate"], "bit_depth": r["bit_depth"], "channels": r["channels"], "codec": r.get("codec"),
                    "channels_detail": r.get("channels_detail") or [], "recorder_model": r.get("recorder_model"),
                    "timecode_start": tod_str(r.get("time_of_day_s")), "bwf_time_reference": r.get("bwf_time_reference"), "creation_time": r.get("creation_time"), "clock_trusted": False,
                    "speaker": speakers.get(r["file"]), "synced_videos": vids, "synced_recordings": recs,
                    "has_speech": old.get("has_speech"), "transcript": old.get("transcript"), "transcript_language": old.get("transcript_language"), "transcript_segments": old.get("transcript_segments") or [],
                    "notes": old.get("notes"), "generated_at": synced_at, "generated_by": {"skill": "footage-sync", "method": "correlation"}})
        json.dump(new, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    log(f"  wrote {len(recordings)} recording JSON(s)")

LONG_TAKE_S = 600   # longer takes get the recording's own whisper transcript: the refresh model can't write that much back

def direct_transcript(j, runs, main_src, provided):
    """Put the main source's whisper transcript, already sliced to the clip and shifted into clip time, straight into
    the clip JSON. Used for long takes (interviews) and whenever the model refresh fails."""
    pick = next(((lab, text) for lab, text in runs if lab.startswith(f"{main_src} ")), runs[0])
    segs = [{"start": round(a, 2), "end": round(b, 2), "text": t, "speaker": None} for a, b, t in tr.parse_srt(pick[1]) if t.strip()]
    c_ = j["content"]; c_["transcript_segments"] = segs; c_["transcript"] = schema.transcript_string(segs)
    forced = re.search(r"(?:forced|auto-detected \()\s*(\w+)", pick[0])
    if segs and forced: c_["transcript_language"] = forced.group(1)
    elif not segs: c_["transcript_language"] = None
    c_["transcript_source"] = pick[0].split(" ")[0] if segs else None; j["has_speech"] = bool(segs)
    return pick[0], len(segs)

def relink(folder, clips, roles, args, log, note_text, synced_at=None):
    n = 0; refreshed = 0; direct = 0; spent = 0.0
    only = set(args.refresh_clips.split(",")) if getattr(args, "refresh_clips", None) else None
    client = None
    if args.refresh_transcripts:
        import anthropic; client = anthropic.Anthropic(); import refresh as rf
    for stem, c in clips.items():
        p = os.path.join(folder, f"{stem}.json"); j = load_json(p)
        if j is None: continue
        j = schema.migrate_video(j)
        f = sync_fields(c, roles, args.include_weak, args.primary, synced_at, clips)
        j["has_external_audio"] = f["has_external_audio"]; j["sync_status"] = f["sync_status"]
        j["sync_relationships"].update(f["sync_relationships"]); n += 1
        if args.refresh_transcripts and only is not None and stem not in only: pass
        elif args.refresh_transcripts and not any(m["status"] == "confident" for m in c["matches"]):
            log(f"  {stem}: no confident recorder match, transcript left as is")
        elif args.refresh_transcripts:
            main_src, how = resolve_main(c, roles, args.primary)
            expected = tr.expected_languages(note_text)
            confident = [m["device"] for m in c["matches"] if m["status"] == "confident"]
            order = ([main_src] if main_src else []) + [d for d in (["camera"] if c["camera_track"] == "ok" else []) + confident if d != main_src]
            people = [pp.get("id") for pp in (j.get("people") or []) if pp.get("id")]
            stats_text = f"mean_volume: {c.get('camera_track_db')} dB" if c.get("camera_track_db") is not None else "(no camera audio)"
            def runs_for(src):
                if src == "camera": r = tr.ensure(c["video_abs"], "auto", SKILL_DIR, {"mean_db": c.get("camera_track_db")}, log=log, language=args.language, expected=expected)
                else:
                    m = next((m for m in c["matches"] if m["device"] == src and m["status"] == "confident"), None)
                    r = tr.external_for_clip(os.path.join(folder, m["recording"]), m["offset_seconds"], c["duration"], SKILL_DIR, log=log, language=args.language, expected=expected) if m else []
                r = [x for x in r if x[1].strip() and "(no speech in this clip's span)" not in x[1]]
                return [(f"{src} ({role_of(src, roles)}): {lab}", text) for lab, text in r]
            def words(runs): return sum(len(seg[2].split()) for _, text in runs for seg in tr.parse_srt(text))
            runs = []; provided = []
            for src in dict.fromkeys(order):
                r = runs_for(src)
                if r: runs += r; provided.append(src)
                if src == main_src and provided and provided[0] == main_src and words(runs) >= 8: break  # main is solid, stop here
                if len(provided) >= 3: break
            if not runs: log(f"  {stem}: no usable speech from any source, transcript left as is")
            else:
                res = None; why = f"{c['duration'] / 60:.0f}-minute take"
                if c["duration"] <= LONG_TAKE_S:
                    try: res, usage, cost = rf.refresh_clip(client, args.refresh_model, args.refresh_effort, runs, main_src or provided[0], stats_text, (j.get("content") or {}).get("visual_description", ""), people, expected, note_text)
                    except Exception as e: why = f"model refresh failed: {str(e)[:120]}"
                if res is None:
                    lab, k = direct_transcript(j, runs, main_src, provided); direct += 1
                    log(f"  {stem}: transcript set straight from {lab} ({k} segments; {why})")
                    json.dump(j, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False); continue
                spent += cost or 0
                used = res.get("source_used") if res.get("source_used") in provided else provided[0]
                segs = [{"start": round(float(sg["start"]), 2), "end": round(float(sg["end"]), 2), "text": sg["text"].strip(), "speaker": sg.get("speaker")} for sg in res.get("transcript_segments") or [] if sg.get("text", "").strip()]
                c_ = j["content"]; c_["transcript_segments"] = segs; c_["transcript"] = schema.transcript_string(segs); c_["transcript_language"] = res.get("transcript_language") if segs else None
                c_["transcript_source"] = used if segs else None; c_["audio_description"] = res["audio_description"]; j["has_speech"] = bool(segs)
                flags = {sp["id"]: bool(sp["voice_heard"]) for sp in res.get("speakers", []) if sp.get("id") in people}
                for pp in j["people"]:
                    if pp["id"] in flags: pp["voice_heard"] = flags[pp["id"]]
                refreshed += 1
                log(f"  {stem}: transcript refreshed from {used if segs else 'nothing (no real speech in ' + ', '.join(provided) + ')'} (main {main_src}, {how}; provided {', '.join(provided)}), ${cost:.4f}" + (f", note: {res['notes']}" if res.get("notes") else ""))
        json.dump(j, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    log(f"relinked {n} JSON file(s) in {os.path.basename(folder)}" + (f", refreshed {refreshed} transcript(s) for ${spent:.2f}" + (f", {direct} set straight from the recording" if direct else "") if args.refresh_transcripts else ""))

def process_folder(folder, args, log):
    """Recorder-first sync (engine.py): recordings matched to each other into sessions on one clock, then every clip
    placed on its session's timeline using all of that session's recordings, then shooting order fills the gaps."""
    folder = os.path.abspath(folder); batch = os.path.basename(folder)
    note_text = ""
    try: note_text = open(os.path.join(folder, "directors_note.md"), encoding="utf-8").read()
    except Exception: pass
    note_text = schema.with_client_note(note_text, folder)
    roles = tr.default_main(tr.audio_sources(note_text), folder)
    videos = sorted(os.path.join(folder, f) for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.startswith("."))
    found = find_recordings(folder, args.audio)
    log(f"{batch}: {len(videos)} video(s), {len(found)} recording(s), roles {roles}")
    clips = {}; feats = []
    for v in videos:
        stem = os.path.splitext(os.path.basename(v))[0]; info = video_info(v); pcm = decode(v)
        entry = {"video": os.path.basename(v), "video_abs": v, "duration": round(info["duration"], 3), "creation_time": info["creation_time"], "timecode": info["timecode"],
                 "camera_track": "none" if pcm is None else "ok", "camera_track_db": rms_db(pcm), "matches": [], "group": None, "linked": [],
                 "sync_status": "not_run", "has_external_audio": None, "main_source": None, "main_status": None, "session": None, "master_offset_s": None}
        silent = pcm is None or entry["camera_track_db"] is None or entry["camera_track_db"] < -55
        if silent: entry["camera_track"] = "silent" if pcm is not None else "none"
        feats.append({"stem": stem, "A": None if silent else E.band_features(pcm, E.CLIP_SKIP_S)[0], "dur": info["duration"]})
        clips[stem] = entry; del pcm
    recordings = []
    n_max = max([E.CHUNK_S * E.HZ] + [len(f["A"]) for f in feats if f["A"] is not None])
    for path, rel, device in found:
        info = audio_info(path, rel, device); pcm = decode(path)
        if pcm is None: log(f"  cannot decode {rel}"); continue
        F, sil = E.band_features(pcm); del pcm
        if F is None: continue
        info["track"] = E.Track(F, sil, n_max); recordings.append(info); log(f"  recording {rel} [{device}]: {info['duration']:.1f} s")
    edges, sessions, placed = E.sync_batch(recordings, feats, log) if recordings else ([], [], {})
    review = []
    for stem, entry in clips.items():
        p = placed.get(stem)
        if not recordings or p is None:
            entry["sync_status"] = "no_match"; entry["has_external_audio"] = False
        elif entry["camera_track"] != "ok":
            entry["sync_status"] = "needs_review"; review.append({"clip": stem, "reason": "camera track silent or missing; place by hand"})
        else:
            st = p["status"]
            entry["matches"] = [{"recording": m["recording"], "device": m["device"], "offset_seconds": m["offset_seconds"], "ncc": m["score"], "ratio": p["ratio"], "z": p["z"],
                                 "status": st, "session": p.get("group"), "method": p.get("method"), "overlap_s": m["overlap_s"]} for m in p.get("members", [])]
            entry["session"] = p.get("group"); entry["master_offset_s"] = p.get("master_offset_s")
            entry["sync_detail"] = {k: p.get(k) for k in ("z", "ratio", "score", "halves", "method", "reason")}
            if st == "confident": entry["sync_status"] = "matched"; entry["has_external_audio"] = True
            elif st == "weak":
                entry["sync_status"] = "needs_review"; entry["has_external_audio"] = True
                review.append({"clip": stem, "reason": p.get("reason") or "only a weak match; confirm by ear", "candidates": entry["matches"]})
            else:
                entry["sync_status"] = "no_match"; entry["has_external_audio"] = False
                review.append({"clip": stem, "reason": p.get("reason") or "no recording matched the camera track"})
        entry["main_source"], entry["main_status"] = resolve_main(entry, roles, args.primary)
        best = next((m for m in entry["matches"] if m["status"] == "confident"), None)
        log(f"  {stem}: {entry['sync_status']}" + (f", {entry['session']} at {entry['master_offset_s']} s ({best['recording']} at {best['offset_seconds']} s, z {best['z']}, ratio {best['ratio']}, {best['method']})" if best else "") + f", transcript source {entry['main_source']} ({entry['main_status']})")
    # multi-camera groups: clips confidently matched to the same recording with overlapping spans
    by_rec = {}
    for stem, c in clips.items():
        for m in c["matches"]:
            if m["status"] == "confident": by_rec.setdefault(m["recording"], []).append((stem, m["offset_seconds"]))
    gi = 0
    for rec, items in by_rec.items():
        items.sort(key=lambda x: x[1]); cur = []; cur_end = -1
        def flush(cur):
            nonlocal gi
            if len(cur) <= 1: return
            gi += 1; gid = f"{os.path.splitext(os.path.basename(rec))[0]}_g{gi}"
            for s, o in cur:
                e = o + clips[s]["duration"]; clips[s]["group"] = clips[s]["group"] or gid
                clips[s]["linked"] = sorted(set(clips[s]["linked"] + [clips[t]["video"] for t, o2 in cur if t != s and o2 < e and o2 + clips[t]["duration"] > o]))
        for s, o in items:
            if cur and o < cur_end - 0.5: cur.append((s, o)); cur_end = max(cur_end, o + clips[s]["duration"])
            else: flush(cur); cur = [(s, o)]; cur_end = o + clips[s]["duration"]
        flush(cur)
    # recording-to-recording sync comes from the session links
    devs = {r["file"]: r["device"] for r in recordings}
    pairs = [{"reference": e["b"], "reference_device": devs[e["b"]], "other": e["a"], "other_device": devs[e["a"]], "offset_seconds": e["offset_s"],
              "ncc": round(e["votes"] / max(e["pieces"], 1), 2), "ratio": None, "status": "confident", "votes": e["votes"], "pieces": e["pieces"], "drift_ppm": e["drift_ppm"]} for e in edges]
    out = {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "batch": batch,
           "offset_convention": "offset_seconds = position in the reference file where the other file's first frame or sample occurs (reference_time = other_time + offset)",
           "engine": "recorder-first: speech-band onsets, sessions, shooting order", "audio_roles": roles,
           "recordings": [{k: v for k, v in r.items() if k not in ("track", "abs")} for r in recordings],
           "sessions": sessions, "recording_links": edges,
           "clips": {s: {k: v for k, v in c.items() if k != "video_abs"} for s, c in clips.items()}, "recording_pairs": pairs, "review": review}
    p = os.path.join(folder, "sync_map.json"); json.dump(out, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    counts = {k: sum(1 for c in clips.values() if c["sync_status"] == k) for k in ("matched", "needs_review", "no_match")}
    log(f"  wrote {p}: {counts}")
    if recordings: write_recording_jsons(folder, recordings, clips, pairs, roles, batch, log, out["generated"])
    if args.relink or args.refresh_transcripts: relink(folder, clips, roles, args, log, note_text, out["generated"])
    if recordings and not args.json_only:
        tl = timeline.write(folder, {**out, "clips": clips}); log(f"  wrote {tl}")
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("folders", nargs="+"); ap.add_argument("--audio", action="append", default=[], help="extra folder(s) of recordings; the folder name is the device")
    ap.add_argument("--relink", action="store_true", help="write sync fields into existing <clip>.json files"); ap.add_argument("--include-weak", action="store_true")
    ap.add_argument("--refresh-transcripts", action="store_true", help="also re-do transcript, audio description and speaker flags from the main source (implies --relink)")
    ap.add_argument("--primary", help="main audio source for this run, overriding the note (camera or a device name)"); ap.add_argument("--language")
    ap.add_argument("--refresh-model", default="claude-sonnet-5"); ap.add_argument("--refresh-effort", default="high")
    ap.add_argument("--refresh-clips", help="comma-separated clip names: refresh only these (e.g. the ones a previous run failed on)")
    ap.add_argument("--json-only", action="store_true")
    a = ap.parse_args()
    log = (lambda *x: None) if a.json_only else (lambda *x: print(*x, flush=True))
    results = [process_folder(f, a, log) for f in a.folders]
    if a.json_only: print(json.dumps([{"batch": r["batch"], "clips": {k: {"sync_status": v["sync_status"], "main_source": v["main_source"], "matches": v["matches"]} for k, v in r["clips"].items()}, "recording_pairs": r["recording_pairs"], "review": r["review"]} for r in results], indent=2))

if __name__ == "__main__":
    main()
