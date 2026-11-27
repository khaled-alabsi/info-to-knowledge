#!/usr/bin/env bash
# Sets up this repository on a new machine, in order, each step skipped when it is done already:
#   1. the Python environment (.venv, Python 3.12) with requirements.txt and requirements-diarization.txt
#   2. the Hugging Face token in .env (git-ignored), which `i2k` exports: the speaker model is gated
#   3. the web page, built into web/dist
#   4. the recommended models (`i2k setup-models`), downloaded into models/ and linked into LM Studio
#
#     ./setup.sh hf_xxx        # the token; without it, HF_TOKEN or the one already in .env is used
#
# Still needed by hand: ffmpeg and ffprobe on the PATH, and LM Studio installed and opened once. The frame classifier's
# own environment is built here, in frame-classifier/.venv (frames.knowledgeClassifierCommand).

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
token="${1:-${HF_TOKEN:-}}"

# 1. the Python environment
if [ ! -x .venv/bin/python ]; then
  if ! command -v python3.12 >/dev/null 2>&1; then
    echo "python3.12 is not installed: install it (macOS: brew install python@3.12), then run this again" >&2
    exit 2
  fi
  echo "creating .venv with Python 3.12"
  python3.12 -m venv .venv
fi
.venv/bin/python -m pip install --quiet -r requirements.txt -r requirements-diarization.txt
echo "Python packages installed"

# 2. the Hugging Face token, kept in .env for every later i2k command
if [ -n "$token" ] && ! grep -q '^HF_TOKEN=' .env 2>/dev/null; then
  echo "HF_TOKEN=$token" >> .env
  echo "HF_TOKEN written to .env"
fi
if [ -z "$token" ] && [ -f .env ]; then
  token=$(sed -n 's/^HF_TOKEN=//p' .env | head -n 1)
fi

# 3. the web page
if command -v npm >/dev/null 2>&1; then
  (cd web && npm ci --silent && npm run build --silent)
  echo "web page built into web/dist"
else
  echo "npm is not installed: the web page is not built (install Node.js, then run this again)" >&2
fi

# 4. the recommended models
if [ -n "$token" ]; then
  ./i2k setup-models --hf-token "$token"
else
  ./i2k setup-models
fi

for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || echo "$tool is not on the PATH: install it (macOS: brew install ffmpeg)" >&2
done
frame-classifier/setup.sh || echo "the frame classifier's environment could not be built: phase 2 needs it" >&2
echo "done: ./i2k probe checks the model hosts"
