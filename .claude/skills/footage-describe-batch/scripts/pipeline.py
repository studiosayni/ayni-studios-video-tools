#!/usr/bin/env python3
"""Shared core for the footage-describe skills.

Deterministic fields come from ffprobe and the batch folder. The content fields (visual_description,
audio_description, tags, transcript, people) come from ONE Claude request with a fixed frame set attached,
then an optional second request checks the description against the frames. Cost is computed from the
response usage at Anthropic list prices.
"""
import base64, glob, hashlib, io, json, os, re, subprocess, sys, time
from PIL import Image, ImageDraw, ImageFont, ImageStat
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mkv"}
TEMPLATE = schema.VIDEO  # schema 2.1; see schema.py
# Anthropic list prices per million tokens (input, output, cache-read multiplier). Cache write (5 min) = 1.25x input, 1 h = 2x.
# Opus 5.5 reads cache at $0.20/MTok (0.05x); the others at 0.1x. Opus 5.5 became the default on 2026-09-22 (see _docs/FINDINGS.md).
PRICING = {"claude-opus-5-5": (4.0, 20.0, 0.05), "claude-opus-5": (5.0, 25.0, 0.1), "claude-sonnet-5": (2.0, 10.0, 0.1), "claude-haiku-4-5": (1.0, 5.0, 0.1)}
FONT_CANDIDATES = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf"]

def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()

def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXT

# ---------- deterministic fields ----------
def xmp_timecode(path, max_bytes=64 * 1024 * 1024):
    """Adobe Media Encoder keeps the source timecode in an XMP packet that ffprobe does not read."""
    try:
        with open(path, "rb") as f: data = f.read(max_bytes)
        m = re.search(rb"<xmpDM:startTimecode[^>]*?xmpDM:timeValue=\"([^\"]+)\"", data) or re.search(rb"<xmpDM:startTimecode>.*?<xmpDM:timeValue>([^<]+)</xmpDM:timeValue>", data, re.S)
        if not m: return None
        tc = m.group(1).decode("utf-8", "ignore").strip()
        return tc if re.match(r"^\d{2}[:;]\d{2}[:;]\d{2}[:;]\d{2}$", tc) else None
    except Exception:
        return None

