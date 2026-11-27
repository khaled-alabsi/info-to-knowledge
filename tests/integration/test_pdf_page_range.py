"""A real `run --pages 3-5 --to 2` of the invented slide export tests/resources/pdf/out/03-slides-export.pdf (ten pages), the run command and its graph run in-process, read as pages as images so phase 2 renders frames:
only pages 3 to 5 are read and shown, each keeping its own number and minute."""

import json
import tempfile
from pathlib import Path

from support import harness
import cli

from models import output_contract
from services import pdf_source_service

PDF = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "out" / "03-slides-export.pdf"


def test_a_pdf_run_on_pages_3_to_5_reads_and_shows_those_pages_alone():
    if not PDF.is_file():
        harness.check("the test PDFs are built (tests/resources/pdf/build_test_pdfs.py)", False, str(PDF))
        return
    output = Path(tempfile.mkdtemp()) / "slides"
    code = cli.main(["run", "--video", str(PDF), "--output", str(output), "--pdf-mode", "pages-as-images",
                     "--pages", "3-5", "--to", "2", "--force"])
    harness.check("the run ends without an error", code == 0, str(code))
    harness.check("the range is kept in the run folder", json.loads(
        (output / output_contract.RUN_INPUT_RANGE_FILE).read_text(encoding="utf-8")) == {"firstPage": 3, "lastPage": 5})
    transcript = json.loads((output / "01_transcript.json").read_text(encoding="utf-8"))
    pages = sorted({segment["page"] for segment in transcript["segments"]})
    harness.check("phase 1 read pages 3 to 5 alone", pages == [3, 4, 5], str(pages))
    harness.check("each at its own minute", min(segment["start"] for segment in transcript["segments"])
                  >= 2 * pdf_source_service.PAGE_SECONDS, str(transcript["segments"][:1]))
    frames = json.loads((output / "02_frames.json").read_text(encoding="utf-8"))
    harness.check("phase 2 shows three pages, by their own numbers",
                  [item["page"] for item in frames["items"]] == [3, 4, 5], str([item.get("page") for item in frames["items"]]))
    harness.check("each frame hears its own page", all(item["transcriptCurrent"].strip() for item in frames["items"]),
                  str([item["transcriptCurrent"][:40] for item in frames["items"]]))
    images = sorted(path.name for path in (output / output_contract.FRAME_IMAGES_DIR_NAME).glob("frame-*.jpg"))
    harness.check("three images written", images == ["frame-000.jpg", "frame-001.jpg", "frame-002.jpg"], str(images))


def test_a_page_range_past_the_pdf_s_end_is_refused_before_the_run_starts():
    if not PDF.is_file():
        return
    output = Path(tempfile.mkdtemp()) / "slides"
    code = cli.main(["run", "--video", str(PDF), "--output", str(output), "--pages", "12-14", "--to", "1", "--force"])
    harness.check("refused", code == 2, str(code))
    harness.check("no run record written", not list(output.glob("logs/*/run.json")), str(list(output.glob("**/*"))))
