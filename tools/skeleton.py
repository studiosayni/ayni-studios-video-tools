#!/usr/bin/env python3
"""Give every dated batch the same subfolders (audio, images, people, transcripts) and file loose images and subtitle
files into them. Moves only, never overwrites, never deletes. Dry run unless --apply.

usage: skeleton.py /Volumes/media-root [--client NAME] [--apply]
"""
import argparse, csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medialib as ml

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drive"); ap.add_argument("--client"); ap.add_argument("--apply", action="store_true"); ap.add_argument("--log")
    a = ap.parse_args(); root = os.path.abspath(a.drive); media = os.path.join(root, "media"); made = 0; moves = []
    for c in ml.clients(media):
        if a.client and c != a.client: continue
        for b in ml.batches(os.path.join(media, c)):
            legacy = os.path.join(b, "people-id")
            if os.path.isdir(legacy) and not os.path.exists(os.path.join(b, "people")):
                moves.append((legacy, os.path.join(b, "people"), "renamed to the standard name"))
                if a.apply: os.rename(legacy, os.path.join(b, "people"))
            for d in ml.SKELETON:
                p = os.path.join(b, d)
                if not os.path.isdir(p) and not (d == "people" and os.path.isdir(legacy) and not a.apply):
                    made += 1
                    if a.apply: os.makedirs(p, exist_ok=True)
            for f in ml.visible(os.listdir(b)):
                src = os.path.join(b, f); ext = os.path.splitext(f)[1].lower()
                if not os.path.isfile(src): continue
                sub = "images" if ext in ml.IMAGE_EXT else "transcripts" if ext in ml.SUBTITLE_EXT else None
                if not sub: continue
                dst = os.path.join(b, sub, f)
                if os.path.exists(dst): print(f"skip, already there: {dst}", file=sys.stderr); continue
                moves.append((src, dst, f"loose {sub[:-1]} filed"))
                if a.apply: os.rename(src, dst)
    print(f"{'created' if a.apply else 'would create'} {made} folder(s); {'moved' if a.apply else 'would move'} {len(moves)} item(s)")
    if a.log and a.apply:
        with open(a.log, "a", newline="") as fh: csv.writer(fh).writerows([("move", os.path.relpath(s, root), os.path.relpath(d, root), w) for s, d, w in moves])

if __name__ == "__main__":
    main()
