---
name: footage-describe
description: Write the media-library metadata JSON for raw footage (visual shot log, audio description, tags, transcript, people) with Claude Opus 5.5 at xhigh effort plus a verify pass, one request per clip, synchronously. Use when someone says "describe this footage", "catalogue these clips", "scrub this batch", "write the JSON for these videos", "make the metadata for", or points at a media-root batch folder. For more than about five clips or an overnight run, use footage-describe-batch instead.
---

# footage-describe

For every video in a batch folder this writes `<clip>.json` next to the video, following `templates/video_metadata_template.json`. Identity and technical fields are filled by script from ffprobe, the proxy's XMP packet (timecode) and the folder name, no model involved. Camera clocks are recorded but never trusted; the shoot date comes from the directors note. The content fields come from one Opus 5.5 xhigh request with a fixed frame set (16-frame overview plus contact sheets every two seconds, brightened copies added for dark clips), then an Opus 5.5 xhigh verify request that corrects claims the frames do not support. About $0.37 per clip at list prices, 60 seconds to 3 minutes per clip. Opus 5.5 at xhigh replaced Opus 5 on 2026-09-22 after a blind benchmark; lower efforts score worse and save little, because the frame images dominate the cost.

## Before running

1. Target. A batch folder `media/<client>/YY.MM.DD.HH-description`, a single clip, or a client folder (every dated batch inside it; `_music`-style folders, `audio/` and `outputs/` are skipped). `client` and `project` in the JSON come from the client folder. Confirm which one with the user.
2. Directors note. The folder must contain `directors_note.md`. If it does not, ask the user for the location, one or two lines on what the shoot was, and the names of key people, then write it from `templates/directors_note_template.md` before running. Key people are optional: without them people are labelled `person_1`, `person_2` with a clothing-and-role `descriptor`, and nobody is ever identified from a face. A name is used only when the note ties it to something visible or audible. `media/<client>/client_note.md` is appended to every batch note. Subtitle files in `transcripts/` named after the clip are passed to the model as supplied transcripts. A `**Language(s):** es` line tells whisper which languages to expect; when auto-detection disagrees, forced passes in those languages are added.
3. Environment. `<skill>/venv` must exist. If not, run `<skill>/setup.sh` (add `--with-whisper` on Apple Silicon for on-device transcripts). ffmpeg must be installed.
4. Sync. If the batch has recordings under `audio/`, run footage-sync on it first so `sync_map.json` exists; the script refuses otherwise. Report batches where most clips only match weakly before describing them.
4. Auth. `ant auth status` must show a login, or `ANTHROPIC_API_KEY` must be set. If neither, tell the user to run `ant auth login` or set their own key, and stop. Get the account owner’s approval before incurring API costs; estimate with --dry-run where supported.
5. Clips that already have a JSON are skipped unless `--force`.

## Run

```
<skill>/venv/bin/python <skill>/scripts/catalog.py <batch-folder-or-clip>
```

Options: `--dry-run` (prepare inputs and estimate tokens, no API calls), `--claude-code` (see below), `--no-verify`, `--transcribe off`, `--language es` (force the whisper language), `--out DIR`, `--force`, `--keep-work`. With `--claude-code`, `--answered-by <model>` and `--answered-effort <level>` record the model that actually answered the requests (for example `--answered-by gpt-6-astra --answered-effort high` when Codex answers them); pass them on the run that finalizes the clip. The default label is `claude-code`.

Run it in the background for more than two clips and wait for it to finish rather than polling.

## Claude Code mode (subscription, no API)

When the API budget is off the table, run the model step inside Claude Code instead. The scripts still do everything deterministic locally (frames, contact sheets, transcripts, technical fields, stills, merging) and a Claude Code agent does the looking. Nothing is sent to the Messages API and the run log records `$0`.

```
<skill>/venv/bin/python <skill>/scripts/catalog.py <batch-folder-or-clip> --claude-code
```

The command is re-entrant. Each run moves every clip one state forward and prints what is pending:

1. First run: prepares the clip and writes `outputs/_describe_work/<clip>/describe_request.json` (the system text, the context, the sheet image paths, the JSON schema, and where to write the answer).
2. For every pending request, launch one `footage-describer` subagent (Agent tool, `subagent_type: footage-describer`) with the request path as the prompt. That agent is defined in `~/.claude/agents/footage-describer.md` (copy in `scripts/footage-describer.agent.md`; install it once per machine) and pins Opus at xhigh effort, so the chat's own effort setting does not leak into describing. Without it, a `general-purpose` agent with `scripts/claude_code_prompt.md` works but runs at the session's effort. The agent reads the sheets with the Read tool and writes `describe_response.json`. Run several agents in parallel, one per clip.
3. Second run: applies the description and writes `verify_request.json`. Same agent loop for the verify pass.
4. Third run: writes `<clip>.json`, the reference stills and the run log, and clears the work folder.

