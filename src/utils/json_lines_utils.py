"""Reading and appending the rows of a `.jsonl` file that a kill may have left with its last line cut in half.

A row is written the moment its event ends, never once at the end, so a run killed mid-write leaves a last line with
no line end. Appended straight after it, the next row would join that torn line and be dropped with it when read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["append_json_line", "read_json_lines"]


def _is_last_line_ended(path: Path) -> bool:
    """Whether the file's last byte ends a line, read without reading the file."""

    with path.open("rb") as handle:
        handle.seek(-1, 2)

        return handle.read(1) == b"\n"


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    """Every complete line of `path`, none when the file is missing. A line a kill cut in half is dropped, never a reason
    to lose the rest."""

    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return rows


def append_json_line(path: Path,
                     row: dict[str, Any]) -> None:
    """`row` as one line at the end of `path`, a value JSON has no form for written as its text. A last line a kill cut
    in half is ended first, so this row starts a line of its own and is read; the torn one is still dropped."""

    path.parent.mkdir(parents=True, exist_ok=True)
    is_torn = path.is_file() and path.stat().st_size > 0 and not _is_last_line_ended(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(("\n" if is_torn else "") + json.dumps(row, ensure_ascii=False, default=str) + "\n")
