#!/bin/sh
# Builds this folder's own environment for d1-2B, the decision model phase 2's knowledge filter runs when
# frames.knowledgeFilterModel is "liquidai-d1-2B", from requirements.txt; with --download it also downloads the model
# (about 4.5 GB) into the Hugging Face cache, where filter_frames.py loads it. It runs on Apple's GPU, a CUDA card or
# the CPU (slower).
here=$(cd "$(dirname "$0")" && pwd)
python=${PYTHON:-python3.12}
model=${D1_MODEL:-LiquidAI/d1-2B}
[ -x "$here/.venv/bin/python" ] || "$python" -m venv "$here/.venv" || exit 1
echo "installing the tool's packages into $here/.venv ..."
"$here/.venv/bin/python" -m pip install -q -r "$here/requirements.txt" || exit 1
if [ "$1" = "--download" ]; then
    echo "downloading $model ..."
    "$here/.venv/bin/python" -c "import sys; from huggingface_hub import snapshot_download; print(snapshot_download(sys.argv[1]))" "$model" || exit 1
fi
echo "d1-2B knowledge filter tool ready"
