"""Apple's text recognizer (Vision, accurate level, English and German), the text reader of a Mac: the Swift tool in
`tools/apple-text-reader/`, built with `swiftc` the first time it is needed, reads one image and returns its words with
their boxes, in the shape `text_detection_service.find_words_in_image` gives (`{"text", "box", "line"}`). In
a measurement on board and ticket frames it read them with fewer misread words than Tesseract, at about 1 s a frame."""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy

__all__ = ["is_available", "find_words_in_image", "try_find_words_in_image"]

_TOOL_DIR = Path(__file__).resolve().parents[2] / "tools" / "apple-text-reader"
_SOURCE = _TOOL_DIR / "apple_text_reader.swift"
_BINARY = _TOOL_DIR / "apple-text-reader"
_BUILD_SECONDS = 300
# A read takes about 0.4 s; under a full swap one in four stalled past 60 s and the next read of the same frame took
# 0.4 s again (DEFECTS 506), so a read is given 10 s and tried once more, never waited on for a minute
_READ_SECONDS = 10
_READ_TRIES = 2
# ... but the first read a process makes is given this long, once: cold, the tool's first call took 1:52 of wall time
# (Apple's Neural Engine compiling its model) and the next 0.2 s, so two tries of 10 s gave every cold start up and a
# small frame's dialog went unread (DEFECTS 506, 622); warm, the first read ends as fast as any other
_FIRST_READ_SECONDS = 180
_WARM = {"is_warm": False}
# Each reading made, by the picture's content: a run's follow-ups read each frame of their batch again, and under a full
# swap 16 of Lecture 7's frames timed out on both tries (DEFECTS 506). A reading that failed is never kept
_WORDS_BY_IMAGE: dict[str, list[dict]] = {}
# Three reads in a row that timed out leave the tool alone this long, every reading meanwhile none: under a full swap 80
# of Lecture 7's readings timed out at 20 s each and phase 3 stood half an hour on them (DEFECTS 506)
_STALL_READS = 3
_STALL_SECONDS = 300
_STALL = {"failures": 0, "until": 0.0}
_now = time.monotonic
_log = logging.getLogger(__name__)


def _build_tool() -> bool:
    """Whether the tool is there, built now when it is missing or older than its source and this Mac has a Swift
    compiler; built beside the tool and moved over it, so a read running meanwhile never meets half a file."""

    if _BINARY.is_file() and _BINARY.stat().st_mtime >= _SOURCE.stat().st_mtime:
        return True
    if platform.system() != "Darwin" or shutil.which("swiftc") is None:
        return _BINARY.is_file()
    fresh = _BINARY.with_name(f"{_BINARY.name}.new")
    built = subprocess.run(["swiftc", "-O", str(_SOURCE), "-o", str(fresh)], capture_output=True, text=True,
                           timeout=_BUILD_SECONDS)
    if built.returncode == 0 and fresh.is_file():
        fresh.replace(_BINARY)

    return _BINARY.is_file()


def is_available() -> bool:
    """Whether this machine reads text with Apple's recognizer: a Mac whose tool is built or can be built."""

    return _build_tool()


def try_find_words_in_image(image: numpy.ndarray) -> list[dict] | None:
    """The words of an image held in memory, `{"text", "box": [left, top, right, bottom], "line", "confidence"}` in its
    pixels, the confidence the recogniser's for the word's line; None when the tool cannot run or the read failed, so a
    check that drops what the reading lacks can tell a failed read from a picture with no words: a timed-out read
    dropped an address a frame's code showed (Lecture 7 frame 8, DEFECTS 553)."""

    if not _build_tool():
        return None
    key = hashlib.sha256(f"{image.shape}|{image.dtype}".encode() + image.tobytes()).hexdigest()
    if key in _WORDS_BY_IMAGE:
        return [dict(word) for word in _WORDS_BY_IMAGE[key]]
    if _now() < _STALL["until"]:
        return None
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "image.png"
        cv2.imwrite(str(path), image)
        # A read that does not end in time is one the tool could not make: the recogniser timed out once while the
        # model host read a long prompt, and the raised error would have ended the step that asked
        read = None
        limits = [_READ_SECONDS] * _READ_TRIES if _WARM["is_warm"] else [_FIRST_READ_SECONDS]
        for limit in limits:
            try:
                read = subprocess.run([str(_BINARY), str(path)], capture_output=True, text=True, timeout=limit)
                break
            except subprocess.TimeoutExpired:
                continue
        _WARM["is_warm"] = True
        if read is None:
            # an empty reading looks like a frame with no words: the cause is said, so a check that then fails says why
            _log.warning(f"Apple's text recognizer gave no reading in {len(limits)} tries of {limits[0]} s: "
                         f"its words are left out")
            _STALL["failures"] += 1
            if _STALL["failures"] >= _STALL_READS:
                _STALL.update(failures=0, until=_now() + _STALL_SECONDS)
                _log.warning(f"Apple's text recognizer timed out {_STALL_READS} times in a row: it is left alone for "
                             f"{_STALL_SECONDS} s, its readings none meanwhile")
            return None
    _STALL["failures"] = 0
    if read.returncode != 0:
        _log.warning(f"Apple's text recognizer failed (exit {read.returncode}): {read.stderr.strip()[:300]}")
        return None

    words = [{"text": str(word["text"]), "box": [int(value) for value in word["box"]], "line": int(word["line"]),
              "confidence": float(word.get("confidence", 1.0))}
             for word in json.loads(read.stdout or "[]")]
    _WORDS_BY_IMAGE[key] = words

    return [dict(word) for word in words]


def find_words_in_image(image: numpy.ndarray) -> list[dict]:
    """`try_find_words_in_image`, with none for a read that could not be made: for a caller that only adds what it
    reads."""

    return try_find_words_in_image(image) or []