def probe(clip):
    r = sh(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", clip])
    if r.returncode != 0: raise RuntimeError(f"ffprobe failed on {clip}: {r.stderr.strip()[:200]}")
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    fmt_tags = {k.lower(): val for k, val in (j["format"].get("tags") or {}).items()}
    v_tags = {k.lower(): val for k, val in (v.get("tags") or {}).items()}
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    fps = round(int(num) / int(den), 3) if int(den) else None
    bit_depth = int(v["bits_per_raw_sample"]) if str(v.get("bits_per_raw_sample", "")).isdigit() else (10 if "10" in (v.get("pix_fmt") or "") else (8 if v.get("pix_fmt") else None))
    color = [v.get(k) for k in ("color_primaries", "color_transfer", "color_space") if v.get(k)]
    color_profile = ", ".join(dict.fromkeys(color)) if color else None
    if color_profile and v.get("color_range"): color_profile = f"{color_profile} ({v['color_range']} range)"
    tmcd = next((s for s in j["streams"] if s.get("codec_type") == "data" and "tmcd" in (s.get("codec_tag_string") or "")), None)
    timecode = ((tmcd.get("tags") or {}).get("timecode") if tmcd else None) or v_tags.get("timecode") or fmt_tags.get("timecode")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    ab = (a or {}).get("bits_per_raw_sample") or (a or {}).get("bits_per_sample")
    camera_audio = {"channels": int(a["channels"]) if a and a.get("channels") else None, "sample_rate": int(a["sample_rate"]) if a and a.get("sample_rate") else None,
                    "bit_depth": int(ab) if a and str(ab).isdigit() and int(ab) > 0 else None, "codec": (a or {}).get("codec_name"), "mean_level_db": None, "silent": None}
    tc_note = "from the file's timecode track" if timecode else None
    if not timecode:
        timecode = xmp_timecode(clip); tc_note = "from the XMP packet written by the transcoder" if timecode else None
    if timecode: tc_note += "; free-run camera timecode, comparable only between clips from the same camera"
    return {
        "camera_metadata": {
            "resolution": f"{v['width']}x{v['height']}", "frame_rate": fps,
            "codec": f"{v['codec_name']}" + (f" ({v['profile']} profile)" if v.get("profile") else ""),
            "bit_depth": bit_depth, "color_profile": color_profile, "timecode_start": timecode or None, "timecode_note": tc_note,
            "duration_seconds": round(float(j["format"]["duration"]), 3), "date_recorded": None,
            "camera_clock_time": fmt_tags.get("creation_time"), "clock_trusted": False,
        },
        "camera_audio": camera_audio, "proxy_of": xmp_derived_from(clip),
        "file_size_bytes": os.path.getsize(clip), "content_hash": "sha256:" + sha256(clip),
        "creation_time": fmt_tags.get("creation_time"), "duration": float(j["format"]["duration"]),
        "width": int(v["width"]), "height": int(v["height"]),
    }

def sha256(path, chunk=8 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""): h.update(block)
    return h.hexdigest()

def xmp_derived_from(path, max_bytes=64 * 1024 * 1024):
    """Adobe writes the source file path into the XMP DerivedFrom reference when it has one."""
    try:
        with open(path, "rb") as f: data = f.read(max_bytes)
        m = re.search(rb"xmpMM:DerivedFrom[^>]*?stRef:filePath=\"([^\"]+)\"", data) or re.search(rb"<xmpMM:DerivedFrom>.*?<stRef:filePath>([^<]+)</stRef:filePath>", data, re.S)
        return os.path.basename(m.group(1).decode("utf-8", "ignore")) if m else None
    except Exception: return None

def audio_stats(clip):
    vol = sh(["ffmpeg", "-i", clip, "-af", "volumedetect", "-f", "null", "-"]).stderr
    sil = sh(["ffmpeg", "-i", clip, "-af", "silencedetect=noise=-35dB:d=1.5", "-f", "null", "-"]).stderr
    mean = re.search(r"mean_volume: ([-\d.]+) dB", vol); mx = re.search(r"max_volume: ([-\d.]+) dB", vol)
    silences = re.findall(r"silence_(start|end): ([\d.]+)", sil)
    has_audio = bool(mean)
    lines = [f"mean_volume: {mean.group(1) if mean else 'no audio stream'} dB", f"max_volume: {mx.group(1) if mx else 'n/a'} dB",
             "silences >= 1.5 s below -35 dB: " + (", ".join(f"{k} {t}" for k, t in silences) if silences else "none")]
    return {"text": "\n".join(lines), "mean_db": float(mean.group(1)) if mean else None, "has_audio": has_audio}

def shoot_date(note_text, batch):
    for m in re.finditer(r"\*\*Shoot date\(s\):\*\*[ \t]*(\d{4}(?:-\d{2}){0,2})?", note_text or ""):   # a year or year-month is allowed when the day is unknown
        if m.group(1): return m.group(1)
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{2})\.", batch or "")
    if not m or m.group(2) == "00" or m.group(3) == "00": return None   # placeholder folder dates are not dates
    return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"

def project_code(batch, path=""):
    return schema.project_of(batch, path)

