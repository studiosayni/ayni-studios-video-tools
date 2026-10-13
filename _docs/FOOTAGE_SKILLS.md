# Footage workflow

## Installation and authentication

Start with the root README. Run each skill's setup.sh using zsh to create its local venv. Install ffmpeg and Python first. footage-sync needs numpy; the describe skills need Pillow and the Anthropic SDK. The setup scripts install these dependencies. Add --with-whisper for MLX Whisper on Apple Silicon. batch-notes reuses the footage-describe environment.

Use your own ANTHROPIC_API_KEY for API description, batch description, people-link, graphics description, and transcript refresh. The scripts also support an existing ant CLI login. Music and sound-effect listening uses GEMINI_API_KEY (or GOOGLE_API_KEY). Never put credentials in tracked files. API billing and subscription usage are separate; use the request-file workflow when your agent environment supports it.

## Prepare

Keep media outside this clone. A batch is media/<client>/YY.MM.DD.HH-description with videos at its root and recordings under audio/<device>/. tools/new_batch.py scaffolds the folders. Run tools/validate.py before processing; use --help for options.

Fill directors_note.md with shoot date, location, context, languages, and audio roles. A client_note.md in the parent client directory provides shared context; the batch note overrides it. batch-notes can collect this through conversation. People are optional and names must come from a human.

## Sync first

```sh
.claude/skills/footage-sync/venv/bin/python .claude/skills/footage-sync/scripts/sync.py /path/to/batch
```

Recordings are aligned first, then camera clips are placed on the recording timeline. Additional recorder directories can be passed with repeated --audio arguments. Review sync_map.json and sync_timeline.fcpxml. Silent camera audio and weak correlations require manual review. Recorder filenames, dates, and camera clocks are not reliable sync evidence.

Basic sync does not call a model. It writes recording metadata, a sync map, and a Final Cut timeline. --relink updates existing clip JSONs. For a batch described before sync, --relink --refresh-transcripts repairs transcripts and audio fields; this mode can call the API.

## Describe and verify

```sh
.claude/skills/footage-describe/venv/bin/python .claude/skills/footage-describe/scripts/catalog.py /path/to/batch --dry-run
.claude/skills/footage-describe-batch/venv/bin/python .claude/skills/footage-describe-batch/scripts/catalog_batch.py /path/to/batch --dry-run
```

A description dry run prepares inputs and estimates cost without sending model requests; it can write working files. Remove --dry-run only when ready for a paid run. Existing clip JSONs are skipped unless --force is used. Keep verification enabled. See each SKILL.md for full options and the multi-pass --claude-code request-file mode.

Output is <clip>.json next to each video. Transcripts and run logs live under outputs/. Synced recordings are transcribed once and sliced into clip time. Without a transcriber, do not expect speech transcripts. The visual describer infers audio descriptions from transcripts and signal statistics; it does not hear the video soundtrack.

## Review and link

Review uncertain shot descriptions, timestamps, transcripts, rights, and sync placements. people-link groups clothing/context descriptors across clips and produces a review sheet; a person supplies names and corrects grouping. asset-describe handles music, sfx, and graphics separately.

Keep catalogue records private. tools/usage_ledger.py can track local run usage. tools/publish.py can copy records into a separate private Git catalogue; specify --repo explicitly and inspect its dry run first. Never target this public toolkit with production records.

## Troubleshooting

- Missing ffmpeg: install ffmpeg and ensure ffprobe is also on PATH.
- Missing transcripts: install the relevant skill with --with-whisper on Apple Silicon. An optional compatible external transcriber can be supplied with FOOTAGE_TRANSCRIBER_DIR; no private repository is required.
- Authentication failure: configure your own provider credentials or supported CLI login.
- Unavailable model or effort: inspect the skill's configuration and your provider's supported values before a paid run. Inherited prices and timing estimates are historical, not guarantees.
- needs_review sync: inspect the review list and verify by ear. Include weak matches only after a person confirms them.
- Wrong transcript source: check Audio sources in the note and the --primary device override.
- Interrupted batch: preserve outputs/describe_runs/ and rerun the same command to resume submitted batches; inspect failures before forcing a retry.
