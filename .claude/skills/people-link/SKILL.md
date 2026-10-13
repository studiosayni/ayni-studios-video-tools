---
name: people-link
description: After footage-describe, link the same person across the clips of a batch (person_1 in one clip, person_3 in another) into batch persons, build a review sheet of reference stills, and let a person name each one once so the name propagates to every clip and recording JSON. Use when someone says "link the people", "who is who in this batch", "name the people", "build the people sheet", "merge these two people", or after describing a batch with people in it. Compares clothing and context only, never faces.
---

# people-link

Runs after `footage-describe` on a batch. Every clip JSON has its own `person_1`, `person_2` with a descriptor, a `best_seen_seconds` and a `reference_still` in `people/`. This skill groups those entries into batch persons (`bp_01`, `bp_02`), writes `people/people.json` (the batch registry), stamps `person_key` into every clip's people entries, builds `people/_people_sheet.html`, and keeps `media/<client>/people.json` (the canonical people across batches) current.

How it links: one Opus 5.5 text request per batch (chunks of 350 entries) groups descriptors that agree on specific details (a garment, colour, accessory, role). Two entries from the same clip are never the same person. Groups not rated high are then checked against a contact sheet of their reference stills, compared on clothing, headwear, carried items and setting. Faces, skin, age and body shape are never used, and the skill leans toward leaving an entry unlinked rather than merging wrongly. Cost is a few cents per batch for the grouping plus 10 to 50 cents for the still checks.

Names are typed once: `--name bp_02 "Alex"` writes `canonical_name` into every linked clip entry and any tied recording speaker, and adds Alex to the client registry. Across batches the link is that name. Re-runs only place entries the registry has not seen, so names and manual edits survive; `--regroup` redoes unnamed groups.

## Before running

1. The batch must already have clip JSONs from footage-describe (schema 2.5 or later gives descriptors and stills; older JSONs link by descriptor only). Confirm the batch with the user.
2. Environment: `<skill>/venv` from `<skill>/setup.sh`. Auth: `ant auth status` or `ANTHROPIC_API_KEY`.

## Run

```
<skill>/venv/bin/python <skill>/scripts/link.py <batch-folder> [<more>]
```

Options: `--confirm all|uncertain|none` (default uncertain), `--regroup`, `--sheet-only` (rebuild the HTML, no model calls).
Edits: `--name KEY "Name"`, `--merge KEEP GONE`, `--split KEY CLIP:person_N`, `--link KEY CLIP:person_N` (place an unlinked entry), `--speaker KEY "speaker 3"` (tie a recording speaker id, from the recording JSONs footage-sync wrote).

## Claude Code mode (subscription, no API)

Add `--claude-code` and no request goes to the API. The script writes each model step as a request file under `people/_link_work/` (the grouping text, or a contact sheet JPEG for a confirm step, plus the JSON schema), a `footage-describer` subagent (Agent tool, `subagent_type: footage-describer`, the request path as the prompt; Opus at xhigh with only Read and Write, about a third of the tokens of a general-purpose agent but about the same share of the plan's limits; see footage-describe) writes the response file. Without that agent installed, a general-purpose agent with `scripts/claude_code_prompt.md` and `{REQUEST}` replaced by the request path also works, and the next run of the same command applies it. Run, launch one agent per pending request (they can run in parallel), run again; typically three runs: grouping, then the confirm sheets for groups not rated high, then the registry, review sheet and propagation. The registry records `model: claude-code`. Never answer a request in the main conversation; always through a subagent. For usage tracking, append each agent's tokens to the batch's `outputs/describe_runs/agent_tokens.tsv` as `_link link_group <tokens> <ms>` or `_link link_confirm <tokens> <ms>` before running `tools/usage_ledger.py record` (see footage-describe). `--confirm none` skips the contact-sheet checks, which are about 57,000 tokens per group, and leaves the review to the people sheet.

## After running

Report how many people were linked, how many entries stayed unlinked, the cost, and point the user at `people/_people_sheet.html` (open it from the drive; the stills are relative links). Do not name anyone yourself and do not guess who a person is. When the user names someone, run the `--name` edit and confirm how many JSONs changed.
