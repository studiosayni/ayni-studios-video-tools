#!/bin/zsh
# One-time setup for footage-sync: a private Python environment with numpy and the Anthropic SDK (only used by --refresh-transcripts).
# Usage: ./setup.sh [--with-whisper]
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is missing. Install it with: brew install ffmpeg"; exit 1; }
[ -x "$HERE/venv/bin/python" ] || python3 -m venv "$HERE/venv"
"$HERE/venv/bin/pip" install -q --upgrade pip >/dev/null; "$HERE/venv/bin/pip" install -q numpy anthropic
if [ "$1" = "--with-whisper" ]; then "$HERE/venv/bin/pip" install -q mlx-whisper && echo "mlx-whisper installed"; fi
"$HERE/venv/bin/python" -c "import numpy, anthropic; print('ready: numpy', numpy.__version__, '| anthropic', anthropic.__version__)"
