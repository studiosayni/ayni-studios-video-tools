# Folders and metadata

Media lives outside this public clone, under media/<client>/YY.MM.DD.HH-description. Use lowercase hyphenated client and shoot names. Each batch contains videos, directors_note.md, audio/<device>/, images/, people/, transcripts/, and generated outputs/. Client-wide context belongs in media/<client>/client_note.md.

The templates directory defines video, recording, people, music, sound-effect, and graphics records. video_metadata_example.json is fictional. Recording files receive their own metadata JSON after sync. Each clip is identified by client + batch + filename, with a content hash to distinguish duplicates.

Description fields include timestamped visual logs, audio summaries, tags, transcripts, people descriptors, and search facets: summary, subjects, setting, people_count, speech_kind, mood, beats, and quality_flags. The schema.py modules define their vocabularies. Search-service integrations and finished-film tooling are outside this toolkit's scope.

Rights, review, canonical names, and usage belong to people and survive re-description. Keep these records in a private catalogue. Shared assets can use libraries/music_library/, libraries/sfx/, libraries/gfx/, and libraries/stock_footage/ on your working drive; a folder name does not establish reuse rights.

## Audio sync fields and schema 2.1 to 2.2 (September 2026)

Schema 2.2 adds `client` to clip and recording JSONs and derives `project` from the client folder. `migrate_json.py` fills both on older files. Schema 2.5 adds `person_key`, `best_seen_seconds` and `reference_still` to each `people` entry and `person_key` to a recording's `speaker`. Schema 2.4 adds a `descriptor` to each `people` entry and a `speaker` block to recording JSONs . Schema 2.3 adds `proxy_source` (volume, path, preset, match) to clip JSONs, read from the batch's `sources.json`.

### Source map

The proxies do not record where their originals live. `tools/build_source_map.py <drive>` rebuilds that link from Adobe Media Encoder's encoding log: it matches every file under `media/` and `stock/` by name, confirms the match against the clip's duration, and writes `sources.json` into each batch folder plus `_index/sources_all.csv` for the whole drive. Each entry gives the original's file name, drive label, full path, the preset used and how the match was made (`name + duration`, `name + duration + folder date`, or `name only` for audio, which the log records without a duration). Files it cannot place are listed under `ambiguous` with their candidates or under `not_in_log`. Re-run it after every transcode session. The describe skills copy the entry into `proxy_of` and `proxy_source`, so an editor or the sync skill can find the original for any clip.

Batches with separate audio keep recordings in `audio/<device>/` inside the batch folder (`audio/lav1/`, `audio/recorder1/`). The folder name is the device name used everywhere else. The directors note names the roles once per batch:

```
**Audio sources:** main: lav1, scratch: camera, recorder1; ambience: camera
```

`main` feeds the transcript, `scratch` are backups, `ambience` are tracks kept for layering (the camera track usually). Any device can be main, including the camera.

### Clip JSON (`templates/video_metadata_template.json`)

- `schema_version`, `generated_at`, `generated_by` (skill, model, effort, verify model and effort): what wrote the file.
- `file_size_bytes`, `content_hash` (sha256), `proxy_of` (source file name when the transcoder recorded it): identity that survives renames and re-transcodes.
- Quick filters at the top: `has_external_audio` (true, false, or null when sync has not run), `sync_status` (`not_run`, `matched`, `needs_review`, `no_match`), `has_speech`.
- `camera_metadata`: technical facts from the file plus `camera_id` for multi-camera shoots; `date_recorded` from the directors note; `camera_clock_time` and `timecode_start` kept but never trusted as real time (`clock_trusted` stays false, `timecode_note` says where the timecode came from).
- `camera_audio`: the camera track's channels, sample rate, bit depth, codec, mean level and a silent flag.
- `content`: `visual_description`, `audio_description`, `tags`, a `shot` block with fixed vocabularies (`shot_type`, `camera_movement`, `lighting`, `time_of_day`, `interior_exterior`), `transcript_segments` (start, end, text, speaker) with `transcript` derived from them, `transcript_language`, `transcript_source` (device name), and `notes` (what the model was unsure about). Schema 2.6 adds the search facets listed in the overview above.
- `location` and `shoot_context` (2.6): the directors note's Location line and Overview paragraph, copied by script so a search can read where and why the clip was shot without the note.
- `people`: an array of `{id, on_camera, voice_heard, canonical_name}`. Ids are generic descriptors or the names from the note; `canonical_name` is filled in by a person later without changing the id.
- `sync_relationships`: `primary_audio` (file, device, offset), `linked_camera_angles` with the offset at which each other angle starts in this clip's timeline, `visual_sync_group`, `audio_sources` (every recording covering the clip with device, role, offset, confidence, drift and method), `audio_roles`, `method`, `synced_at`.
- `review` (status, select, rating, reviewer, notes) and `rights` (releases, restrictions): maintained by people, never overwritten by the skills.
- `usage`: export history.

### Recording JSON (`templates/recording_metadata_template.json`)

Next to every audio file: device, role, project, batch, size and hash, technical facts including `codec`, `channels_detail` (track names from iXML) and `recorder_model`, `timecode_start` and `bwf_time_reference` (untrusted as real time), `synced_videos` and `synced_recordings` with offsets, confidence, drift and method, and a transcript slot with segments and language.

### Conventions

- Offsets everywhere: the position in the reference file where the other file starts. In a clip JSON the recording is the reference. In a recording JSON that recording is the reference; negative means the other file started first.
- Camera clocks are rarely set and timecode is free-run, so no field derived from them is treated as true time. `footage-sync` places everything by sound alone: recordings against each other first, then clips against the synced recordings. Recorder file names and dates are not used either; recorder clocks can be a day or more off. Camera model, lens and exposure are not recorded.
- `footage-sync` writes `sync_map.json` (with `sessions`: the recordings on one clock) and `sync_timeline.fcpxml` in the batch folder and fills the sync fields; `footage-describe` fills them too when the map exists. Sync runs first: the describe skills refuse a batch that has recordings and no `sync_map.json`. A batch described before it was synced is repaired with `footage-sync --relink --refresh-transcripts`, not a re-describe. `migrate_json.py` in the describe skill brings older JSONs up to the current schema.
