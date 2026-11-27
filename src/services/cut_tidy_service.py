"""The cuts a finished run never placed, moved out of the way.

A chapter's worker cuts a frame, looks at the cut, and sometimes writes its chapter text without it: the cut it undid is
recorded as undone, and the one it simply left out of its markdown is recorded as nothing at all. Both leave a PNG in
`04_writing/images/cuts/` that no document line names. Measured over eleven finished runs: 56 of 216 cuts still in use
were never placed, and one run left 33 of its 68.

This is the run's last step, after every phase is complete. Each such file moves to `.trash/cuts/` inside the run
folder, and the store records what moved and why, so the move is read back and undone by hand. Nothing is deleted,
and a run that is not done is left alone: its next resume may still place what it cut. The store's key
`removedUnreferenced` lists the files that were moved, not deleted.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from models.run_paths import RunPaths
from services import document_file_service
from utils import document_text_utils

TRASH_DIR_NAME = ".trash"
# Where a run folder's unused cuts are moved, under it
TRASH_CUTS_DIR = f"{TRASH_DIR_NAME}/cuts"
_CUT_IN_MARKDOWN = re.compile(rf"\(({document_text_utils.CUT_ID_PATTERN})")
# Why a cut was never placed when no reason set it aside: it was left out of every submitted chapter text
UNPLACED = "unplaced"

__all__ = ["TRASH_DIR_NAME", "TRASH_CUTS_DIR", "UNPLACED", "list_unused_cuts", "set_aside_unused_cuts"]


def _find_free_path(wanted: Path) -> Path:
    """`wanted`, or `<stem>-2<suffix>`, `-3` ... when a file of that name is in the trash already: a reset to phase 3
    starts the cut numbers again, and the next tidy's `cut-001.png` would have replaced the first one."""

    target, number = wanted, 2
    while target.exists():
        target, number = wanted.with_name(f"{wanted.stem}-{number}{wanted.suffix}"), number + 1

    return target


def _list_placed_cuts(paths: RunPaths,
                      store: dict[str, Any]) -> set[str]:
    """Every cut id the document rests on: the store's own image blocks, and the id in each section's markdown."""

    # Both are read, never one: a store whose blocks were cleared still has its sections, and a section rewritten by
    # a later phase still has its block
    placed = {str(block.get("cut")) for block in store.get("blocks") or [] if block.get("cut")}
    sections = document_file_service.build_sections_dir(paths)
    if sections.is_dir():
        for path in sections.glob("*.md"):
            placed |= set(_CUT_IN_MARKDOWN.findall(path.read_text(encoding="utf-8", errors="ignore")))

    return placed


def list_unused_cuts(paths: RunPaths,
                     store: dict[str, Any]) -> list[dict[str, str]]:
    """Every cut of the store whose file no document line names: its id, its file, its frame and why it is unused."""

    # A cut set aside and a cut simply left out are both unused; they are told apart so the record says which happened
    placed = _list_placed_cuts(paths, store)
    unused = []
    for cut_id, cut in sorted((store.get("cuts") or {}).items(), key=lambda item: item[0]):
        # A cut whose file an earlier call already moved is no longer unused but moved: listed again, it told the
        # owner there was still work to do after the work was done
        if cut_id in placed or not cut.get("file") or not (paths.output_root / str(cut["file"])).is_file():
            continue
        unused.append({"cut": cut_id, "file": str(cut["file"]), "frame": str(cut.get("frame") or ""),
                       "why": str(cut.get("discarded") or UNPLACED)})

    return unused


def set_aside_unused_cuts(paths: RunPaths) -> list[dict[str, str]]:
    """Move every unused cut's file into the run folder's `.trash/cuts/`, record each move in the store, and return
    what moved in the shape `list_unused_cuts` returns plus the path it moved to."""

    store = document_file_service.read_image_store(paths)
    moved = []
    trash = paths.output_root / TRASH_CUTS_DIR
    # A file an earlier call already moved is no longer listed, so running this twice changes nothing
    for row in list_unused_cuts(paths, store):
        source = paths.output_root / row["file"]
        trash.mkdir(parents=True, exist_ok=True)
        target = _find_free_path(trash / source.name)
        shutil.move(str(source), str(target))
        moved_row = {**row, "movedTo": str(target.relative_to(paths.output_root))}
        moved.append(moved_row)
        store.setdefault("removedUnreferenced", []).append({**moved_row, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        # Written after each move: a later move that fails must not leave this one without its row
        document_file_service.write_image_store(paths, store)

    return moved
