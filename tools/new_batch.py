#!/usr/bin/env python3
"""Create a correctly named batch folder with its audio device folders and a directors note stub.

usage: new_batch.py /Volumes/media-root CLIENT "short description" --date 2026-09-20 --hour 8 [--devices lav1 lav2 recorder1] [--language es] [--location "Example location"]

The description is lowercased and hyphenated for you. Nothing is overwritten. A new client folder is created only with --new-client.
"""
import argparse, datetime, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medialib as ml

def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s: sys.exit("description is empty after cleaning")
    return s

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drive"); ap.add_argument("client"); ap.add_argument("description")
    ap.add_argument("--date", required=True, help="shoot date, YYYY-MM-DD"); ap.add_argument("--hour", type=int, required=True, help="hour the shoot started, 0-23")
    ap.add_argument("--devices", nargs="*", default=[], help="audio device folders to create under audio/"); ap.add_argument("--main", default="camera", help="main audio source (default camera: the mounted shotgun)")
    ap.add_argument("--language", default=""); ap.add_argument("--location", default=""); ap.add_argument("--new-client", action="store_true")
    a = ap.parse_args()
    d = datetime.date.fromisoformat(a.date)
    if not 0 <= a.hour <= 23: sys.exit("--hour must be 0-23")
    if not ml.CLIENT.match(a.client): sys.exit("client must be lowercase words joined by hyphens")
    cdir = os.path.join(os.path.abspath(a.drive), "media", a.client)
    if not os.path.isdir(cdir):
        if not a.new_client: sys.exit(f"no client folder {cdir}. Existing: {', '.join(ml.clients(os.path.dirname(cdir)))}. Pass --new-client to create it.")
        os.makedirs(cdir)
    name = f"{d:%y.%m.%d}.{a.hour:02d}-{slug(a.description)}"; assert ml.BATCH.match(name), name
    b = os.path.join(cdir, name)
    if os.path.exists(b): sys.exit(f"{b} already exists")
    os.makedirs(b)
    for subdir in ml.SKELETON: os.makedirs(os.path.join(b, subdir))
    for dev in a.devices:
        dev = slug(dev); os.makedirs(os.path.join(b, "audio", dev), exist_ok=True)
    others = [slug(x) for x in a.devices if slug(x) != a.main]
    scratch = ", ".join(([] if a.main == "camera" else ["camera"]) + others)
    sources = f"main: {a.main}" + (f", scratch: {scratch}" if scratch else "") + "; ambience: camera"
    note = f"""# Directors Note

**Batch:** {name}
**Client:** {a.client}
**Shoot date(s):** {d.isoformat()}
**Location:** {a.location}
**Language(s):** {a.language}
**Audio sources:** {sources}

## Overview

What was this shoot for? What's the story or goal?

## Key people

Optional. Leave empty and the skills label people generically (person_1 with a clothing descriptor, speaker 1 per recording);
rename them later through canonical_name in the JSONs. If you do name someone, tie the name to something visible or audible:
"- Alex: tan field shirt and cap; audio files starting Alex_". Reference photos go in people/ for editors; the skills never read them.

-

## Shot coverage

What was captured, and what to expect across this batch.

## Notes

Anything worth flagging for post: gear issues, lighting, continuity.
"""
    open(os.path.join(b, "directors_note.md"), "w", encoding="utf-8").write(note)
    print(b); print("  " + "/  ".join(ml.SKELETON) + "/"); print("  directors_note.md" + "".join(f"\n  audio/{slug(x)}/" for x in a.devices))

if __name__ == "__main__":
    main()