# ---------- frames ----------
def fmt_t(t):
    m = int(t // 60); return f"{m:02d}:{t - m*60:04.1f}"

def label(img, text, size=22):
    d = ImageDraw.Draw(img); f = font(size)
    b = d.textbbox((0, 0), text, font=f); pad = 6
    d.rectangle([4, 4, 4 + (b[2]-b[0]) + 2*pad, 4 + (b[3]-b[1]) + 2*pad + 4], fill=(0, 0, 0))
    d.text((4 + pad, 4 + pad), text, font=f, fill=(255, 255, 255)); return img

def brighten(img, gamma=2.0, gain=1.35):
    lut = [min(255, int(((i / 255.0) ** (1 / gamma)) * 255 * gain)) for i in range(256)]
    return img.point(lut * 3)

def build_frames(clip, duration, work, stem):
    os.makedirs(work, exist_ok=True)
    interval = 2.0 if duration / 2.0 >= 12 else max(0.5, duration / 12.0)
    for f in glob.glob(f"{work}/raw_*.jpg") + glob.glob(f"{work}/ov_*.jpg"): os.remove(f)
    sh(["ffmpeg", "-y", "-v", "error", "-i", clip, "-vf", f"fps=1/{interval}", "-q:v", "2", f"{work}/raw_%03d.jpg"])
    sh(["ffmpeg", "-y", "-v", "error", "-i", clip, "-vf", f"fps=16/{duration}", "-q:v", "2", f"{work}/ov_%02d.jpg"])
    raws = sorted(glob.glob(f"{work}/raw_*.jpg"))
    if not raws: raise RuntimeError(f"no frames extracted from {clip}")
    frames = [(i * interval, Image.open(p).convert("RGB")) for i, p in enumerate(raws)]
    lumas = [ImageStat.Stat(im.convert("L")).mean[0] for _, im in frames]
    mean_luma = sum(lumas) / len(lumas)
    dark = mean_luma < 45
    tile_w, tile_h = 780, 585  # sheets land at 1560x1170, under the API's 1568 px long edge
    images = []
    ov = Image.new("RGB", (1560, 1170), (20, 20, 20)); n_ov = 0
    for j, p in enumerate(sorted(glob.glob(f"{work}/ov_*.jpg"))[:16]):
        t = j * duration / 16; im = Image.open(p).convert("RGB").resize((390, 292)); label(im, f"t={fmt_t(t)}", 16)
        ov.paste(im, ((j % 4) * 390, (j // 4) * 292)); n_ov += 1
    images.append((f"Overview: {n_ov} evenly spaced frames across the whole clip, left to right then top to bottom.", ov))
    def sheets(variant):
        out = []
        for k in range(0, len(frames), 4):
            g = frames[k:k+4]; sheet = Image.new("RGB", (2 * tile_w, 2 * tile_h), (20, 20, 20))
            for q, (t, im) in enumerate(g):
                tile = im.resize((tile_w, tile_h)); tile = brighten(tile) if variant == "brightened" else tile
                label(tile, f"{stem}  t={fmt_t(t)}  ({t:.1f}s)" + ("  brightened" if variant == "brightened" else ""))
                sheet.paste(tile, ((q % 2) * tile_w, (q // 2) * tile_h))
            n = k // 4 + 1
            out.append((f"Contact sheet {n} of {(len(frames) + 3) // 4}{' (brightened copy, exposure lifted for legibility)' if variant == 'brightened' else ''}: stills at t={g[0][0]:.1f}s to {g[-1][0]:.1f}s, left to right then top to bottom.", sheet))
        return out
    images += sheets("normal")
    if dark: images += sheets("brightened")
    return {"images": images, "n_stills": len(frames), "interval": interval, "mean_luma": round(mean_luma, 1), "dark": dark}

def jpeg_b64(img, quality=85):
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=quality); return base64.standard_b64encode(buf.getvalue()).decode()

def est_image_tokens(img):
    w, h = img.size; s = min(1.0, 1568 / max(w, h)); return int((w * s) * (h * s) / 750)

# ---------- prompts ----------
SYSTEM = """You catalogue raw footage for a video production company's media library. You receive one clip's directors note, its verified technical facts, its audio statistics, raw whisper transcripts, and a fixed set of frames. Write the content fields an editor will search on.

Rules
- Describe only what is visible in the frames. Never carry a detail over from the directors note unless the frames confirm it; if the note says something the frames do not show, leave it out of the description.
- visual_description: a detailed shot log, roughly 100 to 250 words, for an editor searching the library. Cover framing and shot type, subjects and what they do, setting, lighting, camera movement, and the key beats with timestamps (mm:ss). Timestamps must come from the burned-in labels. Note when the framing changes and when it holds.
- audio_description: what an editor would hear, built from the transcript, the audio statistics and what the visuals imply. Say which parts are inferred. You cannot hear the audio.
- tags: 8 to 20 lowercase searchable tags (subjects, actions, location type, shot type, mood, time of day, species).
- shot: one value per field from the allowed vocabulary, describing the clip as a whole (use mixed when it changes).
- summary: one sentence, at most 25 words, that says what the clip is: subject, action, setting, framing. It heads every search result.
- subjects: species (common names of animals and plants that matter in the shot), activities (what people or animals are doing, as short verb phrases), objects (things an editor might search for: boat, crate, machete, phone), all lowercase English, each list at most 8 items and empty when nothing applies; setting from the allowed vocabulary.
- people_count: how many people are visible at the clip's busiest moment, from the vocabulary, counting background people too.
- speech_kind: from the vocabulary, judged from the transcript and the frames together; none when there is no real speech.
- mood: the clip's emotional register from the vocabulary; neutral when nothing stands out.
- beats: 3 to 8 timestamped moments in video time (seconds, from the burned-in labels) that an editor would scrub to, each with a text of at most 15 words. They must agree with the timestamps in visual_description. One beat for a clip that holds one framing throughout.
- quality_flags: only the visible problems from the vocabulary that an editor should know before pulling the clip; empty when the footage is clean.
- transcript_segments: the real speech as timed segments in video time (seconds), corrected for obvious transcription errors using context, in its original language with correct orthography. Empty when there is no real speech. Whisper invents short phrases on silence or ambient noise; judge every segment against the audio statistics before keeping it. Runs are labelled with the audio source (a device name such as lav1, recorder1, or camera) and its role; prefer the main source and use the others only where the main is unusable or missing. Set transcript_source to the device name you relied on and transcript_language to the ISO code, or null when there is no real speech. Set speaker on a segment to a people id when you can tell who is talking.
- people: one entry per distinct person you can tell apart. Never identify anyone from their face or body. id is generic and numbered in order of first appearance: person_1, person_2. Use a name from the directors note (snake_case) as the id only when the note itself ties that name to something you can see or hear in this clip, such as clothing, a role, a position, or a named audio file; otherwise stay generic. descriptor is what lets an editor pick the person out in this clip: clothing, headwear, what they carry, their role in the action (for example "black shirt, red cap, carrying the camera trap"). Do not describe race, ethnicity, skin colour, age or body shape. Refer to people in visual_description by id and descriptor. best_seen_seconds is the timestamp, taken from the labels burned into the frames you were given, of the one frame where this person is most clearly visible and easiest to tell apart from the others (ideally alone or prominent in frame); use -1 for a voice that is never on camera. on_camera is true when the person is visibly on camera; voice_heard is true when you judge that person's voice is heard. A voice with no visible person gets its own entry with on_camera false.
- notes: anything you were unsure about, in one or two sentences."""

def _vocab_block():
    """The closed vocabularies with their meanings, appended to the system prompt so the model, the database and the search
    layer share one definition of every value (schema.py is the source)."""
    lines = ["", "Vocabularies (value: meaning)"]
    for field, vals in schema.SHOT_MEANING.items(): lines.append(f"- shot.{field}: " + "; ".join(f"{k}: {v}" for k, v in vals.items()))
    for name, vals in (("subjects.setting", schema.SETTING_VOCAB), ("people_count", schema.PEOPLE_COUNT_VOCAB), ("speech_kind", schema.SPEECH_KIND_VOCAB),
                       ("mood", schema.MOOD_VOCAB), ("quality_flags", schema.QUALITY_FLAG_VOCAB)):
        lines.append(f"- {name}: " + "; ".join(f"{k}: {v}" for k, v in vals.items()))
    return "\n".join(lines)
SYSTEM += _vocab_block()

CONTENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "visual_description": {"type": "string"},
        "audio_description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "shot": {"type": "object", "additionalProperties": False,
                 "properties": {k: {"type": "string", "enum": v} for k, v in schema.SHOT_VOCAB.items()},
                 "required": list(schema.SHOT_VOCAB.keys())},
        "summary": {"type": "string"},
        "subjects": {"type": "object", "additionalProperties": False,
                     "properties": {"species": {"type": "array", "items": {"type": "string"}}, "activities": {"type": "array", "items": {"type": "string"}},
                                    "objects": {"type": "array", "items": {"type": "string"}}, "setting": {"type": "string", "enum": list(schema.SETTING_VOCAB)}},
                     "required": ["species", "activities", "objects", "setting"]},
        "people_count": {"type": "string", "enum": list(schema.PEOPLE_COUNT_VOCAB)},
        "speech_kind": {"type": "string", "enum": list(schema.SPEECH_KIND_VOCAB)},
        "mood": {"type": "string", "enum": list(schema.MOOD_VOCAB)},
        "beats": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                  "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}}, "required": ["start", "end", "text"]}},
        "quality_flags": {"type": "array", "items": {"type": "string", "enum": list(schema.QUALITY_FLAG_VOCAB)}},
        "transcript_segments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}, "speaker": {"type": ["string", "null"]}},
                                "required": ["start", "end", "text", "speaker"]}},
        "transcript_language": {"type": ["string", "null"]},
        "transcript_source": {"type": ["string", "null"]},
        "people": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                   "properties": {"id": {"type": "string"}, "descriptor": {"type": "string"}, "on_camera": {"type": "boolean"}, "voice_heard": {"type": "boolean"}, "best_seen_seconds": {"type": "number"}},
                   "required": ["id", "descriptor", "on_camera", "voice_heard", "best_seen_seconds"]}},
        "notes": {"type": "string"},
    },
    "required": ["visual_description", "audio_description", "tags", "shot", "summary", "subjects", "people_count", "speech_kind", "mood", "beats", "quality_flags",
                 "transcript_segments", "transcript_language", "transcript_source", "people", "notes"],
}