Usage tracking (every batch, instead of a written report):
- Before pass 1, read the plan's usage (the Claude desktop app's usage tool, or `/usage` in a terminal session) and note the weekly and 5-hour percentages.
- As each agent's completion notice arrives, append one line to `<batch>/outputs/describe_runs/agent_tokens.tsv`: `<clip> <describe|verify> <subagent_tokens> <duration_ms>`.
- After pass 3 (and people-link, if run), read the usage again and record the batch: `python3 tools/usage_ledger.py record <batch> --who <name> --plan <plan> --effort xhigh --transcribe on|off --wall-seconds N --weekly-before P --weekly-after P --five-hour-before P --five-hour-after P`. That writes `usage/describe/<client>__<batch>.json` in the repo; keep it with the batch's private records. `python3 tools/usage_ledger.py summary --drive /Volumes/media-root` prints tokens per clip, measured weekly percentage per clip, and the projection for what is left.
- No per-batch report file; the ledger is the record.

Transcription: pass `--transcribe off` when a batch is primarily b-roll (`**Language(s):** none`, or an Overview with no interviews or dialogue). Whisper otherwise dominates pass 1 on quiet clips for nothing.

Concurrency: Claude Code runs at most 20 subagents at once (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`). For batches over 20 clips, launch 20, then start one more each time an agent finishes. Pass 2 can run as soon as some describes are in: it writes verify requests for the finished clips and leaves the rest pending, so verify agents can fill free slots while the last describes finish.

Rules for the orchestrator: never answer a request yourself in the main conversation, always through a subagent, so the frames do not land in the main context; never open a browser or preview while agents are running (an earlier run picked up a stray screenshot); a malformed response file is moved aside as `.bad` and the request stays pending, so just launch that agent again. Quality equals the API path when the agent is an Opus model. Always use the `footage-describer` agent: on 2026-09-23 it described 29 short clips at about 52,000 subscription tokens per clip (about 28,000 describe, 24,000 verify), against about 150,000 per clip for the same work through `general-purpose` agents, because a general-purpose agent loads every tool definition and a long system prompt for each clip while footage-describer carries only Read, Write and the brief. Its prompt is just the request path. The saving is in token counts, not in plan limits: most of what a general-purpose agent spends extra is cached setup text, which counts lightly against the plan, and over the first 62 clips on footage-describer the weekly limit moved about 0.065% per clip against about 0.057% before (whole-percent readouts, so within noise). Budget on about 0.06% of the weekly limit per clip either way; the agent's real gains are speed and a fixed xhigh effort. Long clips still cost more (a four-minute take with 34 sheets used about 300,000 tokens on the old agent); pace batches against the plan's limits and run `--no-verify` only when the footage is bright and simple.

## Transcripts

- Existing `outputs/transcripts/<clip>_*.srt` next to the clip are used when present.
- An auto-detect pass runs first. If the note lists `**Language(s):**` and the detected language is not among them, a forced pass per listed language is added and both are given to the model.
- Otherwise on-device whisper runs if available: the compatible external transcriber (`FOOTAGE_TRANSCRIBER_DIR` or `~/footage-transcriber`), or `mlx-whisper` inside the skill venv. Silent clips are skipped.
- Without a transcriber the description is written without speech and audio_description says so.

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

Per clip: cost, number of corrections, and the model's `notes` field from `outputs/describe_runs/<clip>.run.json` (what it was unsure about). Flag any clip whose people keys or transcript look wrong. JSONs, directors notes and transcripts belong in your private media catalogue; the `outputs/_describe_work/` frames are deleted after each run.

## Known limits

- Dark clips: the model cannot crop or zoom, so small text and direction of movement can be misread. Keep the verify pass on.
- Do not swap in Sonnet for the description. In the September 2026 benchmark it fabricated footage details at every effort level.
- The model cannot hear audio. audio_description is inferred from the transcript and loudness statistics.

## Files

`scripts/catalog.py` (runner), `scripts/pipeline.py` (frames, prompts, cost), `scripts/transcribe.py`, `scripts/transcribe_audio.py` (bundled on-device whisper), `setup.sh`, `templates/`.
