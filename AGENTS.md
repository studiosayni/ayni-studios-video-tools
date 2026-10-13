# Ayni Studios Video Tools — agent instructions

This public repository contains reusable skills, scripts, templates, and documentation. Read README.md and _docs/FOOTAGE_SKILLS.md before processing a batch. Individual skill instructions are in .claude/skills/<skill>/SKILL.md.

1. Read or help the user complete the batch's directors_note.md. Record languages and audio roles. Never invent shoot context or names.
2. Sync batches with external audio first. Use footage-sync, review weak and unmatched placements, and retain camera audio as ambience when appropriate. Recorder clock dates do not establish whether audio belongs to a shoot; check waveforms.
3. Describe using footage-describe or footage-describe-batch, with verification enabled. Get authorization for paid API usage and estimate with --dry-run where supported.
4. For already-described batches, use sync --relink --refresh-transcripts when appropriate instead of repeating visual description. Transcript refresh can incur API costs.
5. Review uncertain descriptions and sync matches. Use people-link only for clothing/context grouping; names come from the user, never face identification.

Never move, rename, or delete source media without explicit authorization. Outputs belong beside the user's media, not in this public repository. Do not commit client notes, transcripts, people registries, catalogue records, API keys, or usage logs here. tools/publish.py is for a separate private catalogue and requires an explicit --repo destination.

Use branches and pull requests for changes to this toolkit. Keep the original source repository untouched. Run syntax and relevant local checks; do not incur model charges just to test packaging.
