# Contributing

Open an issue or a pull request with a focused change and the checks you ran. Use synthetic examples and keep credentials, media, client notes, transcripts, people registries, and run logs out of this repository.

The skill folders intentionally bundle some shared modules so they can be used independently. When changing schema, transcription, or pipeline behavior, check the corresponding copies in the other skills. Preserve user-maintained review, rights, names, and usage fields.

For synchronization changes, test against synthetic signals with known offsets and manually reviewed footage you have permission to use; report unmatched and uncertain cases as well as successful matches. Do not include that footage in a pull request.

Run tools/setup_repo.sh to install the media commit guard. The repository workflow also checks tracked files for media extensions and size.
