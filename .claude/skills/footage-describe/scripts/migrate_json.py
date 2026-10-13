#!/usr/bin/env python3
"""Bring every clip and recording JSON under a folder up to the current schema in place. Safe to re-run.
usage: migrate_json.py FOLDER [FOLDER ...] [--dry-run]"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schema

AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".caf"}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("folders", nargs="+"); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(); n = 0
    for folder in a.folders:
        for root, dirs, files in os.walk(os.path.abspath(folder)):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("outputs", "_describe_work", "venv")]
            for f in files:
                if not f.endswith(".json") or f in ("sync_map.json",) or f.endswith((".run.json", ".dryrun.json")): continue
                p = os.path.join(root, f)
                try: j = json.load(open(p, encoding="utf-8"))
                except Exception: continue
                if not isinstance(j, dict) or "file_name" not in j: continue
                stem = os.path.splitext(f)[0]
                is_rec = any(os.path.exists(os.path.join(root, stem + ext)) for ext in AUDIO_EXT) or "synced_videos" in j
                new = schema.migrate_recording(j) if is_rec else schema.migrate_video(j)
                if not new.get("client"): new["client"] = schema.client_of(p)
                if not new.get("project"): new["project"] = schema.project_of(new.get("batch"), p)
                if not is_rec and (new.get("location") is None or new.get("shoot_context") is None):   # 2.6: from the batch's directors note
                    note = os.path.join(root, "directors_note.md")
                    if os.path.exists(note):
                        loc, ctx = schema.note_fields(schema.with_client_note(open(note, encoding="utf-8").read(), root))
                        if new.get("location") is None: new["location"] = loc
                        if new.get("shoot_context") is None: new["shoot_context"] = ctx
                if new != j:
                    n += 1; print(f"{'would update' if a.dry_run else 'updated'} {p}")
                    if not a.dry_run: json.dump(new, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"{n} file(s) {'need updating' if a.dry_run else 'updated'}")

if __name__ == "__main__":
    main()
