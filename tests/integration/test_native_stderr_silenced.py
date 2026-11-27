"""A native library's lines on the error stream are kept off a run's console while phase 1 reads a PDF: Tesseract, run
by pymupdf4llm, printed "Image too small to scale!!" and "Line cannot be recognized!!" for each picture one pixel wide,
written by C code straight onto file 2, past Python's `sys.stderr`. A child process writes such a line with the C
library's own `write` inside `native_stderr_utils.hide_native_stderr`, and one after it, and only the second reaches its
error stream. The entry point loads OpenCV and PyAV the same way: each bundles its own libavdevice, and macOS printed two
lines about the duplicate classes on every run's console as the second loaded."""

import subprocess
import sys
from pathlib import Path

from support import harness

SRC = Path(__file__).resolve().parents[2] / "src"
CHILD = f"""
import ctypes, sys
sys.path.insert(0, {str(SRC)!r})
from utils import native_stderr_utils
libc = ctypes.CDLL(None)
with native_stderr_utils.hide_native_stderr():
    libc.write(2, b"Image too small to scale!!\\n", 27)
libc.write(2, b"after the block\\n", 16)
"""


def test_a_native_library_s_error_line_is_kept_off_the_console_and_later_lines_are_not():
    child = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, timeout=60)
    harness.check("the child ran", child.returncode == 0, child.stderr)
    harness.check("the native line inside the block reached no console", "too small" not in child.stderr, child.stderr)
    harness.check("the error stream is back after it", "after the block" in child.stderr, child.stderr)
