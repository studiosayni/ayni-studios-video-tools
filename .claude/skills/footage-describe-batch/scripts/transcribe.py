#!/usr/bin/env python3
"""Whisper transcripts for clips and for synced recorder files.

Camera track: an auto-detect pass, plus forced passes in the languages the directors note lists when auto-detection
disagrees (whisper mis-detects short or quiet speech). Recorder files: transcribed once per recording, cached next to
the recording, then sliced per clip and shifted into video time using the sync offset."""
import glob, json, os, re, subprocess

LANG_CODES = {"spanish": "es", "english": "en", "portuguese": "pt", "french": "fr", "german": "de", "italian": "it",
              "vietnamese": "vi", "chinese": "zh", "japanese": "ja", "quechua": "qu", "dutch": "nl", "russian": "ru", "arabic": "ar"}

def norm_lang(x):
    x = (x or "").strip().lower(); return LANG_CODES.get(x, x[:2] if x else "")

def note_field(note_text, label_regex):
    """First non-empty value of a '**Label:** value' line. Stays on the line (a blank field must not swallow the next one),
    and skips blank occurrences so a client note appended after the batch note can fill the gap."""
    for m in re.finditer(r"\*\*" + label_regex + r":\*\*[ \t]*([^\n]*)", note_text or "", re.I):
        v = m.group(1).strip()
        if v and not v.startswith("**"): return v
    return ""

def expected_languages(note_text):
    v = note_field(note_text, r"Language(?:\(s\))?")
    return [c for c in (norm_lang(p) for p in re.split(r"[,/ ]+", v)) if c and c.isalpha()]

ROLE_WORDS = ("main", "scratch", "ambience", "backup", "wild", "music", "reference")

def norm_device(x):
    return re.sub(r"[\s_\-]+", "", (x or "").strip().lower())

DEVICE_WORD = re.compile(r"audio/([\w-]+)|\b(camera|unsorted|(?:lav|recorder|mic|boom|zoom)\s?\d+)\b", re.I)

def _devices(seg):
    """Device folder names in one role's text. A bare word is taken as a folder name; prose (a note in brackets, "lavs
    on Rama and Maryam", "one lav moved between speakers ... in audio/unsorted") yields only the device-like words in it."""
    seg = re.sub(r"\(([^)]*)\)", lambda m: " " + " ".join(g1 or g2 for g1, g2 in DEVICE_WORD.findall(m.group(1))) + " ", seg)
    out = []
    for item in re.split(r"[,;]+", seg):
        item = item.strip()
        if not item: continue
        if re.fullmatch(r"[\w-]+(?:\s\d+)?", item): out.append(norm_device(item))
        else: out += [norm_device(g1 or g2) for g1, g2 in DEVICE_WORD.findall(item)]
    return [d for d in dict.fromkeys(out) if d]

def audio_sources(note_text):
    """Parse '**Audio sources:** main: lav1, scratch: camera, recorder 1; ambience: camera' into roles.
    Returns {"main": "lav1", "scratch": ["camera", "recorder1"], "ambience": ["camera"], ...}. Devices are folder names under audio/, or camera.
    Notes in brackets are allowed ('main: recorder1 (lavs on Rama and Maryam)'); a second main device ('main: lav1, lav2')
    is kept in "main_also"."""
    roles = {"main": None, "scratch": [], "ambience": []}
    text = note_field(note_text, r"Audio sources?")
    if not text: return roles
    bare = re.sub(r"\([^)]*\)", lambda m: "(" + " " * (len(m.group(0)) - 2) + ")", text)
    parts = [mm for mm in re.finditer(r"(\w+)\s*:", bare) if mm.group(1).lower() in ROLE_WORDS]
    for i, mm in enumerate(parts):
        role = mm.group(1).lower(); devs = _devices(text[mm.end(): parts[i + 1].start() if i + 1 < len(parts) else len(text)])
        if role == "main":
            roles["main"] = devs[0] if devs else None
            if devs[1:]: roles["main_also"] = devs[1:]
        else: roles.setdefault(role, []); roles[role] += devs
    return roles


AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".caf")

def default_main(roles, folder):
    """When the directors note names no main source, the recorders lead: a lav folder under audio/ first, then a
    recorder folder, then any other folder of recordings (files loose in audio/ count as "recorder"); the camera
    only when the batch has no recordings. A note that names a main source always wins."""
    if roles.get("main"): return roles
    d = os.path.join(folder, "audio"); devs = []
    if os.path.isdir(d):
        for x in sorted(os.listdir(d)):
            p = os.path.join(d, x)
            if os.path.isdir(p) and x != "outputs" and any(f.lower().endswith(AUDIO_EXTS) and not f.startswith(".") for f in os.listdir(p)): devs.append(norm_device(x))
            elif x.lower().endswith(AUDIO_EXTS) and not x.startswith(".") and "recorder" not in devs: devs.append("recorder")
    pick = next((x for x in devs if x.startswith("lav")), None) or next((x for x in devs if x.startswith("recorder")), None) or (devs[0] if devs else None)
    roles["main"] = pick or "camera"
    return roles


def _outdir(path):
    return os.path.join(os.path.dirname(os.path.abspath(path)), "outputs", "transcripts")

def _read(p):
    try: return open(p, encoding="utf-8").read()
    except Exception: return None

def transcriber(skill_dir):
    """Return (python, script) for an on-device whisper transcriber, or None."""
    cands = []
    env = os.environ.get("FOOTAGE_TRANSCRIBER_DIR")
    if env: cands.append(env)
    cands.append(os.path.expanduser("~/footage-transcriber"))
    for d in cands:
        py, sc = os.path.join(d, "venv", "bin", "python"), os.path.join(d, "transcribe_audio.py")
        if os.path.exists(py) and os.path.exists(sc): return py, sc
    py = os.path.join(skill_dir, "venv", "bin", "python"); sc = os.path.join(skill_dir, "scripts", "transcribe_audio.py")
    if os.path.exists(py) and os.path.exists(sc):
        if subprocess.run([py, "-c", "import mlx_whisper"], capture_output=True).returncode == 0: return py, sc
    return None

def _run(t, path, outdir, name, language, log):
    py, sc = t; os.makedirs(outdir, exist_ok=True)
    log(f"  transcribing {os.path.basename(path)} with whisper ({'language ' + language if language else 'auto-detect'})...")
    cmd = [py, sc, path, "--output-dir", outdir, "--output-name", name] + (["--language", language] if language else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: log(f"  whisper failed: {r.stderr.strip()[-300:]}")

def _runs(path, stem, outdir, t, log, language=None, expected=None):
    """Auto pass plus forced passes; reuses cached SRTs. Returns [(label, srt_text)]."""
    expected = expected or []
    def have(name): return _read(os.path.join(outdir, f"{name}.srt"))
    def get(name, lang):
        text = have(name)
        if text is None and t: _run(t, path, outdir, name, lang, log); text = have(name)
        elif text is not None: log(f"  transcript found: {name}")
        return text
    out = []
    if language:
        text = get(f"{stem}_{language}", language)
        if text is not None: out.append((f"forced {language}", text))
        return out
    text = get(f"{stem}_auto", None); detected = None
    if text is not None:
        try: detected = norm_lang(json.load(open(os.path.join(outdir, f"{stem}_auto.json"))).get("language"))
        except Exception: detected = None
        out.append((f"auto-detected{' (' + detected + ')' if detected else ''}", text))
    if expected and (detected not in expected):
        forced = [(f"forced {lang}", ft) for lang in expected for ft in [get(f"{stem}_{lang}", lang)] if ft is not None]
        # one listed language: the misdetected auto pass is noise (whisper renders English speech as Arabic when it guesses ar)
        if len(expected) == 1 and forced: out = []
        out += forced
    return out

def supplied(clip, log=print):
    """Human-made subtitle files for this clip in <batch>/transcripts/, named after the clip (P1000208.srt, P1000208_en.srt).
    Captions of edited mixes are not named after a clip and are ignored."""
    d = os.path.join(os.path.dirname(os.path.abspath(clip)), "transcripts"); stem = os.path.splitext(os.path.basename(clip))[0]; out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".srt") and not f.startswith(".") and re.match(re.escape(stem) + r"([ _.\-].*)?\.srt$", f, re.I):
                text = _read(os.path.join(d, f))
                if text: log(f"  supplied transcript found: {f}"); out.append((f"supplied by a person ({f})", text))
    return out

