"""A run branched from a phase: a NEW run folder beside the run, holding its progress before
phase N as if phase N and every later phase had never run; the run branched from is only read.

Every file is copied: images and media by hard link (no step writes one in place, and a reset unlinks rather than
rewrites them), every other file as a real copy, since the reset rewrites run stores and `02_frames.json` in place and a
hard link would change the original with them. The copy is then taken back to phase N - 1 by the reset's own code, and
`branch.json` records where it came from.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from models import output_contract
from models.run_paths import RunPaths
from services import run_reset_service
from utils import json_store_utils

__all__ = ["BRANCH_FILE", "FIRST_BRANCH_PHASE", "build_branch_folder", "create_branch", "read_branch",
           "is_rereading_recording"]

BRANCH_FILE = "branch.json"
# A branch from phase 1 keeps the run's choices (tags, profile, medium) and its recording: a PDF read again with
# another reader
FIRST_BRANCH_PHASE = 1
# Files no step writes in place: linked, never copied, so a branch of a run with hours of frames costs no space
_LINKED_SUFFIXES = (".jpg", ".jpeg", ".png", ".wav", ".mp3", ".m4a", ".mp4", ".mov", ".mkv", ".webm")


def _copy_file(source: str,
               target: str) -> str:
    """A hard link for an image or a recording, where the disk allows one; a real copy for every other file."""

    if source.lower().endswith(_LINKED_SUFFIXES):
        try:
            os.link(source, target)

            return target
        except OSError:
            pass  # another volume refuses a hard link: the copy below does the same job

    return shutil.copy2(source, target)


def build_branch_folder(run_folder: Path,
                        phase: int) -> Path:
    """`<run>-from-<phase>`, or the same with `-2`, `-3` ... when that name is taken: a branch never writes over one."""

    base = run_folder.parent / f"{run_folder.name}-from-{phase}"
    target, count = base, 1
    while target.exists():
        count += 1
        target = base.parent / f"{base.name}-{count}"

    return target


def create_branch(run_folder: Path,
               phase: int) -> Path:
    """The new run folder: `run_folder` copied whole, then taken back to `phase` - 1, with `branch.json` naming the run
    and the phase. Raises `ValueError` for a phase outside 1 to 6."""

    last = max(output_contract.PHASE_FILES)
    if not FIRST_BRANCH_PHASE <= phase <= last:
        raise ValueError(f"there is no phase {phase} to branch from: give {FIRST_BRANCH_PHASE} to {last}")

    # STEP 1: the whole run, images and media linked, every other file copied
    target = build_branch_folder(run_folder, phase)
    shutil.copytree(run_folder, target, copy_function=_copy_file, symlinks=True)

    # STEP 2: the copy taken back to the phase before the one branched from, by the reset's own code
    run_reset_service.reset_to_phase(RunPaths(repository=target.parent, module=target.name,
                                              output_root_override=target), phase - 1)

    # STEP 3: where the branch came from, for the detail view
    json_store_utils.write_json(target / BRANCH_FILE, {"from": run_folder.name, "phase": phase,
                                                       "at": datetime.now().isoformat(timespec="seconds")})

    return target


def read_branch(run_folder: Path) -> dict | None:
    """`{"from", "phase", "at"}` for a branched run, None for any other."""

    path = run_folder / BRANCH_FILE

    return json_store_utils.read_json(path) if path.is_file() else None


def is_rereading_recording(run_folder: Path) -> bool:
    """Whether the run in `run_folder` is a branch made to run phases 1 and 2 again (another part of its input, a PDF's
    processing mode changed): it reads the recording kept in its own folder."""

    branch = read_branch(run_folder) or {}

    return int(branch.get("phase") or 99) <= output_contract.PHASE_NUMBERS["frames_preparing"]
