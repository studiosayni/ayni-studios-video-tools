# Ayni Studios Video Tools

AI skills and Python tools for syncing production audio, describing video footage, and building a searchable media catalogue. Built by Ayni Studios.

The workflow is **notes → sync → describe → review**. Sync aligns recordings on a shared clock, places camera clips against those recordings, and exports a Final Cut Pro timeline. Description produces timestamped shot logs, transcripts, tags, people descriptors, and structured JSON beside your clips.

| Skill | What it does |
| --- | --- |
| [footage-sync](.claude/skills/footage-sync/SKILL.md) | Waveform-based recorder-first sync, sync maps, and FCPXML timelines |
| [footage-describe](.claude/skills/footage-describe/SKILL.md) | Describe clips with a verification pass; API or agent request-file workflow |
| [footage-describe-batch](.claude/skills/footage-describe-batch/SKILL.md) | Process larger collections through Anthropic's Batch API |
| [batch-notes](.claude/skills/batch-notes/SKILL.md) | Capture shoot context and audio-source roles through conversation |
| [people-link](.claude/skills/people-link/SKILL.md) | Link people by clothing and context, then apply human-supplied names |
| [asset-describe](.claude/skills/asset-describe/SKILL.md) | Catalogue music, sound effects, and graphics |

## Quick start

The setup scripts target macOS with zsh, Python 3, and ffmpeg. Optional on-device transcription uses MLX Whisper on Apple Silicon. Other platforms have not been validated for this public package.

```sh
git clone https://github.com/studiosayni/ayni-studios-video-tools.git
cd ayni-studios-video-tools
brew install ffmpeg python
sh tools/setup_repo.sh
zsh .claude/skills/footage-sync/setup.sh
zsh .claude/skills/footage-describe/setup.sh --with-whisper
```

Omit `--with-whisper` if you do not need local transcription or are not on Apple Silicon. The first transcription downloads model weights. Each additional skill with a `setup.sh` has its own environment; `batch-notes` uses the footage-describe environment.

Keep footage in a separate working directory, for example:

```text
/path/to/media-drive/media/example-client/26.10.13.09-interview/
├── directors_note.md
├── camera-001.mov
└── audio/
    └── lav1/
        └── recording-001.wav
```

Copy [the directors note template](templates/directors_note_template.md) into the batch and fill it in. Include the language and audio roles, for example `**Audio sources:** main: lav1, scratch: camera; ambience: camera`.

```sh
BATCH="/path/to/media-drive/media/example-client/26.10.13.09-interview"
.claude/skills/footage-sync/venv/bin/python .claude/skills/footage-sync/scripts/sync.py "$BATCH"
.claude/skills/footage-describe/venv/bin/python .claude/skills/footage-describe/scripts/catalog.py "$BATCH" --dry-run
```

Review the sync results before describing. To describe through the API, set `ANTHROPIC_API_KEY` in your environment and repeat the description command without `--dry-run`. API processing incurs charges and sends the required frames, text, or audio to the configured provider. Basic waveform sync runs locally without model calls; transcript refresh is a separate API-backed option.

For Claude Code, open this repository and ask to sync or describe a specific batch; the skills live in `.claude/skills/`. For its request-file mode, install the supplied agent:

```sh
mkdir -p ~/.claude/agents
cp .claude/skills/footage-describe/scripts/footage-describer.agent.md ~/.claude/agents/footage-describer.md
```

Then follow [the subscription workflow](.claude/skills/footage-describe/SKILL.md). `--claude-code` prepares requests for an agent to answer; it does not automatically perform the visual description. Other coding agents can read [AGENTS.md](AGENTS.md) and the skill instructions and use the same scripts and request files.

## Documentation

- [Workflow, setup, and troubleshooting](_docs/FOOTAGE_SKILLS.md)
- [Folders, metadata, and sync conventions](_docs/FOLDER_ARCHITECTURE.md)
- [Design findings and limitations](_docs/FINDINGS.md)
- [Templates](templates/) and [drive utilities](tools/)
- [Contributing](CONTRIBUTING.md)

This is a standalone source snapshot. It contains no footage, client catalogues, credentials, production usage records, or upstream Git history. Keep generated records in your own private catalogue. `tools/publish.py` requires `--repo /path/to/private-catalogue` for that workflow.

Model identifiers, effort settings, price estimates, and timings in the inherited skills reflect the source implementation. Confirm provider availability and configuration before paid runs. The public packaging has been checked locally; paid API runs and Final Cut import have not been revalidated for this release.

## License

[MIT](LICENSE). Third-party packages and services retain their own terms.
