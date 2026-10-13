#!/usr/bin/env python3
"""Map every proxy on the media drive to the original it was transcoded from.

Reads Adobe Media Encoder's encoding log (every Source File / Output File pair it has ever written), matches each
file under media/ and stock/ by file name, and confirms the match by comparing the clip's duration with the one the
log recorded. Writes sources.json into each batch folder (or the nearest folder under a client) and a drive-wide
_index/sources_all.csv. No model calls. Safe to re-run: files are rewritten from the log each time.

usage: build_source_map.py /Volumes/media-root [--log PATH] [--dry-run]
"""
import argparse, collections, csv, datetime, glob, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

EXT = (".mp4", ".mp3", ".m4v", ".mov", ".wav", ".aac", ".m4a")
DATED = re.compile(r"^\d\d\.\d\d\.\d\d(\.\d\d)?")
REC = re.compile(r" - Source File: (.*?)\r?\n - Output File: (.*?)\r?\n - Preset Used: (.*?)\r?\n(?: - Video: (.*?)\r?\n)?(?: - Audio: (.*?)\r?\n)?"
                 r"(?:.*?\r?\n){0,3}?(\d\d/\d\d/\d{4} \d\d:\d\d:\d\d [AP]M) : (File Successfully Encoded|Encoding Failed)", re.S)

def read_log(path):
    raw = open(path, "rb").read()
    text = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", errors="replace")
    return [dict(source=m[1].strip(), output=m[2].strip(), preset=m[3].strip(), video=(m[4] or "").strip(), when=m[6])
            for m in REC.finditer(text) if m[7].startswith("File")]

def log_duration(video_line):
    m = re.search(r"(\d\d):(\d\d):(\d\d)[:;](\d+)\s*$", video_line or "")
    if not m: return None
    fps = re.search(r"([\d.]+) fps", video_line); fps = float(fps.group(1)) if fps else 30.0
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / max(fps, 1)

def duration(path):
    try: return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60).stdout)
    except Exception: return None

def root_of(parts):
    """Folder that gets the sources.json: the dated batch, else the folder under the client, else the client."""
    if parts[0] == "media":
        for i, p in enumerate(parts[:-1]):
            if DATED.match(p): return parts[:i + 1]
        return parts[:3] if len(parts) > 3 else parts[:2]
    return parts[:2] if len(parts) > 2 else parts[:1]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drive"); ap.add_argument("--log"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(); root = os.path.abspath(a.drive)
    log = a.log or next(iter(sorted(glob.glob(os.path.expanduser("~/Documents/Adobe/Adobe Media Encoder/*/AMEEncodingLog.txt")), reverse=True)), None)
    if not log or not os.path.exists(log): sys.exit("Media Encoder log not found; pass --log")
    records = read_log(log); by = collections.defaultdict(list)
    for r in records: by[os.path.basename(r["output"]).lower()].append(r)
    print(f"{len(records)} successful encodes in {log}")
    files = []
    for top in ("media", "stock", "stock_footage"):
        for d, ds, fs in os.walk(os.path.join(root, top)):
            ds[:] = [x for x in ds if not x.startswith(".")]
            files += [os.path.join(d, f) for f in fs if not f.startswith(".") and f.lower().endswith(EXT)]
    with ThreadPoolExecutor(8) as ex: durs = dict(zip(files, ex.map(duration, files)))
    rows = []
    for p in files:
        rel = os.path.relpath(p, root); parts = rel.split(os.sep); batch = next((x for x in parts if DATED.match(x)), parts[-2])
        cands = by.get(os.path.basename(p).lower(), []); D = durs[p]; how, pick, amb = "not in log", None, None
        if cands:
            near = [r for r in cands if D is not None and log_duration(r["video"]) is not None and abs(log_duration(r["video"]) - D) <= max(0.6, 0.0015 * D)]
            srcs = {r["source"] for r in near}
            if len(srcs) == 1: pick, how = near[-1], "name + duration"
            elif len(srcs) > 1:
                d = DATED.match(batch); d = d.group(0) if d else None
                dated = [r for r in near if d and d in (r["source"] + " " + r["output"])]
                if len({r["source"] for r in dated}) == 1: pick, how = dated[-1], "name + duration + folder date"
                else: how, amb = "ambiguous", sorted(srcs)
            elif len({r["source"] for r in cands}) == 1: pick, how = cands[-1], "name only"
            else: how, amb = "ambiguous", sorted({r["source"] for r in cands})
        rows.append(dict(path=rel, how=how, source=pick["source"] if pick else None, preset=pick["preset"] if pick else None, encoded=pick["when"] if pick else None, candidates=amb))
    print(dict(collections.Counter(r["how"] for r in rows)))
    if a.dry_run: return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"); groups = collections.defaultdict(list)
    for r in rows:
        parts = r["path"].split(os.sep); ro = root_of(parts); groups[os.sep.join(ro)].append((os.sep.join(parts[len(ro):]), r))
    for folder, items in groups.items():
        doc = {"schema_version": "1.0", "generated_at": now, "generated_from": "Adobe Media Encoder encoding log, verified against clip duration", "folder": folder,
               "note": "Maps each proxy or transcoded file to the original it was made from. Volume names are the drive labels at encode time.",
               "files": {}, "ambiguous": {}, "not_in_log": []}
        for rel, r in sorted(items):
            if r["source"]:
                v = r["source"].split("/")
                doc["files"][rel] = {"source_file": os.path.basename(r["source"]), "source_volume": v[2] if r["source"].startswith("/Volumes/") else None,
                                     "source_path": r["source"], "preset": r["preset"], "encoded_at": r["encoded"], "match": r["how"]}
            elif r["how"] == "ambiguous": doc["ambiguous"][rel] = r["candidates"]
            else: doc["not_in_log"].append(rel)
        json.dump(doc, open(os.path.join(root, folder, "sources.json"), "w"), indent=1, ensure_ascii=False)
    os.makedirs(os.path.join(root, "_index"), exist_ok=True)
    with open(os.path.join(root, "_index", "sources_all.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["path", "source_volume", "source_path", "preset", "encoded_at", "match"])
        for r in sorted(rows, key=lambda z: z["path"]):
            s = r["source"] or ""; w.writerow([r["path"], s.split("/")[2] if s.startswith("/Volumes/") else "", s, r["preset"] or "", r["encoded"] or "", r["how"]])
    print(f"wrote {len(groups)} sources.json files and _index/sources_all.csv")

if __name__ == "__main__":
    main()
