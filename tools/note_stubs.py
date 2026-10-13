#!/usr/bin/env python3
"""Write a directors_note.md stub into every batch that lacks one, and a client_note.md stub into every client folder.

Stubs are pre-filled with what the folder already tells us: the date from the batch name, clip count, the audio devices
found under audio/, and the original folder names from sources.json (often the best hint of what the shoot was).
Everything a person has to supply is left blank. Each stub carries a "**Status:** stub" line so validate.py can tell
which notes still need a human. Existing notes are never touched.

usage: note_stubs.py /Volumes/media-root [--client NAME] [--dry-run]
"""
import argparse, collections, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medialib as ml

def source_hints(batch):
    try: files = (json.load(open(os.path.join(batch, "sources.json"), encoding="utf-8")).get("files") or {})
    except Exception: return [], []
    dirs = collections.Counter(); vols = collections.Counter()
    for e in files.values():
        p = (e.get("source_path") or "").split("/")
        if len(p) > 4: dirs["/".join(p[3:-1])] += 1
        if e.get("source_volume"): vols[e["source_volume"]] += 1
    return [d for d, _ in dirs.most_common(4)], [v for v, _ in vols.most_common(3)]

def batch_stub(b, client):
    name = os.path.basename(b); files = ml.visible(os.listdir(b))
    vids = [f for f in files if os.path.splitext(f)[1].lower() in ml.VIDEO_EXT]
    adir = os.path.join(b, "audio"); devices = [d for d in ml.visible(os.listdir(adir)) if os.path.isdir(os.path.join(adir, d))] if os.path.isdir(adir) else []
    yy, mm, dd = name[:2], name[3:5], name[6:8]
    date = f"20{yy}-{mm}-{dd}" if mm != "00" and dd != "00" else ""
    if devices: sources = f"main: {devices[0]}, scratch: camera" + ("".join(f", {d}" for d in devices[1:])) + "; ambience: camera"
    else: sources = "main: camera; ambience: camera"
    dirs, vols = source_hints(b)
    hint = ("Original folders: " + "; ".join(dirs) + (f" (on {', '.join(vols)})" if vols else "")) if dirs else ""
    return f"""# Directors Note

**Status:** stub
**Batch:** {name}
**Client:** {client}
**Shoot date(s):** {date}
**Location:**
**Language(s):**
**Audio sources:** {sources}

<!-- The Audio sources line above is a guess from the folders found. Confirm which device carried the usable dialogue.
     Location, Language(s) and Key people fall back to ../client_note.md when left blank here. -->

## Overview

{len(vids)} clip(s). {hint}

What was this shoot for? One or two lines.

## Key people

Optional. Left empty, the skills label people generically (person_1 plus a clothing descriptor, speaker 1 per recording).
To name someone, tie the name to something visible or audible: "- Alex: tan field shirt and cap; audio files starting Alex_".

-

## Notes

"""

def client_stub(client, nb):
    return f"""# Client Note

**Status:** stub
**Client:** {client}
**Owner:**
**Location(s):**
**Language(s):**

<!-- Shared context for all {nb} batch(es) of this client. The skills append this note to every batch note,
     and anything a batch note states itself wins. Keep it short: who, where, what languages. -->

## What this work is

One or two lines on the client and the project.

## Key people

Optional. People who appear across several batches, with their role. Left empty, the skills label people generically.

-

## Recurring locations

-
"""

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drive"); ap.add_argument("--client"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(); media = os.path.join(os.path.abspath(a.drive), "media"); nb = nc = 0
    for c in ml.clients(media):
        if a.client and c != a.client: continue
        cdir = os.path.join(media, c); bs = ml.batches(cdir)
        p = os.path.join(cdir, "client_note.md")
        if not os.path.exists(p):
            nc += 1
            if not a.dry_run: open(p, "w", encoding="utf-8").write(client_stub(c, len(bs)))
        for b in bs:
            p = os.path.join(b, "directors_note.md")
            if os.path.exists(p): continue
            nb += 1
            if not a.dry_run: open(p, "w", encoding="utf-8").write(batch_stub(b, c))
    print(f"{'would write' if a.dry_run else 'wrote'} {nb} batch note stub(s) and {nc} client note stub(s)")

if __name__ == "__main__":
    main()
