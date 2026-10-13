#!/usr/bin/env python3
"""Schema 2.6 for clip and recording JSONs: blank documents, migration from older shapes, and small helpers.
Every skill imports this so there is one definition of the shape."""
import copy, datetime, os, re

VERSION = "2.6"
SHOT_VOCAB = {
    "shot_type": ["extreme_wide", "wide", "medium_wide", "medium", "medium_close", "close_up", "extreme_close_up", "mixed"],
    "camera_movement": ["static", "handheld", "pan", "tilt", "push_in", "pull_out", "tracking", "gimbal", "drone", "mixed"],
    "lighting": ["daylight", "overcast", "golden_hour", "night", "artificial", "mixed"],
    "time_of_day": ["dawn", "morning", "midday", "afternoon", "dusk", "night", "unknown"],
    "interior_exterior": ["interior", "exterior", "mixed"],
}
# Schema 2.6 (2026-09-22): closed vocabularies carry a one-line meaning each, so the describe prompt, the SQL check constraints and a
# System One model's Choice criteria (TypeSafe Jev) all read the same definition. Values are the keys; meanings are for prompts only.
SHOT_MEANING = {
    "shot_type": {"extreme_wide": "the environment dominates; people, if any, are small in frame", "wide": "the whole subject in its setting",
                  "medium_wide": "subject from about the knees up", "medium": "subject from the waist up", "medium_close": "subject from the chest up",
                  "close_up": "a face, hands or one object fills the frame", "extreme_close_up": "a detail such as an eye, a texture or lettering",
                  "mixed": "the framing changes substantially within the clip"},
    "camera_movement": {"static": "locked off or held still", "handheld": "small handheld drift without a deliberate move", "pan": "the camera turns horizontally",
                        "tilt": "the camera turns vertically", "push_in": "the camera moves toward the subject", "pull_out": "the camera moves away from the subject",
                        "tracking": "the camera travels alongside or behind a moving subject", "gimbal": "smooth stabilised movement", "drone": "aerial",
                        "mixed": "several kinds of movement within the clip"},
    "lighting": {"daylight": "direct or open sun", "overcast": "soft diffuse daylight, no hard shadows", "golden_hour": "low warm sun near sunrise or sunset",
                 "night": "dark, lit only by practical or artificial sources", "artificial": "lamps, screens or film lights dominate", "mixed": "the light changes within the clip"},
    "time_of_day": {"dawn": "first light", "morning": "early to mid morning", "midday": "high sun", "afternoon": "mid to late afternoon", "dusk": "last light",
                    "night": "after dark", "unknown": "cannot be told from the frames"},
    "interior_exterior": {"interior": "inside a building, vehicle or tent", "exterior": "outdoors", "mixed": "both within the clip"},
}
SETTING_VOCAB = {
    "rainforest": "dense tropical forest, jungle trail or canopy", "river_or_lake": "on or beside a river, creek, lake or flooded forest",
    "coast_or_sea": "beach, open sea, reef or shoreline", "mangrove_or_wetland": "mangroves, marsh, tidal flats", "desert": "sand, dunes, dry scrub",
    "mountain": "highlands, hills, mountain trail", "farm_or_garden": "fields, crops, nursery, orchard, animal pens", "village": "small rural settlement, huts, dirt paths",
    "town_or_city": "streets, buildings, traffic, urban public space", "market_or_shop": "stalls, shop floor, retail interior", "warehouse_or_factory": "processing floor, storage, industrial interior",
    "restaurant_or_kitchen": "dining room, kitchen, food preparation", "lodge_or_camp": "eco-lodge, research station, camp, field base", "home_interior": "inside a private home",
    "office_or_meeting_room": "desks, meeting table, conference space", "event_venue_or_stage": "stage, hall, ceremony, exhibition", "boat_or_harbour": "aboard a boat, at a jetty, dock or harbour",
    "road_or_vehicle": "roads, inside a car, bus or truck", "studio": "controlled studio set or backdrop", "other": "none of the listed settings fits",
}
PEOPLE_COUNT_VOCAB = {"none": "no people visible", "one": "a single person", "two_to_three": "two or three people", "small_group": "four to about ten people", "crowd": "more than about ten people"}
SPEECH_KIND_VOCAB = {
    "none": "no real speech", "interview": "a person answers questions, framed for it, with the interviewer off camera",
    "address_to_camera": "someone speaks straight to the lens: a piece to camera, greeting or testimonial", "conversation": "people on screen talk to each other",
    "briefing_or_instruction": "one person explains, teaches or directs a group", "ambient_chatter": "background voices that are not the subject of the clip",
    "voice_off_camera": "a voice that belongs to nobody visible on screen",
}
MOOD_VOCAB = {"calm": "quiet, unhurried, contemplative", "joyful": "laughter, celebration, play", "focused": "people concentrating on work or a task",
              "energetic": "fast action, movement, excitement", "tense": "urgency, conflict, danger or worry", "solemn": "ceremony, grief, gravity", "neutral": "no strong emotional register"}