VERIFY_SYSTEM = """You check a footage description against the frames it claims to describe. Ground truth is the frames. List every claim in the description that the frames do not support (quote the claim, say what the frames show, with a timestamp), then return a corrected description that keeps the original's structure and length but states only what the frames support. If nothing is wrong, return the description unchanged and an empty list."""

VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "corrections": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                        "properties": {"claim": {"type": "string"}, "frames_show": {"type": "string"}}, "required": ["claim", "frames_show"]}},
        "visual_description": {"type": "string"},
    },
    "required": ["corrections", "visual_description"],
}

def context_text(clip, batch, det, note_text, stats, transcripts, fr, sync=None):
    cm = det["camera_metadata"]
    lines = [f"Clip: {os.path.basename(clip)}", f"Batch folder: {batch}", f"Client: {schema.client_of(clip) or '-'}", f"Project code: {project_code(batch, clip)}", "",
             "Directors note:", (note_text or "(no directors note was provided for this batch)").strip(), "",
             "Verified technical facts (from the file):",
             f"- {cm['resolution']} at {cm['frame_rate']} fps, {cm['codec']}, {cm['bit_depth']}-bit, {cm['color_profile']}, duration {cm['duration_seconds']} s",
             f"- embedded file time: {det['creation_time']} (a transcode or camera clock time, not to be trusted as the shoot date)"]
    if cm.get("timecode_start"): lines.append(f"- timecode at start: {cm['timecode_start']} (free-run, only comparable with other clips from the same camera)")
    lines += ["", "Audio statistics (ffmpeg, camera track):", stats["text"], ""]
    if sync and sync.get("note"): lines += [sync["note"], ""]
    if transcripts:
        lines.append("Raw whisper transcripts (large-v3, unedited):")
        for lab, text in transcripts: lines += [f"[run: {lab}]", (text or "").strip() or "(empty)", ""]
    else:
        lines.append("No transcript was produced for this clip.\n")
    lines.append(f"Frames: {fr['n_stills']} stills, one every {fr['interval']:.1f} s, on 2x2 contact sheets with the timestamp burned into each tile, plus one overview sheet." + (" The clip is dark (mean luma {:.0f}/255), so a brightened copy of every sheet follows the normal ones; use it for legibility but describe the true exposure.".format(fr['mean_luma']) if fr["dark"] else ""))
    return "\n".join(lines)

