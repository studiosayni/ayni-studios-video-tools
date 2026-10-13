---
name: batch-notes
description: Fill the directors notes and client notes on the media drive by asking the user questions in chat and writing the answers into the files, so nobody has to open a text editor. Use when someone says "fill the notes", "let's do the directors notes", "walk me through the batch notes", "confirm the stubs", "client note for X", or before describing a client whose notes are still stubs. One batch or one client at a time; shows a preview sheet of the batch first.
---

# batch-notes

The describe skill reads each batch's `directors_note.md` and the client's `client_note.md` before it looks at the frames. This skill fills them through conversation. `scripts/notes.py` reads the current note and the folder's hints, builds a preview sheet, and writes answers back without touching anything else in the note.

Uses the footage-describe environment: `<skills>/footage-describe/venv/bin/python <skill>/scripts/notes.py ...` (needs Pillow and ffmpeg, both already there).

## Order of work

1. Client notes first, one client at a time. Run `show-client <drive> <client>`, then ask the client questions below. Write with `write-client ... --confirm`.
2. Then batches, in the order `list <drive> --client <client>` prints (oldest first). For each: run `show <batch>`, Read the preview sheet at `<batch>/outputs/note_preview.jpg` and say in one or two lines what the frames show so the user can place the shoot without opening anything, then ask the batch questions. Write with `write ... --confirm`.
3. Stop after each note is written and move to the next only when the user answers. Never invent an answer; an empty field falls back to the client note, and a wrong note misleads the describer more than an empty one.

## Client questions (ask all at once, numbered, the user answers in one message)

1. Owner: who describes this client, you or your colleague?
2. Languages spoken on these shoots (codes or names, e.g. `es`, `en, ar`).
3. Location(s), one line: country, region, the usual sites.
4. What this work is, one or two lines: who the client is and what the project was for. (Skip if the note already has it.)
5. Key people who recur across batches: name and role, only the main ones. Optional.

## Batch questions (ask all at once, numbered; show the current value or guess next to each)

1. Shoot date, if the folder date is a placeholder or wrong. Otherwise skip.
2. Location, if it differs from the client note.
3. Language(s), if they differ from the client note.
4. Audio sources: confirm the guess (`main: recorder1, scratch: camera; ambience: camera`) or correct it. Devices are the folders under `audio/` plus `camera`.
5. Overview, one or two lines: what was this shoot for, what happens in it, anything the frames would not tell you (who the people are by role, what the event was, the story beat).
6. Key people, optional: name plus something visible ("Alex: tan shirt and cap") or the audio files that are theirs.
7. Notes for post, optional: gear problems, continuity, things to avoid.

Accept short answers. "same" means keep the client value (leave the field empty). "skip" means leave it. Turn a list answer into bullets: people are separated by `|` or new lines, so "Alex: tan shirt; audio files Alex_" stays one bullet. Pass everything to `write` in one call, quote the values, then confirm the note is written and show `list` for what is left.
