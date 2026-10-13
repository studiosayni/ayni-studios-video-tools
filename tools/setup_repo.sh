#!/bin/sh
# One-time setup of a media-root clone: install the commit guard and check the tools the publish flow needs.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd); cd "$HERE"
git config core.hooksPath tools/githooks && chmod +x tools/githooks/* && echo "commit guard installed (core.hooksPath = tools/githooks)"
[ -n "$(git config user.name)" ] || echo "set your name first: git config user.name \"Your Name\""
command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 && echo "gh: logged in" || echo "gh: install GitHub CLI (brew install gh) and run gh auth login, needed for publish.py --pr"
command -v ffmpeg >/dev/null 2>&1 && echo "ffmpeg: ok" || echo "ffmpeg: missing (brew install ffmpeg)"
for s in footage-describe footage-describe-batch footage-sync people-link asset-describe; do
  [ -x ".claude/skills/$s/venv/bin/python" ] && echo "$s: ready" || echo "$s: run .claude/skills/$s/setup.sh"
done
