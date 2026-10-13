#!/bin/zsh
# One-time setup for asset-describe: a private Python environment with the Gemini and Anthropic SDKs, Pillow and librosa.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is missing. Install it with: brew install ffmpeg"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is missing. Install it with: brew install python"; exit 1; }
[ -x "$HERE/venv/bin/python" ] || python3 -m venv "$HERE/venv"
"$HERE/venv/bin/pip" install -q --upgrade pip >/dev/null
"$HERE/venv/bin/pip" install -q google-genai anthropic pillow librosa numpy
"$HERE/venv/bin/python" -c "import google.genai as g, anthropic, librosa, PIL; print('ready: google-genai', g.__version__, '| anthropic', anthropic.__version__, '| librosa', librosa.__version__)"
if [ -n "$GEMINI_API_KEY" ] || [ -n "$GOOGLE_API_KEY" ]; then echo "gemini: key found"; else echo "gemini: no key. Export GEMINI_API_KEY (Google AI Studio, paid tier) in ~/.zshrc; without it the listening fields stay empty"; fi
if command -v ant >/dev/null 2>&1 && ant auth status >/dev/null 2>&1; then echo "claude: ant login found"; elif [ -n "$ANTHROPIC_API_KEY" ]; then echo "claude: ANTHROPIC_API_KEY set"; else echo "claude: none yet. Run 'ant auth login' or export ANTHROPIC_API_KEY; graphics will get scripted fields only"; fi
