"""Write a batch's synced timeline as FCPXML (Final Cut Pro 10.6 and later), laid out the way the user syncs a shoot:
one project per recording session with every recording on its own audio lane below the storyline (a device's files
end to end on one lane), every placed clip connected above it at its synced position, and a separate project holding
the clips that couldn't be placed, in card order. The file points at the media-root proxies and recordings, so it opens
on any machine with the drive mounted. It is a view of sync_map.json and is rewritten whenever the batch is re-synced.

usage: timeline.py BATCH_FOLDER [--out PATH]
"""
import argparse, json, math, os, re, subprocess, urllib.parse
from fractions import Fraction
from xml.sax.saxutils import quoteattr

SEQ_FD = Fraction(1001, 30000)


def t(fr): return "0s" if fr == 0 else f"{fr.numerator}/{fr.denominator}s"
def grid(sec, fd=SEQ_FD, up=False):
    n = Fraction(sec) / fd; return (math.ceil(n) if up else math.floor(n)) * fd


def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", p], capture_output=True, text=True)
    d = json.loads(r.stdout or "{}"); v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None); fd = None
    if v and v.get("avg_frame_rate") not in (None, "0/0"):
        num, den = map(int, v["avg_frame_rate"].split("/")); fd = Fraction(den, num) if num else None
    return {"duration": float(d.get("format", {}).get("duration") or 0), "w": v and int(v["width"]), "h": v and int(v["height"]), "fd": fd,
            "ch": int(a.get("channels") or 2) if a else 0, "sr": int(a.get("sample_rate") or 48000) if a else 0}


def camera_of(stem):
    m = re.match(r"[A-Za-z]+", stem); return m.group(0).upper() if m else "PHONE"


class Resources:
    def __init__(self): self.n = 1; self.formats = {}; self.assets = {}; self.xml = []; self.info = {}
    def nid(self): self.n += 1; return f"r{self.n}"
    def fmt(self, w, h, fd):
        k = (w, h, fd)
        if k not in self.formats:
            self.formats[k] = self.nid(); self.xml.append(f'<format id="{self.formats[k]}" frameDuration="{t(fd)}" width="{w}" height="{h}" colorSpace="1-1-1 (Rec. 709)"/>')
        return self.formats[k]
    def asset(self, path):
        real = os.path.realpath(path)
        if real in self.assets: return self.assets[real]
        i = probe(real); fd = i["fd"] or SEQ_FD; f = self.fmt(i["w"], i["h"], fd) if i["w"] else None; aid = self.nid()
        dur = grid(i["duration"], fd if i["w"] else Fraction(1, 48000))
        video = f' hasVideo="1" format="{f}" videoSources="1"' if i["w"] else ""
        audio = f' hasAudio="1" audioSources="1" audioChannels="{i["ch"]}" audioRate="{i["sr"]}"' if i["ch"] else ""
        self.xml.append(f'<asset id="{aid}" name={quoteattr(os.path.splitext(os.path.basename(real))[0])} start="0s" duration="{t(dur)}"{video}{audio}>'
                        f'<media-rep kind="original-media" src={quoteattr("file://" + urllib.parse.quote(real))}/></asset>')
        self.assets[real] = aid; self.info[aid] = i; return aid


def lanes_for(items, sign):
    """Greedy lanes: each (start, end, key) goes to the first lane free at its start. Returns {key: lane}."""
    ends = []; out = {}
    for st, en, key in sorted(items):
        k = next((i for i, e in enumerate(ends) if e <= st + Fraction(1, 100)), None)
        if k is None: ends.append(en); k = len(ends) - 1
        else: ends[k] = en
        out[key] = sign * (k + 1)
    return out


