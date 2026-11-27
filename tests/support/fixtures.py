# <!-- sync: seed/support.fixtures v1 -->
"""Throwaway trees for the offline suite. No model host, no network, no repository.

One place that knows how to build a `RunPaths` over a temporary directory, so a test that needs a
run tree does not grow its own copy of the layout - which is how a layout change breaks eight tests
in eight different ways.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import config as app_config
from models.run_paths import RunPaths
from services import run_paths_service


# A run's record with every phase a reader's document needs complete
DONE_PHASES = {"transcribe": "complete", "frames_preparing": "complete", "frames_processing": "complete",
               "finalize": "complete", "review": "complete"}

def make_paths(tmp: Path | None = None,
               module: str = "fixture") -> RunPaths:
    """A prepared run tree under `tmp`, or under a fresh temporary directory, for the recording `module`."""

    root = Path(tmp) if tmp is not None else Path(tempfile.mkdtemp())
    paths = RunPaths(repository=root / "repo", module=module)
    run_paths_service.ensure_run_paths(paths)
    # The run creates every artifact directory before a worker starts; the write tool refuses to
    # invent one. The suite's one-item jobs write under `items/`, so it exists here too.
    (paths.output_root / "items").mkdir(exist_ok=True)

    return paths


def item_artifact(paths: RunPaths, slug: str) -> Path:
    """Where the offline suite's one-item jobs write. The generated playbook names its real artifact
    layout in `RunPaths`; the engine tests need only SOME path under the output root."""

    return paths.output_root / "items" / f"{slug}.md"


@contextlib.contextmanager
def critique_switches(is_critique_on: bool = True,
                      is_critique_repair_on: bool = True,
                      is_pdf_critique_on: bool = False) -> Iterator[None]:
    """Phase 3's critique switches set for a block and put back after it: a test counting a batch's calls states
    them, never inherits this machine's settings files."""

    work = app_config.settings.frames_processing
    previous = (work.is_critique_on, work.is_critique_repair_on, work.is_pdf_critique_on)
    work.is_critique_on, work.is_critique_repair_on, work.is_pdf_critique_on = (is_critique_on, is_critique_repair_on,
                                                                                is_pdf_critique_on)
    try:
        yield
    finally:
        work.is_critique_on, work.is_critique_repair_on, work.is_pdf_critique_on = previous


@contextlib.contextmanager
def single_frame_attempts(count: int) -> Iterator[None]:
    """Phase 3's `framesProcessing.singleFrameAttempts` set for a block and put back after it."""

    work = app_config.settings.frames_processing
    previous = work.single_frame_attempts
    work.single_frame_attempts = count
    try:
        yield
    finally:
        work.single_frame_attempts = previous


@contextlib.contextmanager
def cuts_per_frame(limit: int) -> Iterator[None]:
    """Phase 4's `budgets.cutsPerFrame` set for a block and put back after it: a test of the rule at a limit states the
    limit, never the number the shipped settings hold (strict 3, the default settings 5)."""

    budgets = app_config.settings.budgets
    previous = budgets.cuts_per_frame
    budgets.cuts_per_frame = limit
    try:
        yield
    finally:
        budgets.cuts_per_frame = previous


@contextlib.contextmanager
def max_attempts(limit: int) -> Iterator[None]:
    """`budgets.maxAttempts` set for a block and put back after it: a test of a step's retries states the budget,
    never the number the shipped settings hold (strict 1, the default settings 3)."""

    budgets = app_config.settings.budgets
    previous = budgets.max_attempts
    budgets.max_attempts = limit
    try:
        yield
    finally:
        budgets.max_attempts = previous


@contextlib.contextmanager
def undos_per_frame(limit: int) -> Iterator[None]:
    """Phase 4's `budgets.undosPerFrame` set for a block and put back after it."""

    budgets = app_config.settings.budgets
    previous = budgets.undos_per_frame
    budgets.undos_per_frame = limit
    try:
        yield
    finally:
        budgets.undos_per_frame = previous
