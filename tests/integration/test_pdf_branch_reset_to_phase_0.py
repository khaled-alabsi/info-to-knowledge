"""A PDF run branched from phase 1 by the real branch service, then reset to phase 0 by the real reset, as the page's
reset does (DEFECTS 490): a branch made to read its PDF again was refused ("move it back to the inbox first"),
though its run line reads the PDF kept in its folder. Made data: an invented PDF's bytes and a run folder that keeps
it, its phase 1 and 2 files written."""

import json
import tempfile
from pathlib import Path

from support import harness
from services import run_branch_service
from services import run_operations_service


def _build_run_keeping_its_pdf() -> Path:
    """A run folder that read its PDF and keeps it in `recording/`, as a finished run does."""

    folder = Path(tempfile.mkdtemp()) / "acmesync-guide"
    (folder / "recording").mkdir(parents=True)
    (folder / "recording" / "acmesync-guide.pdf").write_bytes(b"%PDF-1.4 invented guide")
    (folder / "recording" / ".origin.json").write_text(json.dumps({"name": "acmesync-guide.pdf"}), encoding="utf-8")
    (folder / "01_transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    (folder / "02_frames.json").write_text(json.dumps({"items": [], "medium": "pdf-text-only"}), encoding="utf-8")

    return folder


def test_a_real_branch_from_phase_1_is_reset_to_phase_0_defect():
    # the branch's reset was refused, so its PDF could not be read again
    branch = run_branch_service.create_branch(_build_run_keeping_its_pdf(), 1)
    result = run_operations_service.reset_run_to_phase(branch, 0, True)
    harness.check("the reset goes ahead", result.exit_code == 0, result.output)
    harness.check("phase 1's file is removed", not (branch / "01_transcript.json").exists(), result.output)
    harness.check("the PDF stays in the branch", (branch / "recording" / "acmesync-guide.pdf").exists(), result.output)
