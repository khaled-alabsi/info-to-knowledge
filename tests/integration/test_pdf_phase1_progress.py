"""A PDF's phase 1 followed step by step, run whole through `i2k run --phase 1` on a
made PDF of 8 pages (every word invented): each step of the reading is its own stage in `transcribe.json`, counted page
by page, in order, the reader's second walk over the pages (its markdown) among them, and a step the PDF never needed
(it has no scanned page) marked skipped. The console says how far a step is at its last page."""

import shutil
import tempfile
from pathlib import Path

import pymupdf

from support import harness

import cli
from services import frames_preparing_progress_service as progress_service
from services import recording_hash_service

PAGES = 8


def _build_pdf(folder: Path) -> Path:
    document = pymupdf.open()
    for number in range(PAGES):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 80), f"Refund rule {number + 1}", fontsize=20)
        page.insert_text((72, 120), f"A refund of tenant {number + 1} is paid within ten days.", fontsize=11)
    path = folder / "refund-rules.pdf"
    document.save(path)

    return path


def test_a_pdf_s_phase_1_counts_every_step_of_its_reading_page_by_page_defect():
    # Defect: a 6,761-page PDF stood at 6,761 of 6,761 for hours: phase 1 counted only the reader's first walk over the
    # pages. Fix: each step of the reading is its own stage, counted page by page, the second walk ("markdown") too
    root = Path(tempfile.mkdtemp())
    recording_hash_service.set_cache_path(root / "hashes.json")
    pdf = _build_pdf(root)
    run = root / "run"
    code = cli.main(["run", "--force", "--pdf-mode", "markdown-conversion", "--phase", "1", "--video", str(pdf),
                     "--output", str(run)])
    harness.check("phase 1 ends well", code == 0, str(code))
    run_dir = next((run / "logs").glob("*/run.json")).parent
    stages = progress_service.read_progress(run_dir, progress_service.TRANSCRIBE_PROGRESS_FILE)
    shown = [(stage["name"], stage["status"], stage["done"], stage["total"]) for stage in stages]
    harness.check("every step counted all 8 pages in order, the scanned pages skipped", shown == [
        ("text layer", "done", PAGES, PAGES), ("figures", "done", PAGES, PAGES), ("parsing", "done", PAGES, PAGES),
        ("markdown", "done", PAGES, PAGES), ("scanned pages", "skipped", 0, None),
        ("page cleaning", "done", PAGES, PAGES)], str(shown))
    harness.check("no stage is left running", all(stage["seconds_since_progress"] is None for stage in stages),
                  str(stages))
    console = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.glob("*.log"))
    harness.check("the console says each step's last page with its pace",
                  f"markdown: page {PAGES} of {PAGES}," in console and "s a page" in console, console[-1500:])
    shutil.rmtree(root, ignore_errors=True)
