"""Lines a native library prints straight onto the process's error stream, kept off a run's console while it works."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator

__all__ = ["hide_native_stderr"]


@contextlib.contextmanager
def hide_native_stderr() -> Iterator[None]:
    """The process's error stream (file 2) sent to nowhere while the block runs, then put back. A library written in C
    or C++ writes there past Python's own `sys.stderr`: Tesseract, run by pymupdf4llm on each picture of a PDF, printed
    "Image too small to scale!!" and "Line cannot be recognized!!" for every picture one pixel wide, hundreds of lines on
    one run's console. Python's own errors still reach the console: an exception is printed after the block, once the
    stream is back."""

    sys.stderr.flush()
    saved = os.dup(2)
    nowhere = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(nowhere, 2)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, 2)
        os.close(nowhere)
        os.close(saved)
