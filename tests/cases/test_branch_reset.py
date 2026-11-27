"""A run's reset to phase 0 or 1 while its recording is kept in its folder: a run
that finished keeps it there only to be watched, so phases 1 and 2 wait for it back in the inbox; a branch made to run
them again reads it there, so its reset goes ahead, as its run line does."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from support import harness

from services import run_branch_service
from services import run_operations_service


def _build_run_folder(branch_phase: int | None) -> Path:
    """A run folder that read its PDF, kept in its own `recording/`, a branch from `branch_phase` when given."""

    folder = Path(tempfile.mkdtemp()) / "guide"
    (folder / "recording").mkdir(parents=True)
    (folder / "recording" / "guide.pdf").write_bytes(b"%PDF-1.4 invented")
    (folder / "recording" / ".origin.json").write_text(json.dumps({"name": "guide.pdf"}), encoding="utf-8")
    (folder / "01_transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    if branch_phase is not None:
        (folder / run_branch_service.BRANCH_FILE).write_text(
            json.dumps({"from": "guide-original", "phase": branch_phase, "at": "2026-11-01T12:00:00"}), encoding="utf-8")

    return folder


def test_a_branch_made_to_read_its_pdf_again_is_reset_to_phase_0_defect():
    # Reset to phase 0 refused a branch made from phase 1 ("move it back to the inbox first"), though its run line
    # reads the recording kept in its folder. Now the reset makes the same exception as the run line
    result = run_operations_service.reset_run_to_phase(_build_run_folder(branch_phase=1), 0, False)
    harness.check("listed, not refused", result.exit_code == 0 and not any("inbox" in line for line in result.lines),
                  result.output)


def test_a_finished_run_keeping_its_recording_still_waits_for_it_in_the_inbox():
    result = run_operations_service.reset_run_to_phase(_build_run_folder(branch_phase=None), 0, False)
    harness.check("refused, naming the move back", result.exit_code == 2 and "inbox" in result.output, result.output)
    result = run_operations_service.reset_run_to_phase(_build_run_folder(branch_phase=4), 0, False)
    harness.check("a branch from phase 4 waits too", result.exit_code == 2, str(result.output))
