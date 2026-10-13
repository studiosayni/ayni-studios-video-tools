---
name: footage-describe-batch
description: Write the media-library metadata JSON for a whole batch of raw footage through Anthropic's Batch API at half price, with Claude Opus 5.5 at xhigh effort plus a verify pass. Use when someone says "batch describe", "catalogue this whole card overnight", "run the footage JSONs through batch", "cheapest way to describe these clips", or wants more than about five clips described and can wait up to an hour. For a clip needed within minutes, use footage-describe instead.
---

# footage-describe-batch

Same output as footage-describe (`<clip>.json` next to each video, script-filled identity and camera fields, Opus 5.5 xhigh description plus Opus 5.5 xhigh verify), but every request goes through the Message Batches API: all describe requests in one batch, then all verify requests in a second batch. Half the list price: about $0.13 per clip on average, measured over 4,300 clips in October 2026 (about $0.09 plus $0.10 per minute of footage). Results arrive when the queue clears, usually within 20 minutes, at most an hour per batch. Frames travel inline, so a large card is split into several batches under the API's 256 MB cap, submitted together (50 MB uploads, retried) and polled until all end. A run that stops after submitting is resumable: rerun the same command, and the batches already submitted are collected from `outputs/describe_runs/_batch_<stage>.json` instead of being paid for twice. Batches have no refusal fallback, which does not matter for footage.

## Before running

1. Target. A batch folder `media/<client>/YY.MM.DD.HH-description`, several of them, or a client folder (every dated batch inside it; `_music`-style folders, `audio/` and `outputs/` are skipped). `client` and `project` in the JSON come from the client folder. Confirm with the user.
2. Directors note. Each folder must contain `directors_note.md`. If one is missing, ask the user for the location, one or two lines on the shoot, and key people's names, then write it from `templates/directors_note_template.md`. Include a `**Language(s):** es` line so whisper runs forced passes when auto-detection disagrees.
3. Environment. `<skill>/venv` must exist; if not, run `<skill>/setup.sh` (add `--with-whisper` on Apple Silicon for on-device transcripts). ffmpeg must be installed.
4. Auth. `ant auth status` must show a login, or `ANTHROPIC_API_KEY` must be set. If neither, tell the user to run `ant auth login` or set their own key, and stop. Get the account owner’s approval before incurring API costs; estimate with --dry-run where supported.
5. Clips that already have a JSON are skipped unless `--force`.
6. Sync. Every target batch with recordings under `audio/` needs `sync_map.json` from footage-sync first; the script refuses otherwise. Run footage-sync on those batches, report any where most clips only match weakly, then describe.

## Run

```
<skill>/venv/bin/python <skill>/scripts/catalog_batch.py <batch-folder> [<batch-folder> ...]
```

Always run it in the background and wait for completion; it polls the batch every 30 seconds and prints progress. Options: `--dry-run` (prepare and estimate, submit nothing), `--no-verify`, `--transcribe off`, `--language es`, `--poll 60`, `--out DIR`, `--force`, `--keep-work`.

Transcripts are found or made the same way as in footage-describe (existing SRTs, else on-device whisper when available, silent clips skipped). Frames for all clips are prepared up front, so a large card takes a few minutes before the first batch is submitted.

Takes with more than 128 contact sheets (about 17 minutes and up) run at high effort instead of xhigh: at xhigh a 34-minute take spent its whole output budget thinking and returned nothing.

## Audio sources and sync

The directors note names the recording systems once per batch, for example `**Audio sources:** main: lav1, scratch: camera, recorder1; ambience: camera`. Devices are the folder names under `audio/` plus `camera`. When the note names no main source, the recorders lead: a lav folder under `audio/` first, then a recorder folder, then any other folder of recordings; the camera is main only when the batch has no recordings or the note says so (a mic plugged straight into the camera). The transcript comes from the main source; if it is silent for a clip the next available source is used and `content.transcript_source` says which. `--primary <device>` overrides the note for one run.

If `sync_map.json` from footage-sync exists, recorder files are transcribed once each, sliced per clip with timestamps shifted into video time, and `has_external_audio`, `sync_status` and `sync_relationships` are filled. Sync comes first: when `audio/` holds recordings and the batch has no `sync_map.json`, the script stops and prints the footage-sync command, because without the map every transcript comes from the camera mic and the recorders (the lav dialogue) are never transcribed. `--no-sync-check` skips the stop; use it only when the recordings are not meant for these clips. A batch that was described before it was synced is repaired with `footage-sync --relink --refresh-transcripts` (about a cent per clip), not a re-describe. Existing JSONs are merged, never rewritten: usage history and sync fields survive a re-describe. `--include-weak-sync` also uses matches a person has confirmed by ear.

## Schema notes

- Schema 2.6 (2026-09-22) adds the search facets the model fills from the frames: `summary`, `subjects` (species, activities, objects, setting), `people_count`, `speech_kind`, `mood`, timestamped `beats` and `quality_flags`, plus top-level `location` and `shoot_context` from the directors note. Vocabularies and their meanings live in `schema.py`; the design is in `_docs/FOLDER_ARCHITECTURE.md`.

- `people` is an array of `{id, on_camera, voice_heard, canonical_name}`; a person fills `canonical_name` later and re-describing keeps it.
- `review`, `rights` and `usage` are maintained by people and survive a re-describe. Never overwrite them by hand-editing the skill.
- `content.shot` uses fixed vocabularies; `transcript_segments` is the transcript of record and `transcript` is derived from it.
- `--camera-id camA` records the camera on multi-camera shoots.
- `scripts/migrate_json.py <folder>` brings older clip and recording JSONs up to the current schema in place.

## Report back

Batch IDs and queue times, then per clip: cost at batch prices, corrections, and the model's `notes` from `outputs/describe_runs/<clip>.run.json`. If a request errored or expired, say which clips are missing and re-run with `--force` for those. JSONs, notes and transcripts belong in your private media catalogue.

## Known limits

Same as footage-describe: dark clips can be misread without crop or zoom, keep the verify pass on, do not swap in Sonnet, audio is inferred not heard.

## Files

`scripts/catalog_batch.py` (runner), `scripts/catalog.py` (shared helpers), `scripts/pipeline.py`, `scripts/transcribe.py`, `scripts/transcribe_audio.py`, `setup.sh`, `templates/`.
