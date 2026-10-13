#!/bin/zsh
# One-time setup for people-link: a private Python environment with the Anthropic SDK and Pillow.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo "python3 is missing. Install it with: brew install python"; exit 1; }
[ -x "$HERE/venv/bin/python" ] || python3 -m venv "$HERE/venv"
"$HERE/venv/bin/pip" install -q --upgrade pip >/dev/null
"$HERE/venv/bin/pip" install -q anthropic pillow
"$HERE/venv/bin/python" -c "import anthropic, PIL; print('ready: anthropic', anthropic.__version__, '| pillow', PIL.__version__)"
if command -v ant >/dev/null 2>&1 && ant auth status >/dev/null 2>&1; then echo "auth: ant login found"; elif [ -n "$ANTHROPIC_API_KEY" ]; then echo "auth: ANTHROPIC_API_KEY set"; else echo "auth: none yet. Run 'ant auth login' or export ANTHROPIC_API_KEY"; fi
