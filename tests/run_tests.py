#!/usr/bin/env python3
# <!-- sync: seed/run_tests v1 -->
"""Offline test runner. No model host, no network, no agent binary.

ORDER puts the cheapest and most diagnostic first: a layout or contract-coherence failure explains
every other failure, so seeing it first saves reading the rest.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from support import harness  # noqa: E402
from services import inbox_tags_service  # noqa: E402
from prompts import system_prompt_builder  # noqa: E402
from services import instruction_store_service  # noqa: E402
from services import recording_hash_service  # noqa: E402
from services import text_detection_service  # noqa: E402

ORDER = ("test_layout_conventions", "test_contract_coherence", "test_graph_topology")

# NEVER make a test wait on the clock: no `sleep`, no fixed delay, no poll with a pause. Wait on the readiness signal
# with a hard failure limit, or inject the clock. And a name given on the command line runs THAT file only: a runner
# that ignores it cost 1:47 a call where the named file costs 15 s, on every call of a session.


# Integration tests run apart: they run the real face detector and text reader on whole images (scenes rendered from
# HTML, real meeting stills), take tens of seconds, and only a change to frame extraction, cropping, face or text
# detection, or the reading of names can move them. `--integration` runs them; a name given on the command line is found
# in either folder.
CASE_DIRS = ("cases", "integration")


def discover(wanted: list[str] = (),
             is_integration: bool = False) -> list[str]:
    """Every case file of `cases/`, the ones in ORDER first, as `<folder>.<name>`; with `is_integration`, every one of
    `integration/`; or only the ones named on the command line, from either folder, so one file's checks can be run
    in a second rather than the whole suite's hundred."""

    found = {path.stem: f"{folder}.{path.stem}" for folder in CASE_DIRS for path in (ROOT / folder).glob("test_*.py")}
    if wanted:
        asked = [name if name.startswith("test_") else f"test_{name}" for name in wanted]
        chosen = [found[name] for name in asked if name in found]
        missing = [name for name in asked if name not in found]
        if missing:
            print(f"no case file named: {', '.join(missing)}", file=sys.stderr)
        return chosen
    folder = "integration" if is_integration else "cases"
    names = sorted(name for name, module in found.items() if module.startswith(f"{folder}."))
    ordered = [n for n in ORDER if n in names]

    return [found[n] for n in ordered + [n for n in names if n not in ordered]]


def run_case(func,
             name: str) -> None:
    """One test function, its crash a failing check, with the process's settings given back as they were before it.

    A case that starts a new video run in this process makes the run lay its default speed (Balanced) over the settings,
    which switches the review off: every case file after it read phase 5 as off, and 22 review checks failed whenever
    the whole suite ran in one process, never when their file ran alone."""

    settings = config.settings.model_copy(deep=True)
    profile_name, worker_set_name = config.PROFILE_NAME, config.WORKER_SET_NAME
    try:
        func()
    except Exception:
        # A crash is a failing test, not a stop: the other cases still have information.
        harness.check(name, False, traceback.format_exc().strip().splitlines()[-1])
    finally:
        config.settings = settings
        config.PROFILE_NAME, config.WORKER_SET_NAME = profile_name, worker_set_name


def main() -> int:
    verbose = "-v" in sys.argv
    wanted = [argument for argument in sys.argv[1:] if not argument.startswith("-")]
    harness.reset()
    # Recording hashes go to a file of the suite's own, never the real ui/recording-hashes.json
    recording_hash_service.set_cache_path(Path(tempfile.mkdtemp()) / "recording-hashes.json")
    # So do an inbox video's tags, never the real ui/inbox-tags.json
    inbox_tags_service.set_store_path(Path(tempfile.mkdtemp()) / "inbox-tags.json")
    # And the instructions a person edited on the page: the tests read the shipped defaults, never this workspace's edits
    instruction_store_service.set_store_dir(Path(tempfile.mkdtemp()) / "instructions")
    # A case that builds a prompt outside a run builds the video workflow's, as a video run does
    system_prompt_builder.set_run_workflow("video")
    # Phase 2's model filters off, as in code: this machine's settings turn them on, and every case file that runs phase
    # 2 without asking for them called the real redundancy model, which LM Studio then loaded beside a run (DEFECTS 82)
    config.settings.frames.is_redundancy_filter_on = False
    config.settings.frames.is_similarity_recheck_on = False
    # The words Tesseract reads on a frame are kept on disk by the frame's content, in the git-ignored real media: five
    # integration files read every frame of a real recording again in each run, 1.3 to 5 s a frame (DEFECTS 499)
    if "--integration" in sys.argv:
        text_detection_service.set_words_cache_dir(ROOT / "real_media" / ".words-cache")
    chosen = discover(wanted, is_integration="--integration" in sys.argv)
    if wanted and not chosen:
        return 2
    for module_name in chosen:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            # A case file that does not IMPORT is one failing test, not a dead run. Without this the
            # runner exits on a bare traceback with no summary line at all, so one broken import
            # hides every result the other case files still hold - and "the suite printed nothing"
            # reads like a tooling problem rather than like a defect in the package under test.
            harness.check(f"{module_name} imports", False,
                          traceback.format_exc().strip().splitlines()[-1])
            continue
        for attr in sorted(dir(module)):
            if not attr.startswith("test_"):
                continue
            func = getattr(module, attr)
            if not callable(func):
                continue
            run_case(func, f"{module_name}.{attr}")
    if verbose:
        for name, _ in harness.PASSED:
            print(f"  pass  {name}")
    for name, detail in harness.FAILED:
        print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))
    total = len(harness.PASSED) + len(harness.FAILED)
    print(f"\n{len(harness.PASSED)} passed, {len(harness.FAILED)} failed, of {total}")

    return 1 if harness.FAILED else 0


def run_in_own_temp_folder() -> int:
    """The suite with every temporary file of its own under one folder, removed when it ends: case files make
    folders with `tempfile` and never remove them, and a session of suite runs filled the disk with 78,679 of them
    (5.9 GB) while a model run was writing."""

    root = tempfile.mkdtemp(prefix="i2k-tests-")
    tempfile.tempdir = root
    # A command a case starts makes its temporary files there too
    os.environ["TMPDIR"] = root
    try:
        return main()
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run_in_own_temp_folder())
