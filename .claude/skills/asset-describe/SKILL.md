---
name: asset-describe
description: Write the library metadata JSON for music, sound effects and graphics under libraries/ (technical fields by script, bpm and key locally, genre/mood/pacing/energy by Gemini listening to the audio, graphics described by Claude), one JSON per asset, rights left for a person. Use when someone says "tag this music", "catalogue the sfx", "describe these logos", "write the JSON for the library", or points at a libraries/ folder.
---

# asset-describe

For every audio, image or motion file under a library folder this writes `<asset>.json` next to it from the matching template in `templates/` (music, sfx, gfx). Three layers:

- Scripted, no model: ffprobe (duration, sample rate, bit depth, channels, codec, dimensions, alpha, frame rate), ffmpeg loudness (integrated LUFS, true peak), embedded tags (title, artist, album, bpm), sha256, and librosa bpm and key for music.
- Listening, Gemini Flash (music and sfx only): genre, mood, pacing, energy, vocal, instrumentation, structure with times, edit points, description, tags. Only the audio bytes, the file name and the schema are sent. Under a cent per track.
- Seeing, Claude Opus 5.5 high (graphics only): type, title, description, colours, readable text, dark and light background safety, tags. Motion graphics send four frames.

Rights are never filled by a model. The run ends with a list of assets whose `rights.license_type` is still empty so a person can fill them from receipts and licenses. Existing JSONs are merged: `rights`, `review`, `usage`, `notes` and any hand-filled field survive re-runs. Each library root gets an `index.json` (one row per asset) rebuilt after every run, and a run log lands in `<library>/outputs/asset_runs/`.

## Before running

1. Target. A file or folder under a library: `music_library/` or `music/`, `sfx/`, `gfx/` (under `libraries/` in the repo, at the root of the working drive), or a client-only `_music/`, `_sfx/`, `_gfx/` folder under `media/<client>/`. Audio outside those folders needs `--type music|sfx`. Confirm the target with the user.
2. Environment. `<skill>/venv` must exist; if not, run `<skill>/setup.sh`. ffmpeg must be installed.
3. Gemini. `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) must be exported in the shell, from Google AI Studio on the paid tier. Never read, print or handle the key value. Without it the run still writes every scripted field and says the listening fields are empty.
4. Claude. `ant auth status` must show a login, or `ANTHROPIC_API_KEY` must be set, for graphics. Without it graphics get scripted fields only.
5. Assets with an up-to-date JSON (same content hash, model fields present) are skipped unless `--force`.

## Run

```
<skill>/venv/bin/python <skill>/scripts/describe_assets.py <folder-or-file> [<more>]
```

Options: `--dry-run` (list the plan, no writes), `--type music|sfx|gfx`, `--no-listen` (skip Gemini), `--no-see` (skip Claude), `--no-analysis` (skip bpm and key), `--force`, `--no-index`, `--gemini-model` (default `gemini-3.8-flash`; if the API does not list that name the newest Flash model with the same version is used and named in the log), `--claude-model`, `--claude-effort`, `--max-minutes` (default 15: longer audio gets scripted fields only, since a three-hour field recording is not a library asset and would cost real money to send).

Run in the background for more than about ten assets.

## Vocabularies

Music `pacing`: slow, medium, fast, variable. `energy`: low, medium, high, building. Sfx `kind`: one-shot, bed, loop, sequence. `source_type`: recorded, designed, library. `category`: ambience, foley, impact, whoosh, ui, nature, animals, water, vehicles, crowd, mechanical, other. Gfx `type`: logo, lower-third, title, transition, overlay, template, icon, map, other. Enforced by schema on both models; free tags are lowercase.

## After running

Report the count, the cost, the model actually used, any assets whose model layer failed (scripted fields were still written), and the list with empty rights. Do not fill rights yourself.
