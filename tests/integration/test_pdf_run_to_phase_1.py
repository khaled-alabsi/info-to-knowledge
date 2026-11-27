"""A real `run --to 1` of a PDF read as text only (DEFECTS 488), the run command and its graph run in-process on the
invented notes PDF tests/resources/pdf/out/06-notes-no-headings.pdf: phase 2 was recorded failed, "02_speech.md is
missing ... run --phase 2 first", though the run was asked for phase 1 alone."""

import json
import tempfile
from pathlib import Path

from support import harness
import cli

PDF = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "out" / "06-notes-no-headings.pdf"


def test_a_pdf_run_to_phase_1_leaves_phase_2_unrecorded_defect():
    # phase 2 was recorded failed in a run that asked for phase 1 alone
    if not PDF.is_file():
        harness.check("the test PDFs are built (tests/resources/pdf/build_test_pdfs.py)", False, str(PDF))
        return
    output = Path(tempfile.mkdtemp()) / "notes"
    code = cli.main(["run", "--video", str(PDF), "--output", str(output), "--pdf-mode", "text-only", "--to", "1",
                     "--force"])
    records = sorted(output.glob("logs/*/run.json"))
    harness.check("the run wrote its record", bool(records), str(list(output.glob("**/*"))[:10]))
    if not records:
        return
    phases = json.loads(records[-1].read_text(encoding="utf-8"))["phases"]
    harness.check("phase 1 is complete", phases.get("transcribe") == "complete", str(phases))
    harness.check("phase 2 is not recorded failed", phases.get("frames_preparing") != "failed", str(phases))
    harness.check("the run ends without an error", code == 0, str(code))
