"""Apple's text recognizer cold (DEFECTS 506): its first call took 1:52 of wall time while the Neural Engine compiled
its model, and every later call 0.2 s. Through the real read and its real subprocess, a made tool that behaves so
(12 s until one call has ended, then at once) stands in for the cold recognizer: the first read waits it out."""

import stat
import tempfile
from pathlib import Path

import numpy

from support import harness
from services import apple_text_service

_COLD_TOOL = """#!/bin/sh
marker="{marker}"
[ -f "$marker" ] || sleep 12
touch "$marker"
echo '[{{"text": "Refund", "box": [1, 2, 3, 4], "line": 0}}]'
"""


def test_a_cold_recognizer_s_first_read_is_waited_out_defect():
    # Defect: two tries of 10 s gave the cold start up, and the frame's words were left out. Fix: the first read a
    # process makes is given 180 s
    folder = Path(tempfile.mkdtemp())
    tool = folder / "apple-text-reader"
    tool.write_text(_COLD_TOOL.format(marker=folder / "warm"), encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    saved = (apple_text_service._BINARY, apple_text_service._build_tool, dict(apple_text_service._WARM))
    apple_text_service._BINARY, apple_text_service._build_tool = tool, (lambda: True)
    apple_text_service._WARM["is_warm"] = False
    apple_text_service._STALL.update(failures=0, until=0.0)
    try:
        words = apple_text_service.try_find_words_in_image(numpy.full((30, 30, 3), 77, numpy.uint8))
    finally:
        apple_text_service._BINARY, apple_text_service._build_tool = saved[0], saved[1]
        apple_text_service._WARM.update(saved[2])
    harness.check("the cold first read is waited out and its words kept",
                  [word["text"] for word in words or []] == ["Refund"], str(words))