def build(folder, smap):
    batch = smap.get("batch") or os.path.basename(folder); R = Resources(); projects = []
    sessions = smap.get("sessions") or []
    placed = {s: c for s, c in smap["clips"].items() if c.get("sync_status") == "matched" and c.get("session")}
    for g in sessions:
        clips = {s: c for s, c in placed.items() if c["session"] == g["id"]}
        if not clips and len(g["members"]) < 2: continue
        lo = min([m["start_s"] for m in g["members"]] + [c["master_offset_s"] for c in clips.values()])
        shift = -lo
        rec_items = [(Fraction(m["start_s"] + shift).limit_denominator(48000), Fraction(m["start_s"] + shift + m["duration_s"]).limit_denominator(48000), m["file"]) for m in g["members"]]
        # one lane per device: put each device's files on lanes of their own, in device order
        rec_lanes = {}; base = 0
        for dev in sorted({m["device"] for m in g["members"]}):
            ln = lanes_for([x for x in rec_items if next(m for m in g["members"] if m["file"] == x[2])["device"] == dev], -1)
            for k, v in ln.items(): rec_lanes[k] = v - base
            base += max(-v for v in ln.values())
        clip_items = [(Fraction(c["master_offset_s"] + shift).limit_denominator(30000), Fraction(c["master_offset_s"] + shift + c["duration"]).limit_denominator(30000), s) for s, c in clips.items()]
        clip_lanes = lanes_for(clip_items, 1)
        conns = []; end = Fraction(0)
        for st, en, f in sorted(rec_items):
            aid = R.asset(os.path.join(folder, f)); off = grid(st); dur = grid(R.info[aid]["duration"]); end = max(end, off + dur)
            dev = next(m["device"] for m in g["members"] if m["file"] == f)
            conns.append(f'<asset-clip ref="{aid}" lane="{rec_lanes[f]}" offset="{t(off)}" name={quoteattr(f"{os.path.basename(f)} [{dev}]")} start="0s" duration="{t(dur)}" audioRole="dialogue"/>')
        for st, en, s in sorted(clip_items):
            c = clips[s]; aid = R.asset(c["video_abs"] if c.get("video_abs") and os.path.exists(c["video_abs"]) else os.path.join(folder, c["video"]))
            off = grid(st); dur = grid(min(R.info[aid]["duration"], c["duration"])); end = max(end, off + dur)
            conns.append(f'<asset-clip ref="{aid}" lane="{clip_lanes[s]}" offset="{t(off)}" name={quoteattr(s)} start="0s" duration="{t(dur)}" audioRole="dialogue"/>')
        total = grid(float(end), up=True)
        name = f"{batch} sync" + (f" {g['id']}" if len(sessions) > 1 else "") + f" ({len(g['members'])} recordings, {len(clips)} clips)"
        projects.append(f'<project name={quoteattr(name)}><sequence format="r1" duration="{t(total)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">'
                        f'<spine><gap name="Session" offset="0s" start="0s" duration="{t(total)}">{"".join(conns)}</gap></spine></sequence></project>')
    rest = sorted(s for s in smap["clips"] if s not in placed)
    if rest:
        items = []; off = Fraction(0)
        for s in rest:
            c = smap["clips"][s]; p = c.get("video_abs") if c.get("video_abs") and os.path.exists(c["video_abs"]) else os.path.join(folder, c["video"])
            if not os.path.exists(p): continue
            aid = R.asset(p); dur = grid(min(R.info[aid]["duration"], c["duration"]))
            if dur <= 0: continue
            items.append(f'<asset-clip ref="{aid}" offset="{t(off)}" name={quoteattr(f"{s} ({c.get("sync_status")})")} start="0s" duration="{t(dur)}" audioRole="dialogue"/>'); off += dur
        if items:
            projects.append(f'<project name={quoteattr(f"{batch} not synced ({len(items)} clips)")}><sequence format="r1" duration="{t(off)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">'
                            f'<spine>{"".join(items)}</spine></sequence></project>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10"><resources>'
            '<format id="r1" name="FFVideoFormat1080p2997" frameDuration="1001/30000s" width="1920" height="1080" colorSpace="1-1-1 (Rec. 709)"/>'
            + "".join(R.xml) + f'</resources><library><event name={quoteattr(batch + " sync")}>' + "".join(projects) + "</event></library></fcpxml>\n")


def write(folder, smap=None, out=None):
    folder = os.path.abspath(folder); smap = smap or json.load(open(os.path.join(folder, "sync_map.json")))
    out = out or os.path.join(folder, "sync_timeline.fcpxml"); open(out, "w", encoding="utf-8").write(build(folder, smap)); return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("folder"); ap.add_argument("--out"); a = ap.parse_args()
    print("wrote", write(a.folder, out=a.out))