QUALITY_FLAG_VOCAB = {"shaky": "camera shake an editor would notice", "soft_focus": "the subject is not sharp for a meaningful part of the clip",
                      "overexposed": "blown highlights on the subject", "underexposed": "the subject is too dark to read without grading",
                      "motion_blur": "whip pans or fast movement blur the subject", "obstructed": "something blocks the subject: a hand, a branch, a passer-by",
                      "lens_flare_or_dirt": "flare, rain drops or dirt on the lens over the subject", "rolling_shutter": "wobble or skew from fast movement"}
FACETS = ("summary", "subjects", "people_count", "speech_kind", "mood", "beats", "quality_flags")   # the 2.6 content fields a facet pass fills

OFFSET_CONVENTION = "offset_seconds = position in the reference file where the other file's first frame or sample occurs (reference_time = other_time + offset); negative means the other file started first"

# One entry of a clip's people array. id is per clip (person_1); person_key links the same person across the clips of a batch
# (written by people-link); canonical_name is the real name a person types once and is the link across batches.
PERSON = {"id": None, "descriptor": None, "person_key": None, "canonical_name": None, "on_camera": None, "voice_heard": None,
          "best_seen_seconds": None, "reference_still": None}

VIDEO = {
    "schema_version": VERSION,
    "file_name": "", "client": "", "project": "", "batch": "",
    "location": None, "shoot_context": None,   # 2.6: the directors note's Location line and Overview, filled by script
    "file_size_bytes": None, "content_hash": None, "proxy_of": None,
    "proxy_source": {"volume": None, "path": None, "preset": None, "match": None},
    "has_external_audio": None, "sync_status": "not_run", "has_speech": None,
    "generated_at": None,
    "generated_by": {"skill": None, "model": None, "effort": None, "verify_model": None, "verify_effort": None},
    "camera_metadata": {"camera_id": None, "resolution": None, "frame_rate": None, "codec": None, "bit_depth": None, "color_profile": None,
                        "timecode_start": None, "timecode_note": None, "duration_seconds": None, "date_recorded": None,
                        "camera_clock_time": None, "clock_trusted": False},
    "camera_audio": {"channels": None, "sample_rate": None, "bit_depth": None, "codec": None, "mean_level_db": None, "silent": None},
    "content": {"summary": None, "visual_description": "", "audio_description": "", "tags": [],
                "shot": {"shot_type": None, "camera_movement": None, "lighting": None, "time_of_day": None, "interior_exterior": None},
                # 2.6 facets: closed vocabularies a database can filter on and a System One model can judge without reading prose
                "subjects": {"species": [], "activities": [], "objects": [], "setting": None},
                "people_count": None, "speech_kind": None, "mood": None,
                "beats": [],            # [{start, end, text}] the shot log's timestamped moments as data, for sub-clip search
                "quality_flags": [],    # visible problems an editor should know before pulling the clip
                "facets_source": None,  # "describe" (the frames were seen), "jev" (backfilled from the description), "migrate" (code-derived), or null
                "transcript": None, "transcript_language": None, "transcript_source": None, "transcript_segments": [], "notes": None},
    "people": [],
    "sync_relationships": {"primary_audio": {"file": None, "device": None, "offset_seconds": None}, "linked_camera_angles": [], "visual_sync_group": None,
                           "audio_sources": [], "audio_roles": {"main": None, "scratch": [], "ambience": []}, "method": None, "synced_at": None},
    "review": {"status": "unreviewed", "select": None, "rating": None, "reviewed_by": None, "reviewed_at": None, "notes": None},
    "rights": {"model_release": None, "location_release": None, "restrictions": None, "notes": None},
    "usage": {"used_in_export": False, "export_history": []},
}