def ensure(clip, mode, skill_dir, stats, log=print, language=None, expected=None):
    """Camera-track transcripts for a clip. mode: 'auto' (find or make) or 'off'."""
    if mode == "off": return []
    stem = os.path.splitext(os.path.basename(clip))[0]
    silent = stats.get("mean_db") is None or stats["mean_db"] < -60
    if silent: log("  camera audio at noise floor or missing, skipping whisper"); return []
    t = transcriber(skill_dir)
    if not t: log("  no on-device transcriber available, continuing with any existing transcripts (see setup.sh --with-whisper)")
    return supplied(clip, log) + _runs(clip, stem, _outdir(clip), t, log, language, expected)

# ---------- synced recorder files ----------
_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")

def parse_srt(text):
    segs = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if not lines: continue
        ti = 1 if len(lines) > 1 and _TS.search(lines[1]) else (0 if _TS.search(lines[0]) else None)
        if ti is None: continue
        m = _TS.search(lines[ti]); g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000; en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        segs.append((st, en, " ".join(lines[ti + 1:]).strip()))
    return segs

def fmt_ts(t):
    t = max(0.0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s - int(s)) * 1000)) % 1000:03d}"

def collapse_loops(segs, min_repeats=3):
    """Whisper sometimes repeats one phrase over quiet audio. Keep the first and say how often it repeated."""
    out = []; i = 0
    while i < len(segs):
        j = i
        while j + 1 < len(segs) and segs[j + 1][2].strip().lower() == segs[i][2].strip().lower(): j += 1
        n = j - i + 1
        if n >= min_repeats: out.append((segs[i][0], segs[j][1], f"{segs[i][2]} (repeated {n} times in a row, likely a transcription loop, treat as unreliable)"))
        else: out.extend(segs[i:j + 1])
        i = j + 1
    return out

def format_srt(segs):
    return "\n\n".join(f"{i}\n{fmt_ts(st)} --> {fmt_ts(en)}\n{tx}" for i, (st, en, tx) in enumerate(segs, 1))

def slice_shift(segs, offset, duration, pad=0.5):
    """Keep segments that overlap the clip's span in the recording and shift them into video time."""
    out = []
    for st, en, tx in segs:
        if en < offset - pad or st > offset + duration + pad: continue
        out.append((max(0.0, st - offset), max(0.0, en - offset), tx))
    return out

def external_for_clip(audio_path, offset, duration, skill_dir, log=print, language=None, expected=None):
    """Transcribe the recorder file once (cached next to it), then return this clip's slice in video time."""
    t = transcriber(skill_dir)
    stem = os.path.splitext(os.path.basename(audio_path))[0]; outdir = _outdir(audio_path)
    if not t and not glob.glob(os.path.join(outdir, f"{stem}_*.srt")):
        log("  no transcriber and no cached recorder transcript; falling back to the camera track"); return []
    runs = _runs(audio_path, stem, outdir, t, log, language, expected)
    out = []
    for lab, text in runs:
        segs = collapse_loops(slice_shift(parse_srt(text), offset, duration))
        out.append((f"{lab}, external recorder {os.path.basename(audio_path)} synced at {offset:.3f} s, timestamps already in video time", format_srt(segs) if segs else "(no speech in this clip's span)"))
    return out