def prepare(clip, batch, note_text, transcripts, work, sync=None):
    """Everything that happens before any model call. Returns a dict the sync and batch runners both use."""
    stem = os.path.splitext(os.path.basename(clip))[0]
    det = probe(clip); stats = audio_stats(clip)
    det["camera_metadata"]["date_recorded"] = shoot_date(note_text, batch)
    fr = build_frames(clip, det["duration"], work, stem)
    ctx = context_text(clip, batch, det, note_text, stats, transcripts, fr, sync)
    images = [(cap, jpeg_b64(img)) for cap, img in fr["images"]]
    image_files = []   # the same sheets on disk, for the Claude Code mode (an agent reads them with its own eyes, no API)
    for n, (cap, img) in enumerate(fr["images"], 1):
        p = os.path.join(work, f"sheet_{n:02d}.jpg"); img.save(p, "JPEG", quality=85); image_files.append({"caption": cap, "path": p})
    content = [{"type": "text", "text": ctx}]
    for cap, b64 in images:
        content.append({"type": "text", "text": cap}); content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": "Now write the content fields for this clip as JSON matching the required schema."})
    out_json = schema.blank_video()
    out_json["location"], out_json["shoot_context"] = schema.note_fields(note_text)
    out_json.update({"file_name": os.path.basename(clip), "client": schema.client_of(clip), "project": project_code(batch, clip), "batch": batch,
                     "file_size_bytes": det["file_size_bytes"], "content_hash": det["content_hash"], "proxy_of": det["proxy_of"]})
    src = schema.source_of(clip)
    if src:
        out_json["proxy_of"] = out_json.get("proxy_of") or src.get("source_file")
        out_json["proxy_source"] = {"volume": src.get("source_volume"), "path": src.get("source_path"), "preset": src.get("preset"), "match": src.get("match")}
    out_json["camera_metadata"].update(det["camera_metadata"])
    out_json["camera_audio"].update(det["camera_audio"]); out_json["camera_audio"]["mean_level_db"] = stats.get("mean_db"); out_json["camera_audio"]["silent"] = (stats.get("mean_db") is None) or (stats["mean_db"] < -60)
    if sync and sync.get("fields"):
        for k, v in sync["fields"].items():
            if k == "sync_relationships": out_json["sync_relationships"].update(v)
            else: out_json[k] = v
    est_img = sum(est_image_tokens(img) for _, img in fr["images"]); est_txt = (len(SYSTEM) + len(ctx)) // 4
    manifest = {"clip": clip, "batch": batch, "images": len(images), "stills": fr["n_stills"], "interval_s": round(fr["interval"], 2),
                "mean_luma": fr["mean_luma"], "dark": fr["dark"], "estimated_input_tokens": est_img + est_txt, "transcripts": [lab for lab, _ in transcripts], "transcript_source": sync.get("transcript_source") if sync else None}
    return {"stem": stem, "clip": clip, "batch": batch, "duration": det["duration"], "content": content, "images": images, "image_files": image_files,
            "context_text": ctx, "out": out_json, "manifest": manifest}

