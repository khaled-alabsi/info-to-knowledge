"""Asks Liquid AI's decision model d1-2B, for every frame image of a folder, whether it holds knowledge and which kind of
frame with no knowledge it is, all questions in one pass a frame, and writes each frame's probabilities as JSON:

    {"model": "LiquidAI/d1-2B", "frames": [{"frame": "frame-012.jpg", "pKnowledge": 0.91,
                                             "classes": {"people": 0.02, "desktop": 0.01, "blank": 0.03, "joining": 0.0}}]}

Run from this folder's own environment (setup.sh), as phase 2's knowledge filter does when `frames.knowledgeFilterModel`
is `liquidai-d1-2B`:

    .venv/bin/python filter_frames.py <frames dir> --out <file>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODEL_ID = "LiquidAI/d1-2B"
# The width a frame is asked at: the measured one (0.7 s a frame on Apple's GPU)
FRAME_WIDTH = 640
# The POC's question, its AUC 0.94 to 0.998 over three labelled runs; each class is one more question of the same pass
QUESTIONS = {
    "knowledge": "Does this screen show information a reader of the meeting's notes would need: text, numbers, a table, "
                 "a diagram, code or a page with content? A screen of people's cameras, an empty desktop, a blank or "
                 "loading window is not.",
    "people": "Does this screen show only people: one camera, a gallery of cameras, or a room, with no shared content?",
    "desktop": "Does this screen show only an empty desktop, a wallpaper or a file manager with no document open?",
    "blank": "Is this screen blank, a single colour, or a window still loading with nothing shown yet?",
    "joining": "Is this screen a call's join, waiting or lobby screen, or its controls with no shared content?",
}


def _ask(model, path: Path) -> dict:
    from PIL import Image

    image = Image.open(path).convert("RGB")
    if image.width > FRAME_WIDTH:
        image = image.resize((FRAME_WIDTH, round(image.height * FRAME_WIDTH / image.width)))
    questions = {name: {"type": "noul", "instructions": text} for name, text in QUESTIONS.items()}
    answers = model.system_one(None, questions, media=[image])["answers"]
    probabilities = {name: round(float(answers[name]["noul"]), 4) for name in QUESTIONS}

    return {"frame": path.name, "pKnowledge": probabilities.pop("knowledge"), "classes": probabilities}


def _load_model():
    import torch
    from transformers import AutoModel

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.float32 if device == "cpu" else torch.bfloat16

    return AutoModel.from_pretrained(MODEL_ID, trust_remote_code=True, dtype=dtype).to(device)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("frames_dir", type=Path, help="the folder of frame images (frame-*.jpg)")
    parser.add_argument("--out", type=Path, required=True, help="the JSON file to write")
    args = parser.parse_args()
    paths = sorted(args.frames_dir.glob("frame-*.jpg"))
    if not paths:
        print(f"no frame-*.jpg in {args.frames_dir}", file=sys.stderr)
        return 1
    model = _load_model()
    rows = [_ask(model, path) for path in paths]
    args.out.write_text(json.dumps({"model": MODEL_ID, "frames": rows}, indent=1), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
