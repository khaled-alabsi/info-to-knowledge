"""Reads page images with Unlimited-OCR-2, one page a request, the model loaded once, and writes each page's reading
into a JSON file as it lands: `[{"image", "text", "seconds", "finishReason", "isReadAgain"}]`. A page that runs to the
token limit (a loop: a dot leader or a reference written over and over at temperature 0) is read again with a
repetition penalty, which ended all 3 such pages of 168 measured.

Usage (in this folder's environment): .venv/bin/python read_pages.py --model <repository> --out <json> <image> ...
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from mlx_vlm import generate, load
from mlx_vlm.prompt_utils import apply_chat_template

PROMPT = "Multi page parsing."    # the instruction the model was trained on (its card)
MAX_TOKENS = 8192                 # a dense page wrote at most 1,863 tokens when measured; a loop runs to it
REPETITION_PENALTY = 1.1          # measured: ended all 3 looped pages, each losing at most 1% to 10% of words


def _read_page(model,
               processor,
               image: Path,
               repetition_penalty: float | None) -> tuple[str, float, str | None]:
    prompt = apply_chat_template(processor, model.config, PROMPT, num_images=1)
    started = time.perf_counter()
    result = generate(model, processor, prompt, image=[str(image)], max_tokens=MAX_TOKENS, temperature=0.0,
                      repetition_penalty=repetition_penalty)

    return result.text, round(time.perf_counter() - started, 2), result.finish_reason


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="the Hugging Face repository of the MLX build")
    parser.add_argument("--out", required=True, type=Path, help="the JSON file each page's reading is written into")
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    model, processor = load(args.model)
    readings = []
    for image in args.images:
        text, seconds, finish_reason = _read_page(model, processor, image, None)
        is_read_again = finish_reason == "length"
        if is_read_again:
            text, seconds, finish_reason = _read_page(model, processor, image, REPETITION_PENALTY)
        readings.append({"image": str(image), "text": text, "seconds": seconds, "finishReason": finish_reason,
                         "isReadAgain": is_read_again})
        args.out.write_text(json.dumps(readings, ensure_ascii=False), encoding="utf-8")
        print(f"{image.name}: {seconds} s{' (read again: it looped)' if is_read_again else ''}", flush=True)


if __name__ == "__main__":
    main()