def verify_content(prep, draft_vd):
    vcontent = [{"type": "text", "text": f"Clip: {os.path.basename(prep['clip'])}, duration {prep['duration']:.1f} s. Frames follow."}]
    for cap, b64 in prep["images"]:
        vcontent.append({"type": "text", "text": cap}); vcontent.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    vcontent.append({"type": "text", "text": "Description to check:\n\n" + draft_vd + "\n\nReturn the corrections and the corrected description as JSON matching the schema."})
    return vcontent

def _lower_list(x, n=8):
    return list(dict.fromkeys(str(t).strip().lower() for t in (x or []) if str(t).strip()))[:n]

def apply_content(out_json, result, transcript_source=None, allowed_sources=None):
    reported = result.get("transcript_source")
    src = reported if (reported and (not allowed_sources or reported in allowed_sources)) else transcript_source
    segs = [{"start": round(float(sg["start"]), 2), "end": round(float(sg["end"]), 2), "text": sg["text"].strip(), "speaker": sg.get("speaker")}
            for sg in result.get("transcript_segments") or [] if sg.get("text", "").strip()]
    out_json["content"].update({"visual_description": result["visual_description"], "audio_description": result["audio_description"],
                                "tags": [str(t).lower() for t in result.get("tags", [])], "shot": result.get("shot") or out_json["content"]["shot"],
                                "transcript_segments": segs, "transcript": schema.transcript_string(segs), "transcript_language": result.get("transcript_language") if segs else None,
                                "transcript_source": src if segs else None, "notes": result.get("notes") or None,
                                # 2.6 facets, straight from the model that saw the frames
                                "summary": (result.get("summary") or "").strip() or None,
                                "subjects": {"species": _lower_list(result.get("subjects", {}).get("species")), "activities": _lower_list(result.get("subjects", {}).get("activities")),
                                             "objects": _lower_list(result.get("subjects", {}).get("objects")), "setting": result.get("subjects", {}).get("setting")},
                                "people_count": result.get("people_count"), "speech_kind": result.get("speech_kind") if segs or result.get("speech_kind") == "none" else "none",
                                "mood": result.get("mood"),
                                "beats": sorted([{"start": round(float(b["start"]), 2), "end": round(float(b["end"]), 2), "text": b["text"].strip()} for b in result.get("beats") or [] if b.get("text", "").strip()], key=lambda b: b["start"]),
                                "quality_flags": sorted(set(str(f) for f in result.get("quality_flags") or [] if f in schema.QUALITY_FLAG_VOCAB)),
                                "facets_source": "describe"})
    out_json["has_speech"] = bool(segs)
    out_json["people"] = [{**schema.PERSON, "id": p["id"], "descriptor": (p.get("descriptor") or None), "on_camera": bool(p["on_camera"]), "voice_heard": bool(p["voice_heard"]),
                           "best_seen_seconds": (round(float(p["best_seen_seconds"]), 2) if p.get("on_camera") and isinstance(p.get("best_seen_seconds"), (int, float)) and p["best_seen_seconds"] >= 0 else None)} for p in result.get("people") or []]
    return out_json

