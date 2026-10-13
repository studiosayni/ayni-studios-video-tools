---
name: footage-sync
description: Recorder-first sync for a media-root batch, the way the editor syncs a shoot. Lines up every recording (lavs, room recorders, the files a recorder split into when paused) on one clock, places every camera clip on that synced timeline, and writes sync_map.json, a Final Cut timeline (sync_timeline.fcpxml) and optionally the sync fields into existing clip JSONs. Use when someone says "sync the audio", "match the recorder files to the clips", "find the offsets", "which WAV goes with which clip", "make the synced timeline", "relink the sync fields", and always before running footage-describe on a batch that has recordings. No model calls, runs locally, free.
---

# footage-sync

Recordings live in `audio/<device>/` inside the batch folder (lav1, recorder1, unsorted). The sync follows the editor's own editing order:

1. **Recordings first.** Every recording is matched against every other in 2-minute pieces. Recordings that overlap (two lavs on one conversation, a room recorder) go on one clock as a **session**, with the offset and clock drift measured over the whole overlap. Every pair is tried, because device folders often hold several physical recorders (two lavs both filed under `recorder1`); files that never overlapped simply find no match. A link resting on a single piece needs two 30-second pieces inside the overlap to agree.
2. **Clips onto the session.** Each camera clip is scored against every recording of a session at once, on the session's clock, and placed where the combined score stands out from that clip's own background (z) and from the next-best position (ratio). Clips of 8 seconds or more must also have both halves land on the same position.
3. **Shooting order.** One camera's clips run in order and don't overlap, so a borderline placement that breaks its camera's order is demoted, and clips between two placed neighbours are searched only in that gap. Very strong placements are kept even when out of order, because file numbers restart on a second body or a new card.
4. **Sessions joined by clips.** A recorder paused and resumed leaves files that never overlap. A long take that runs across the pause shows where the two files sit; the parts of a placed clip that its session doesn't cover are matched against the other sessions, and a strong match joins them.

Matching uses onset envelopes (rises in log energy) in four speech bands (300 Hz to 4 kHz), 100 per second, with digital silence masked (recorder pauses, dropouts, the AAC priming at the head of a camera file). Wind, water and engines sit outside those bands. File-name dates, recorder metadata dates and camera clocks are never used: recorder clocks are set separately and can be a day or an hour off (iquitos: files named 18:00 and 19:01 started 132 s apart), and proxy timecode is record-run.

**Tested 2026-10-11 against ten of the editor's hand-synced Final Cut timelines** (110 clip/recording pairs, 7 recording pairs): 105 correct, 0 wrong, 5 missed (short clips sharing no sound with the lav). Three of the 105 disagreed with his timeline; by ear he confirmed the engine was right and his manual sync had slipped. Every recording-to-recording offset matched his within 10 ms. The previous clip-by-clip matcher got 65.

The directors note names the roles once per batch: `**Audio sources:** main: lav1, scratch: camera, recorder1; ambience: camera`. Without that line the recorders lead: a lav folder first, then a recorder folder, then any other folder of recordings; the camera is main only when there are no recordings or the note says so.

Offset convention: an offset is the position in the recording where the clip's first frame occurs (recording time = clip time + offset). Each clip also gets its `session` and `master_offset_s`, its position on the session's clock.

## Before running

Run this before footage-describe or footage-describe-batch on any batch with recordings under `audio/`; both refuse to describe such a batch until `sync_map.json` exists. For a batch that was already described without it, use `--relink --refresh-transcripts`.

A date mismatch between recorder files and the batch is not proof a recording belongs elsewhere; sync decides. Never pair, filter or move recordings by date.

1. Target: one or more batch folders under `media/<client>/`. Recorder files should sit in the batch folder or its `audio/` subfolder; otherwise pass `--audio <folder>`.
2. Environment: `<skill>/venv` must exist. If not, run `<skill>/setup.sh`. ffmpeg must be installed.
3. Nothing is sent anywhere. No login is needed.

## Run

```
<skill>/venv/bin/python <skill>/scripts/sync.py <batch-folder> [more folders] [--audio <recorder-folder>]
```

Add `--relink` to write `has_external_audio`, `sync_status` and the `sync_relationships` block into every `<clip>.json` that already exists. Confident placements only; add `--include-weak` after a person has confirmed the weak ones. Add `--refresh-transcripts` when the batch was described before its audio was synced: for clips with a confident recorder match, the main source is transcribed (recordings once each, sliced per clip in video time) and a text-only claude-sonnet-5 call rewrites `content.transcript`, `content.transcript_source`, `content.audio_description` and the speaker flags, about a cent per clip. Takes longer than 10 minutes (interviews), and any clip whose model refresh fails, get the main recording's own whisper transcript, sliced to the clip and shifted into clip time, with no model call. `--refresh-clips A,B` limits the refresh to named clips. `--primary <device>` overrides the note's main source. `--json-only` prints the result without writing.

Expect one to six minutes per batch; long lav files and hundreds of clips take longest.

## Outputs

- `sync_map.json`: `sessions` (each recording's start on its session clock), `recording_links` (offset, drift in ppm, how many pieces agreed, or the clips that bridged two sessions), and per clip `sync_status` (matched, needs_review, no_match), `session`, `master_offset_s`, `matches` (offset in every recording of the session it overlaps) and `sync_detail` (z, ratio, halves check, method).
- `sync_timeline.fcpxml`: the synced timeline for Final Cut Pro, laid out like the editor's sync projects. One project per session: recordings on audio lanes below (a device's files end to end), every placed clip connected above at its position. A second project holds the clips that couldn't be placed, in card order. It points at the media-root proxies and recordings and is rewritten on every sync; `scripts/timeline.py <batch>` rebuilds it from `sync_map.json` alone.
- `<recording>.json` next to every recording.

## Report back

Counts of matched, review and unmatched clips, the sessions (which recordings joined, and any link resting on a clip bridge or a short overlap), and the review list with reasons. Point the person at `sync_timeline.fcpxml` to check in Final Cut, and ask them to confirm weak matches by ear, then rerun with `--relink --include-weak` if they confirm.

## Schema notes

- Schema 2.6 (2026-09-22) adds search facets to clip JSONs (see `_docs/FOLDER_ARCHITECTURE.md`); recording JSONs are unchanged.

Relink migrates older clip JSONs to the current schema before writing. It fills `primary_audio`, `audio_sources`, `linked_camera_angles` with the offset at which each other angle starts in this clip's timeline, `method` and `synced_at`. Recording JSONs carry `channels_detail` from iXML, `recorder_model`, `bwf_time_reference`, size and hash.

## Files

`scripts/sync.py` (batch run, outputs), `scripts/engine.py` (features, sessions, placement), `scripts/timeline.py` (FCPXML), `scripts/refresh.py`, `setup.sh`.
