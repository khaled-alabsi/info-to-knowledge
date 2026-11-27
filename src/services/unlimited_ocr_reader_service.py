"""Scanned PDF pages read by Unlimited-OCR-2 (`pdf.ocrEngine` "unlimited-ocr"): its tool runs in
its own environment (`tools/unlimited-ocr/setup.sh`) on the pages rendered as images, the model loaded once, and its
reading of each page comes back as text in the model's block format (`utils/unlimited_ocr_block_utils.py`). It runs on
MLX, Apple silicon only, and needs its model downloaded into the Hugging Face cache (3.9 GB, too large for git). With
`providers.chat.host` set, that chat server reads each page instead: one standard chat request with the page's image and
the same instruction, naming the model `pdf.unlimitedOcrModel`; the tool and its model are not needed here."""

from __future__ import annotations

import collections
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import config
from models.endpoint_errors import EndpointError
from models.endpoint_spec import EndpointSpec
from services import endpoint_client
from services import knowledge_filter_service

__all__ = ["TOOL_SCRIPT", "SETUP_SCRIPT", "PAGE_PROMPT", "is_installed", "is_model_downloaded", "read_pages", "set_up"]

TOOL_SCRIPT = config.PLAYBOOK_ROOT / "tools" / "unlimited-ocr" / "read_pages.py"  # loads the model, reads each page
SETUP_SCRIPT = config.PLAYBOOK_ROOT / "tools" / "unlimited-ocr" / "setup.sh"      # builds its environment, downloads
_TAIL_LINES = 5                                   # the last lines of a failing tool's output its error carries
PAGE_PROMPT = "Multi page parsing."               # the instruction the model was trained on (its card)
_MAX_TOKENS = 8192                                # a dense page wrote at most 1,863 tokens when measured; a loop runs to it
_REPETITION_PENALTY = 1.1                         # measured: ended all 3 looped pages, each losing at most 1% to 10% of words


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
    """Whether scanned pages can be read: a chat server is set (`providers.chat.host`), or the tool's environment is built
    and its model downloaded."""

    return endpoint_client.find_endpoint(kind="chat") is not None or (_resolve_python().is_file() and is_model_downloaded())


def _request_page_text(endpoint: EndpointSpec,
                       data_url: str,
                       repetition_penalty: float | None) -> tuple[str, str]:
    """Give me the text a server reads on one page and why it stopped (`stop` or `length`); a repetition penalty, when
    given, is sent beside the standard fields."""

    body = {"model": endpoint.model or config.settings.pdf.unlimited_ocr_model,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_url}},
                                                      {"type": "text", "text": PAGE_PROMPT}]}],
            "max_tokens": _MAX_TOKENS, "temperature": 0.0}
    if repetition_penalty is not None:
        body["repetition_penalty"] = repetition_penalty
    choice = endpoint_client.request_chat_completion(endpoint=endpoint, body=body)["choices"][0]

    return str(choice["message"].get("content") or ""), str(choice.get("finish_reason") or "")


def _read_pages_from_server(endpoint: EndpointSpec,
                            images: list[Path],
                            on_page: Callable[[int, int], None] | None) -> list[str]:
    """Give me each image's reading from the model server, in order; a page that ended at the token limit (a loop) is read
    again with a repetition penalty, as the tool does. A failed request raises `RuntimeError` naming the page."""

    readings = []
    for image in images:
        data_url = endpoint_client.build_data_url(content=image.read_bytes(), media_type="image/png")
        try:
            text, finish_reason = _request_page_text(endpoint=endpoint, data_url=data_url, repetition_penalty=None)
            if finish_reason == "length":
                text, finish_reason = _request_page_text(endpoint=endpoint, data_url=data_url,
                                                         repetition_penalty=_REPETITION_PENALTY)
        except EndpointError as error:
            raise RuntimeError(f"the server read {len(readings)} of {len(images)} page(s), and failed on {image.name}: "
                               f"{error}") from error
        readings.append(text)
        if on_page:
            on_page(len(readings), len(images))

    return readings


def read_pages(images: list[Path],
               on_page: Callable[[int, int], None] | None = None) -> list[str]:
    """Each image's reading, in order, as the model writes it; `on_page(read, total)` is told after each page, from
    the line the tool prints for it, so a reader of 8 to 20 s a page shows how far it is. A failing tool raises
    `RuntimeError` with its last lines. With `providers.chat.host` set, the chat server reads each page instead."""

    endpoint = endpoint_client.find_endpoint(kind="chat")
    if endpoint is not None:
        return _read_pages_from_server(endpoint=endpoint, images=images, on_page=on_page)
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
