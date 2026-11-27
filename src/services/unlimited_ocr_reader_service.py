"""Scanned PDF pages read by Unlimited-OCR-2 (`pdf.ocrEngine` "unlimited-ocr"): its tool runs in
its own environment (`tools/unlimited-ocr/setup.sh`) on the pages rendered as images, the model loaded once, and its
reading of each page comes back as text in the model's block format (`utils/unlimited_ocr_block_utils.py`). It runs on
MLX, Apple silicon only, and needs its model downloaded into the Hugging Face cache (3.9 GB, too large for git)."""

from __future__ import annotations

import collections
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import config
from services import knowledge_filter_service

__all__ = ["TOOL_SCRIPT", "SETUP_SCRIPT", "is_installed", "is_model_downloaded", "read_pages", "set_up"]

TOOL_SCRIPT = config.PLAYBOOK_ROOT / "tools" / "unlimited-ocr" / "read_pages.py"  # loads the model, reads each page
SETUP_SCRIPT = config.PLAYBOOK_ROOT / "tools" / "unlimited-ocr" / "setup.sh"      # builds its environment, downloads
_TAIL_LINES = 5                                   # the last lines of a failing tool's output its error carries


def _resolve_python() -> Path:
    return knowledge_filter_service.resolve_playbook_path(config.settings.pdf.unlimited_ocr_python)


def _build_model_cache_dir() -> Path:
    """Where the Hugging Face cache keeps `pdf.unlimitedOcrModel`'s files: `$HF_HOME/hub`, else ~/.cache/huggingface."""

    hub = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"

    return hub / ("models--" + config.settings.pdf.unlimited_ocr_model.replace("/", "--"))


def is_model_downloaded() -> bool:
    """Whether the model's weights are in the Hugging Face cache."""

    return any((_build_model_cache_dir() / "snapshots").glob("*/*.safetensors"))


def is_installed() -> bool:
    """Whether the tool's environment is built and its model downloaded: then scanned pages can be read by it."""

    return _resolve_python().is_file() and is_model_downloaded()


def read_pages(images: list[Path],
               on_page: Callable[[int, int], None] | None = None) -> list[str]:
    """Each image's reading, in order, as the model writes it; `on_page(read, total)` is told after each page, from
    the line the tool prints for it, so a reader of 8 to 20 s a page shows how far it is. A failing tool raises
    `RuntimeError` with its last lines."""

    names = {image.name for image in images}
    with tempfile.TemporaryDirectory(prefix="i2k-unlimited-ocr-") as folder:
        out = Path(folder) / "readings.json"
        # its errors joined to its own lines, read as they come: two pipes read one after the other can fill and hang
        process = subprocess.Popen([str(_resolve_python()), str(TOOL_SCRIPT), "--model",
                                    config.settings.pdf.unlimited_ocr_model, "--out", str(out), *map(str, images)],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines: collections.deque[str] = collections.deque(maxlen=_TAIL_LINES)
        read = 0
        for line in process.stdout or []:
            lines.append(line.rstrip())
            if line.split(":", 1)[0] in names:
                read += 1
                if on_page:
                    on_page(read, len(images))
        returncode = process.wait()
        readings = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
    if returncode != 0 or len(readings) != len(images):
        raise RuntimeError(f"Unlimited-OCR-2 read {len(readings)} of {len(images)} page(s) "
                           f"(exit {returncode}): " + "\n".join(lines))

    return [str(reading["text"]) for reading in readings]


def set_up(say) -> int:
    """`tools/unlimited-ocr/setup.sh --download`, its lines handed to `say`: the tool's environment built and its model
    downloaded; its exit code."""

    process = subprocess.Popen(["sh", str(SETUP_SCRIPT), "--download"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        say("  " + line.rstrip())

    return process.wait()
