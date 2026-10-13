"""Scripted layer for asset-describe: everything that needs no model.
ffprobe for technical fields, ffmpeg ebur128 for loudness, embedded tags, sha256, librosa for bpm and key."""
import hashlib, json, os, re, subprocess, sys

AUDIO_EXT = {".wav", ".mp3", ".aif", ".aiff", ".flac", ".m4a", ".aac", ".ogg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif", ".svg", ".psd", ".ai", ".eps"}
MOTION_EXT = {".mov", ".mp4", ".mxf", ".webm"}
PROJECT_EXT = {".aep", ".prproj", ".mogrt", ".aep", ".psd", ".ai"}


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in AUDIO_EXT: return "audio"
    if ext in IMAGE_EXT: return "image"
    if ext in MOTION_EXT: return "motion"
    return None


def run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def ffprobe(path):
    r = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path])
    if r.returncode: return {}
    return json.loads(r.stdout or "{}")


def loudness(path):
    """Integrated loudness (LUFS) and true peak (dBTP) from ffmpeg's ebur128 filter."""
    r = run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    text = r.stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", text)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", text)
    return (float(i[-1]) if i else None), (float(p[-1]) if p else None)


def audio_technical(path):
    info = ffprobe(path); fmt = info.get("format", {}); st = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    lufs, peak = loudness(path)
    bits = st.get("bits_per_raw_sample") or st.get("bits_per_sample")
    return {
        "duration_seconds": round(float(fmt.get("duration", 0)), 3) if fmt.get("duration") else None,
        "sample_rate": int(st["sample_rate"]) if st.get("sample_rate") else None,
        "bit_depth": int(bits) if bits and str(bits).isdigit() and int(bits) > 0 else None,
        "channels": st.get("channels"),
        "codec": st.get("codec_name"),
        "integrated_loudness_lufs": lufs,
        "true_peak_dbtp": peak,
    }, {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}


def image_technical(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in {".svg", ".ai", ".eps", ".psd"}:
        tech = {"format": ext.lstrip("."), "width": None, "height": None, "has_alpha": ext in {".svg", ".psd", ".ai", ".eps"} or None,
                "frame_rate": None, "duration_seconds": None, "color_space": None, "layers": None}
        if ext == ".psd":
            try:
                from PIL import Image
                im = Image.open(path); tech.update(width=im.width, height=im.height, has_alpha="A" in im.getbands())
            except Exception: pass
        return tech
    try:
        from PIL import Image
        im = Image.open(path)
        return {"format": (im.format or ext.lstrip(".")).lower(), "width": im.width, "height": im.height,
                "has_alpha": "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info),
                "frame_rate": None, "duration_seconds": None, "color_space": im.mode,
                "layers": getattr(im, "n_frames", None) if ext == ".gif" else None}
    except Exception:
        return {"format": ext.lstrip("."), "width": None, "height": None, "has_alpha": None, "frame_rate": None, "duration_seconds": None, "color_space": None, "layers": None}


def motion_technical(path):
    info = ffprobe(path); fmt = info.get("format", {}); v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    fr = v.get("avg_frame_rate", "0/1")
    try: num, den = fr.split("/"); fps = round(int(num) / int(den), 3) if int(den) else None
    except Exception: fps = None
    pix = v.get("pix_fmt", "") or ""
    return {"format": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "has_alpha": ("a" in pix and pix not in ("yuva",)) or pix.endswith("a") or "rgba" in pix or "argb" in pix or "bgra" in pix,
            "frame_rate": fps, "duration_seconds": round(float(fmt.get("duration", 0)), 3) if fmt.get("duration") else None,
            "color_space": v.get("color_space") or v.get("color_primaries"), "layers": None}


KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def bpm_and_key(path, max_seconds=180):
    """Tempo and key by librosa: beat tracking for bpm, chroma against Krumhansl profiles for key."""
    try:
        import librosa, numpy as np
    except Exception:
        return None, None, None
    try:
        y, sr = librosa.load(path, sr=22050, mono=True, duration=max_seconds)
        if len(y) < sr: return None, None, None
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(np.atleast_1d(tempo)[0])
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
        best = None
        for i in range(12):
            for name, prof in (("major", MAJOR), ("minor", MINOR)):
                p = np.roll(prof, i); score = float(np.corrcoef(chroma, p)[0, 1])
                if best is None or score > best[0]: best = (score, f"{KEYS[i]} {name}")
        return round(tempo, 1), best[1], round(best[0], 3)
    except Exception as e:
        print(f"  bpm/key skipped ({type(e).__name__}: {str(e)[:80]})", file=sys.stderr)
        return None, None, None


def frames_for_motion(path, work, n=4):
    """A few evenly spaced frames from a motion graphic, for the seeing layer."""
    os.makedirs(work, exist_ok=True)
    dur = motion_technical(path).get("duration_seconds") or 1
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n; p = os.path.join(work, f"frame_{i}.png")
        r = run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", p])
        if r.returncode == 0 and os.path.exists(p): out.append((p, round(t, 2)))
    return out
