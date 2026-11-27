#!/bin/sh
# Builds this folder's own environment for Unlimited-OCR-2 (Python 3.12), from requirements.txt; with --download it
# also downloads the model (3.9 GB) into the Hugging Face cache, where read_pages.py loads it. MLX runs on Apple
# silicon only: on any other machine pdf.ocrEngine stays "tesseract".
here=$(cd "$(dirname "$0")" && pwd)
python=${PYTHON:-python3.12}
model=${UNLIMITED_OCR_MODEL:-mlx-community/Unlimited-OCR-2-8bit}
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "Unlimited-OCR-2 runs on MLX, Apple silicon only: keep pdf.ocrEngine \"tesseract\" on this machine"
    exit 1
fi
[ -x "$here/.venv/bin/python" ] || "$python" -m venv "$here/.venv" || exit 1
echo "installing the tool's packages into $here/.venv ..."
"$here/.venv/bin/python" -m pip install -q -r "$here/requirements.txt" || exit 1
if [ "$1" = "--download" ]; then
    echo "downloading $model (about 3.9 GB) ..."
    "$here/.venv/bin/python" -c "import sys; from huggingface_hub import snapshot_download; print(snapshot_download(sys.argv[1]))" "$model" || exit 1
fi
echo "Unlimited-OCR-2 tool ready"