RECORDING = {
    "schema_version": VERSION,
    "file_name": "", "path": "", "device": "", "role": "", "client": "", "project": "", "batch": "",
    "file_size_bytes": None, "content_hash": None,
    "duration_seconds": None, "sample_rate": None, "bit_depth": None, "channels": None, "codec": None, "channels_detail": [], "recorder_model": None,
    "timecode_start": None, "bwf_time_reference": None, "creation_time": None, "clock_trusted": False,
    "offset_convention": OFFSET_CONVENTION,
    "speaker": {"id": None, "person_key": None, "canonical_name": None, "assigned_by": None},
    "synced_videos": [], "synced_recordings": [],
    "has_speech": None, "transcript": None, "transcript_language": None, "transcript_segments": [],
    "notes": None, "generated_at": None, "generated_by": None,
}

def blank_video(): return copy.deepcopy(VIDEO)
def blank_recording(): return copy.deepcopy(RECORDING)
def now(): return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

def fmt_mmss(t):
    t = max(0.0, float(t)); m = int(t // 60); s = int(t % 60); return f"{m:02d}:{s:02d}"

def transcript_string(segments):
    """The human-readable transcript, derived from the segments so the two never disagree."""
    lines = [f"{fmt_mmss(sg['start'])} {sg['text'].strip()}" for sg in segments or [] if sg.get("text", "").strip()]
    return "\n".join(lines) or None

def _fill(target, template):
    """Add every missing key from the template, recursing into dicts; existing values win."""
    for k, v in template.items():
        if k not in target: target[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(target[k], dict): _fill(target[k], v)
    return target

def migrate_video(j):
    """Bring a clip JSON of any earlier shape up to the current schema without losing data."""
    if not isinstance(j, dict): return j
    j = copy.deepcopy(j)
    cm = j.get("camera_metadata") or {}
    for k in ("camera_model", "lens"): cm.pop(k, None)
    for k, v in list(cm.items()):
        if v == "": cm[k] = None
    j["camera_metadata"] = cm
    content = j.get("content") or {}
    if "transcript_segments" not in content:
        content["transcript_segments"] = parse_timestamped(content.get("transcript"))
    if content.get("transcript") in ("", None) and content["transcript_segments"]: content["transcript"] = transcript_string(content["transcript_segments"])
    j["content"] = content
    p = j.get("people")
    if isinstance(p, dict):
        feat = p.get("featured") or {}; aud = p.get("audio_speaker_tags") or {}
        ids = list(dict.fromkeys(list(feat.keys()) + list(aud.keys())))
        j["people"] = [{**PERSON, "id": i, "on_camera": bool(feat.get(i, False)), "voice_heard": bool(aud.get(i, False))} for i in ids]
    for q in j.get("people") or []:
        if isinstance(q, dict): j["people"][j["people"].index(q)] = reorder({**PERSON, **q}, PERSON)
    sr = j.get("sync_relationships") or {}
    if "primary_audio" not in sr:
        sr["primary_audio"] = {"file": sr.pop("synced_audio_file", None), "device": None, "offset_seconds": sr.pop("sync_offset_seconds", None)}
        for src in sr.get("audio_sources") or []:
            if src.get("file") == sr["primary_audio"]["file"]: sr["primary_audio"]["device"] = src.get("device")
    sr["linked_camera_angles"] = [x if isinstance(x, dict) else {"file": x, "offset_seconds": None} for x in sr.get("linked_camera_angles") or []]
    j["sync_relationships"] = sr
    if j.get("has_speech") is None and content.get("transcript") is not None: j["has_speech"] = bool(content.get("transcript"))
    _fill(j, VIDEO)
    c = j["content"]
    if not c.get("beats") and c.get("visual_description"):   # 2.6: the timestamps already in the shot log become data
        c["beats"] = beats_from_description(c["visual_description"], (j.get("camera_metadata") or {}).get("duration_seconds"))
        if c["beats"] and not c.get("facets_source"): c["facets_source"] = "migrate"
    if c.get("speech_kind") is None and j.get("has_speech") is False: c["speech_kind"] = "none"
    if c.get("people_count") is None and j.get("people") == [] and c.get("visual_description"): c["people_count"] = "none"
    j["schema_version"] = VERSION
    return reorder(j, VIDEO)

def migrate_recording(j):
    if not isinstance(j, dict): return j
    j = copy.deepcopy(j)
    if not isinstance(j.get("speaker"), dict): j["speaker"] = {}
    j["speaker"] = {**{"id": None, "person_key": None, "canonical_name": None, "assigned_by": None}, **j["speaker"]}
    if "transcript_segments" not in j: j["transcript_segments"] = parse_timestamped(j.get("transcript"))
    if j.get("generated") and not j.get("generated_at"): j["generated_at"] = j.pop("generated")
    j.pop("generated", None)
    _fill(j, RECORDING); j["schema_version"] = VERSION
    return reorder(j, RECORDING)

_DATED = re.compile(r"^\d\d\.\d\d\.\d\d")
_CODED = re.compile(r"^\d\d\.\d\d\.\d\d\.\d\d_([^_]+)_")

def client_of(path):
    """Client folder for a clip, recording or batch path: the folder right under media/ (media/<client>/<batch>/...).
    Empty for the older flat layout (media/<batch>/...) or a path outside media/."""
    parts = os.path.abspath(path).split(os.sep)
    if "media" not in parts: return ""
    i = parts.index("media")
    if i + 2 >= len(parts): return ""
    cand = parts[i + 1]
    return "" if _DATED.match(cand) or cand.startswith((".", "_")) else cand

def project_of(batch, path):
    """Project code: the _CODE_ in an old-style batch name (YY.MM.DD.HH_CODE_keywords), else the client folder."""
    m = _CODED.match(batch or "")
    return m.group(1) if m else client_of(path)

def source_of(path, max_up=4):
    """Look the file up in the nearest sources.json (written by tools/build_source_map.py). Returns the entry or None."""
    import json
    p = os.path.abspath(path); d = os.path.dirname(p)
    for _ in range(max_up):
        f = os.path.join(d, "sources.json")
        if os.path.exists(f):
            try: return (json.load(open(f, encoding="utf-8")).get("files") or {}).get(os.path.relpath(p, d))
            except Exception: return None
        d = os.path.dirname(d)
    return None

def with_client_note(note_text, folder):
    """Batch note first, then media/<client>/client_note.md. The skills read the first match of a line such as
    **Language(s):**, so anything the batch note states wins and the client note fills the gaps."""
    parts = os.path.abspath(folder).split(os.sep)
    for root in ("media", "stock", "stock_footage"):
        if root in parts and parts.index(root) + 1 < len(parts):
            p = os.sep.join(parts[:parts.index(root) + 2] + ["client_note.md"])
            if os.path.exists(p):
                try: client = open(p, encoding="utf-8").read().strip()
                except Exception: client = ""
                if client: return (note_text or "").rstrip() + "\n\n---\nClient context (applies to every batch of this client unless the batch note says otherwise):\n\n" + client + "\n"
            break
    return note_text or ""

def speaker_key(file_name):
    """Group recordings that belong to the same voice by the words at the start of the file name: Alex_1 and Alex_2 share
    a key, 250116_1530 and 250116_1617 (recorder counters, no name) share the empty key."""
    stem = os.path.splitext(os.path.basename(file_name))[0]
    m = re.match(r"^([A-Za-z][A-Za-z '\-]*?)(?=[\s_\-]*\d|[\s_\-]*$)", stem)
    return re.sub(r"[\s_\-]+", " ", m.group(1)).strip().lower() if m else ""

def assign_speakers(recordings, existing=None):
    """Give every recording a generic speaker id (speaker 1, speaker 2, ...) that a person can rename later through
    canonical_name. recordings: [(file_name, device)]. existing: {file_name: speaker dict} from earlier runs, kept as is.
    Files sharing a device and a name prefix get the same id. Nothing here listens to the audio: it is a filing label."""
    existing = existing or {}; out = {}; by_group = {}
    used = [int(m.group(1)) for v in existing.values() for m in [re.match(r"speaker (\d+)$", (v or {}).get("id") or "")] if m]
    for f, dev in recordings:
        if (existing.get(f) or {}).get("id"): by_group.setdefault((dev, speaker_key(f)), existing[f])
    n = max(used, default=0)
    for f, dev in sorted(recordings):
        if (existing.get(f) or {}).get("id"): out[f] = existing[f]; continue
        g = (dev, speaker_key(f))
        if g not in by_group:
            n += 1; by_group[g] = {"id": f"speaker {n}", "person_key": None, "canonical_name": None, "assigned_by": "file name prefix" if g[1] else "device folder"}
        out[f] = dict(by_group[g])
    return out

def identity(j):
    """A clip is identified by client + batch + file name; file names alone repeat across cards and cameras."""
    return "/".join(x for x in (j.get("client"), j.get("batch"), j.get("file_name")) if x)

def reorder(j, template):
    """Template key order first, then anything extra, so files diff cleanly."""
    out = {k: j[k] for k in template if k in j}
    out.update({k: v for k, v in j.items() if k not in out})
    return out

_TS = re.compile(r"^\s*\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*(.*)$")

def parse_timestamped(text):
    """Best-effort segments from an older 'mm:ss text' transcript string (end times unknown)."""
    if not isinstance(text, str) or not text.strip(): return []
    segs = []
    for line in text.splitlines():
        m = _TS.match(line)
        if not m or not m.group(4).strip(): continue
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        start = (a * 3600 + b * 60 + int(c)) if c else (a * 60 + b)
        segs.append({"start": float(start), "end": None, "text": m.group(4).strip(), "speaker": None})
    return segs


# ---------- 2.6: note fields, beats, search card ----------
def note_fields(note_text):
    """Location line and Overview paragraph of a directors note, for the top-level location and shoot_context fields."""
    t = note_text or ""
    m = re.search(r"\*\*Location:\*\*[ \t]*(.+)", t); location = m.group(1).strip() if m and m.group(1).strip() not in ("", "-") else None
    m = re.search(r"^## Overview\s*\n(.*?)(?=^## |^---|\Z)", t, re.S | re.M)
    ctx = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    return location or None, (ctx[:600] if ctx else None)

_BEAT = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?:\s*[\u2013\u2014-]\s*(\d{1,2}):(\d{2}))?(?![\d:])")

def beats_from_description(text, duration=None, max_words=18):
    """Turn the mm:ss markers a shot log carries into [{start, end, text}]. Each beat's text is the prose that follows its
    timestamp up to the next one, trimmed to a few words. Code only; used when a record predates the describe-time beats."""
    if not isinstance(text, str): return []
    marks = list(_BEAT.finditer(text))
    if not marks: return []
    beats = []
    for i, m in enumerate(marks):
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = int(m.group(3)) * 60 + int(m.group(4)) if m.group(3) else None
        nxt = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        frag = text[m.end():nxt].strip(" ,;:.\u2013\u2014-\n")
        words = frag.split()
        if not words: continue
        txt = " ".join(words[:max_words]).rstrip(",;:")
        if end is None: end = (int(marks[i + 1].group(1)) * 60 + int(marks[i + 1].group(2))) if i + 1 < len(marks) else (round(duration) if duration else None)
        if beats and beats[-1]["start"] == start: beats[-1]["text"] += " " + txt; continue
        beats.append({"start": float(start), "end": float(end) if end is not None else None, "text": txt})
    return beats

def search_card(j, transcript_chars=400):
    """The compact, text-only view of a clip that a search layer judges: what an editor asks about, nothing that only a
    machine needs (codec, hashes, sync offsets, model notes). One card is roughly 400 to 700 tokens."""
    c = j.get("content") or {}; cm = j.get("camera_metadata") or {}; sub = c.get("subjects") or {}
    tr = c.get("transcript") or ""
    return {
        "id": identity(j), "client": j.get("client"), "batch": j.get("batch"), "date_recorded": cm.get("date_recorded"),
        "location": j.get("location"), "shoot_context": j.get("shoot_context"),
        "duration_seconds": cm.get("duration_seconds"),
        "summary": c.get("summary") or ((c.get("visual_description") or "").split(". ")[0][:200] or None),
        "visual_description": c.get("visual_description"), "audio_description": c.get("audio_description"),
        "tags": c.get("tags") or [], "shot": c.get("shot") or {},
        "setting": sub.get("setting"), "species": sub.get("species") or [], "activities": sub.get("activities") or [], "objects": sub.get("objects") or [],
        "people_count": c.get("people_count"), "people": [{"id": p.get("id"), "descriptor": p.get("descriptor"), "name": p.get("canonical_name")} for p in j.get("people") or []],
        "speech_kind": c.get("speech_kind"), "has_speech": j.get("has_speech"), "transcript_language": c.get("transcript_language"),
        "transcript_excerpt": (tr[:transcript_chars] + ("\u2026" if len(tr) > transcript_chars else "")) or None,
        "mood": c.get("mood"), "quality_flags": c.get("quality_flags") or [], "beats": c.get("beats") or [],
        "review": {"status": (j.get("review") or {}).get("status"), "select": (j.get("review") or {}).get("select"), "rating": (j.get("review") or {}).get("rating")},
        "used_in_export": (j.get("usage") or {}).get("used_in_export"),
    }
