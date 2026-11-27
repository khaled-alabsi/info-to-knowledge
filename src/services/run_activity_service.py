"""What a run is doing right now, in words, for its page: preparing a request (reading each frame's screen text), or
waiting for the model's answer, each since when.

The page said only "Running phase 3" while the run read frames' text for minutes with no model call, and the owner saw
LM Studio idle and took the run for stuck. The run writes `activity.json` in its log folder at each step; the page
reads it while the run is live.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

__all__ = ["ACTIVITY_FILE", "write_activity", "read_activity"]

ACTIVITY_FILE = "activity.json"


def write_activity(run_dir: Path,
                   text: str) -> None:
    """The run is now doing `text`, since now; replaced whole, so a reader never finds it half written."""

    path = run_dir / ACTIVITY_FILE
    written = path.with_name(f"{path.name}.writing")
    try:
        written.write_text(json.dumps({"text": text, "since": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
        os.replace(written, path)
    except OSError:
        # the page then shows the step before; the run's work does not depend on this record
        pass


def read_activity(run_dir: Path) -> dict | None:
    """`{"text", "since"}` the run wrote last, or None when it wrote none or the file does not read."""

    path = run_dir / ACTIVITY_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return record if isinstance(record, dict) and "text" in record else None