def merge_existing(new, old):
    """Re-describing a clip keeps what people and other skills wrote: usage, review, rights, canonical names,
    and the sync fields when no sync map was applied this run."""
    if not isinstance(old, dict): return new
    old = schema.migrate_video(old)
    for k in ("usage", "review", "rights"):
        if isinstance(old.get(k), dict): new[k] = old[k]
    prev = {p.get("id"): p for p in old.get("people") or []}
    for p in new.get("people") or []:
        o = prev.get(p["id"])
        if o:   # same id as before: keep the name and the batch link a person or people-link gave it
            p["canonical_name"] = o.get("canonical_name") or p.get("canonical_name"); p["person_key"] = o.get("person_key") or p.get("person_key")
    if new.get("sync_status") in (None, "not_run") and old.get("sync_status") not in (None, "not_run"):
        new["sync_status"] = old["sync_status"]; new["has_external_audio"] = old.get("has_external_audio")
        if isinstance(old.get("sync_relationships"), dict): new["sync_relationships"] = old["sync_relationships"]
    return new

def save_reference_stills(clip, out_json, dest_folder, log=print):
    """Pull one still per on-camera person at best_seen_seconds into <dest_folder>/people/<clip>__<id>.jpg and record the path.
    The whole frame is kept (no crop): the descriptor says who to look at. This identifies nobody; it gives a person
    something to look at when they decide who person_2 is."""
    people = [p for p in out_json.get("people") or [] if p.get("on_camera") and p.get("best_seen_seconds") is not None]
    if not people: return 0
    d = os.path.join(dest_folder, "people"); os.makedirs(d, exist_ok=True); stem = os.path.splitext(os.path.basename(clip))[0]; n = 0
    dur = (out_json.get("camera_metadata") or {}).get("duration_seconds") or 0
    for p in people:
        t = max(0.0, min(float(p["best_seen_seconds"]), max(0.0, dur - 0.1) if dur else float(p["best_seen_seconds"])))
        name = f"{stem}__{re.sub(r'[^A-Za-z0-9_-]+', '_', p['id'])}.jpg"; path = os.path.join(d, name)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", clip, "-frames:v", "1", "-q:v", "3", "-vf", "scale='min(1280,iw)':-2", path], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0: p["reference_still"] = f"people/{name}"; n += 1
        else: log(f"  could not save a still for {p['id']} at {t:.1f}s")
    return n

def usage_dict(u):
    return {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_creation_input_tokens": u.cache_creation_input_tokens, "cache_read_input_tokens": u.cache_read_input_tokens}

def cost_of(usage, model, discount=1.0):
    pin, pout, rd = PRICING.get(model, (None, None, None))
    if pin is None:
        for k, v in PRICING.items():
            if k.split("-")[1] in (model or ""): pin, pout, rd = v; break
    if pin is None: return None
    cc = getattr(usage, "cache_creation", None)
    c5 = getattr(cc, "ephemeral_5m_input_tokens", None) if cc else None
    c1 = getattr(cc, "ephemeral_1h_input_tokens", None) if cc else None
    create = usage.cache_creation_input_tokens or 0
    if c5 is None: c5, c1 = create, 0
    return round(((usage.input_tokens or 0) * pin + (c5 or 0) * pin * 1.25 + (c1 or 0) * pin * 2 + (usage.cache_read_input_tokens or 0) * pin * rd + (usage.output_tokens or 0) * pout) / 1e6 * discount, 4)

def call(client, model, effort, system, content, schema, use_fallbacks, max_tokens=32000):
    """One synchronous request with structured output. Returns (parsed_json, message, wall_seconds, path)."""
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": content}],
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    t0 = time.time(); path = "messages"
    import anthropic
    retryable = (anthropic.APITimeoutError, anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)
    last = None
    for attempt in range(4):
        try:
            return _call_once(client, kwargs, use_fallbacks, t0, path)
        except retryable as e:
            last = e; wait = 20 * (2 ** attempt)
            print(f"  API {type(e).__name__}; retry {attempt + 1}/3 in {wait}s", file=sys.stderr); time.sleep(wait); t0 = time.time()
    raise last

def _call_once(client, kwargs, use_fallbacks, t0, path):
    if use_fallbacks:
        try:
            with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs) as s:
                msg = s.get_final_message(); path = "beta.messages+fallbacks"
        except Exception as e:
            print(f"  fallbacks path unavailable ({type(e).__name__}: {str(e)[:120]}); using messages.stream", file=sys.stderr)
            with client.messages.stream(**kwargs) as s: msg = s.get_final_message()
    else:
        with client.messages.stream(**kwargs) as s: msg = s.get_final_message()
    wall = time.time() - t0
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"the model declined this request: {getattr(msg, 'stop_details', None)}")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if not text.strip(): raise RuntimeError(f"empty response (stop_reason {msg.stop_reason}); raise max_tokens or lower effort")
    return json.loads(text), msg, wall, path
