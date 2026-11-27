"""Scanned PDF pages read by Unlimited-OCR-2: the model's blocks turned into a page's
markdown, its chrome and its figures; a scan told from a page with words drawn in ink; phase 1 handing only its
scanned pages to the engine the run chose; and the setup check naming the tool when a setting needs it. The model is
stood in for by a script that writes a reading of invented words for each page it is given."""

from __future__ import annotations

import functools
import json
import logging
import shutil
import stat
import tempfile
from pathlib import Path

import pymupdf

from support import harness

import config as app_config
from graph.nodes import transcribe_audio_node
from services import frames_preparing_progress_service as progress_service
from services import knowledge_filter_service
from services import pdf_source_service
from services import setup_check_service
from services import unlimited_ocr_reader_service
from utils import html_table_utils
from utils import unlimited_ocr_block_utils

READING = ("<PAGE><BLOCK>header[80, 30, 600, 45]<FORK>AcmeSync Wiki · Gateway\n"
           "<BLOCK>title<LEVEL2>[80, 60, 400, 80]<FORK>Refund rules\n"
           "<BLOCK>text[80, 90, 900, 130]<FORK>A refund above 2,000 EUR is answered later.\n"
           "<BLOCK>table[80, 140, 900, 300]<FORK><table><tr><td>Tenant</td><td>Region</td></tr>"
           "<tr><td>Tenant 16</td><td>Dublin</td></tr></table>\n"
           "<BLOCK>image[100, 400, 600, 700]<FORK>\n"
           "<BLOCK>image_caption[100, 705, 600, 720]<FORK>Refunds per month\n"
           "<BLOCK>image[300, 900, 320, 915]<FORK>\n"
           "<BLOCK>footer[80, 950, 400, 965]<FORK>Printed from AcmeSync Wiki\n")


def _write_fake_tool(reading: str,
                     calls: Path) -> Path:
    """A stand-in for the tool's Python: given `read_pages.py --model M --out OUT IMAGE...`, it prints a line per image
    as the tool does ("page-0002.png: 1.0 s"), writes `reading` for every image into OUT and appends the images' names
    to `calls`."""

    folder = Path(tempfile.mkdtemp())
    (folder / "reading.txt").write_text(reading, encoding="utf-8")
    script = folder / "python"
    script.write_text("#!/bin/sh\n"
                      'out="$5"; shift 5\n'
                      f'echo "$@" >> "{calls}"\n'
                      'for image in "$@"; do echo "loading weights"; echo "$(basename "$image"): 1.0 s"; done\n'
                      f'/usr/bin/python3 -c \'import json,sys; t=open("{folder / "reading.txt"}").read(); '
                      'json.dump([{"image": i, "text": t, "seconds": 1.0, "finishReason": "stop", "isReadAgain": False} '
                      'for i in sys.argv[2:]], open(sys.argv[1], "w"))\' "$out" "$@"\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    return script


def _build_pdf_with_scan() -> Path:
    """Page 1 written in text; page 2 a picture filling the page with no word in ink, as a scanner writes it."""

    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Overview of the gateway, written in text")
    scan = document.new_page()
    picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 520), False)
    picture.clear_with(230)
    scan.insert_image(scan.rect, pixmap=picture)
    path = Path(tempfile.mkdtemp()) / "partner-guide.pdf"
    document.save(path)

    return path


def test_the_model_s_blocks_become_markdown_chrome_and_figures():
    markdown, chrome, figures = unlimited_ocr_block_utils.read_page(READING)
    harness.check("a title is a heading of its level", "## Refund rules" in markdown, markdown)
    harness.check("a table is a markdown table", "| Tenant 16 | Dublin |" in markdown, markdown)
    harness.check("the header and the footer are chrome, out of the text",
                  chrome == ["AcmeSync Wiki · Gateway", "Printed from AcmeSync Wiki"]
                  and "Printed from" not in markdown, f"{chrome} {markdown}")
    harness.check("the picture is a figure with its caption; the tiny icon is none",
                  figures == [{"box": [100.0, 400.0, 600.0, 700.0], "caption": "Refunds per month"}]
                  and "Refunds per month" not in markdown, str(figures))


def test_a_reading_in_no_block_shape_is_its_own_text():
    markdown, chrome, figures = unlimited_ocr_block_utils.read_page("<PAGE>Plain words read off a page.")
    harness.check("kept as it is", (markdown, chrome, figures) == ("Plain words read off a page.", [], []),
                  repr(markdown))


def test_a_scan_is_a_page_a_picture_fills_with_no_word_in_ink():
    with pymupdf.open(_build_pdf_with_scan()) as document:
        harness.check("the text page is no scan", not pdf_source_service.is_scanned_page(document[0]))
        harness.check("the picture page is a scan", pdf_source_service.is_scanned_page(document[1]))
        document[1].insert_text((72, 72), "Poster title drawn in ink")
        harness.check("a page whose words are drawn in ink over its picture is no scan",
                      not pdf_source_service.is_scanned_page(document[1]))
        document[0].insert_text((72, 200), "invisible OCR layer", render_mode=3)
        harness.check("a text page stays no scan", not pdf_source_service.is_scanned_page(document[0]))


def test_an_invisible_text_layer_keeps_a_scan_a_scan():
    # A searchable scan carries an old OCR's words in invisible ink (the NASA report's, garbled); it is still read as
    # a scan
    with pymupdf.open(_build_pdf_with_scan()) as document:
        document[1].insert_text((72, 72), "1NTRoDucTION garbled", render_mode=3)
        harness.check("still a scan", pdf_source_service.is_scanned_page(document[1]))


def test_only_the_scanned_page_is_read_by_the_engine_and_its_picture_is_a_figure():
    calls = Path(tempfile.mkdtemp()) / "calls.txt"
    previous = app_config.settings.pdf.unlimited_ocr_python
    app_config.settings.pdf.unlimited_ocr_python = str(_write_fake_tool(READING, calls))
    try:
        transcript, removed = pdf_source_service.build_page_transcript(
            _build_pdf_with_scan(), is_read_as_pages=False,
            ocr_engine=pdf_source_service.UNLIMITED_OCR_ENGINE)
    finally:
        app_config.settings.pdf.unlimited_ocr_python = previous
    page_two = " ".join(segment["text"] for segment in transcript["segments"] if segment["page"] == 2)
    called_images = [Path(image).name for image in calls.read_text().split()]
    harness.check("one call with the scanned page alone", called_images == ["page-0002.png"], calls.read_text())
    harness.check("the scan's text is the model's", "A refund above 2,000 EUR" in page_two, page_two)
    harness.check("the text page keeps its own text", any("Overview of the gateway" in segment["text"]
                                                          for segment in transcript["segments"]))
    harness.check("the pages read are named", transcript.get("ocrPages") == [2]
                  and transcript.get("ocrEngine") == "unlimited-ocr", str(transcript.get("ocrPages")))
    chrome = [entry for entry in removed if entry["rule"].startswith("Unlimited-OCR-2")]
    harness.check("its header and footer are written into the cleanup", [entry["page"] for entry in chrome] == [2, 2],
                  str(chrome))
    figures = transcript.get("figures") or []
    # the page is 595 x 842 points: the grid's 100..600 by 400..700 is 59.5..357 by 336.8..589.4
    harness.check("its picture is a figure of page 2, its box in points, with its caption",
                  [(figure["page"], figure["box"], figure["caption"]) for figure in figures]
                  == [(2, [59.5, 336.8, 357.0, 589.4], "Refunds per month")], str(figures))


def _follow_pages(run_dir: Path,
                  told: list[tuple[int, int, str]],
                  read: int,
                  total: int,
                  step: str) -> None:
    """What phase 1 does with each page count, the count kept in `told` as well."""

    told.append((read, total, step))
    transcribe_audio_node._report_pages(run_dir, logging.getLogger("test"), read, total, step)


def test_phase_1_counts_the_scanned_pages_as_the_tool_reads_them_defect():
    # Defect: while Unlimited-OCR-2 read a PDF's scans, 8 to 20 s a page, phase 1's progress said only "waiting"
    # Fix: each page the tool prints as read is counted into phase 1's progress file, in scanned pages
    calls = Path(tempfile.mkdtemp()) / "calls.txt"
    run_dir = Path(tempfile.mkdtemp())
    progress_service.open_progress(run_dir, progress_service.PDF_STAGES, progress_service.TRANSCRIBE_PROGRESS_FILE)
    progress_service.start_stage(run_dir, progress_service.TEXT_LAYER, None, progress_service.TRANSCRIBE_PROGRESS_FILE)
    told: list[tuple[int, int, str]] = []
    previous = app_config.settings.pdf.unlimited_ocr_python
    app_config.settings.pdf.unlimited_ocr_python = str(_write_fake_tool(READING, calls))
    try:
        pdf_source_service.build_page_transcript(_build_pdf_with_scan(), ocr_engine="unlimited-ocr",
                                                 on_pages=functools.partial(_follow_pages, run_dir, told))
    finally:
        app_config.settings.pdf.unlimited_ocr_python = previous
    scans = [entry for entry in told if entry[2] == "scanned pages"]
    harness.check("told before the first page and after the one scan, other lines not counted",
                  scans == [(0, 1, "scanned pages"), (1, 1, "scanned pages")], str(told))
    stages = {stage["name"]: stage for stage in progress_service.read_progress(
        run_dir, progress_service.TRANSCRIBE_PROGRESS_FILE)}
    stage = stages["scanned pages"]
    harness.check("the progress file counts it as its own stage, in pages",
                  (stage["done"], stage["total"], stage["unit"]) == (1, 1, "pages"), str(stage))


def test_tesseract_hands_nothing_to_the_engine():
    calls = Path(tempfile.mkdtemp()) / "calls.txt"
    previous = app_config.settings.pdf.unlimited_ocr_python
    app_config.settings.pdf.unlimited_ocr_python = str(_write_fake_tool(READING, calls))
    try:
        transcript, _removed = pdf_source_service.build_page_transcript(_build_pdf_with_scan())
    finally:
        app_config.settings.pdf.unlimited_ocr_python = previous
    harness.check("no call, no page named", not calls.exists() and "ocrPages" not in transcript)


def test_a_failing_tool_is_named():
    folder = Path(tempfile.mkdtemp())
    script = folder / "python"
    script.write_text("#!/bin/sh\necho 'the model was not found' >&2\nexit 3\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    previous = app_config.settings.pdf.unlimited_ocr_python
    app_config.settings.pdf.unlimited_ocr_python = str(script)
    try:
        unlimited_ocr_reader_service.read_pages([folder / "page-0001.png"])
        harness.check("the failure raised", False)
    except RuntimeError as error:
        harness.check("its exit and last line named", "exit 3" in str(error) and "the model was not found" in str(error),
                      str(error))
    finally:
        app_config.settings.pdf.unlimited_ocr_python = previous


def test_a_run_s_chosen_engine_wins_over_the_setting():
    folder = Path(tempfile.mkdtemp())
    harness.check("no choice is the setting's", pdf_source_service.read_chosen_ocr_engine(folder)
                  == app_config.settings.pdf.ocr_engine)
    pdf_source_service.write_chosen_ocr_engine(folder, "tesseract")
    harness.check("the run's choice", pdf_source_service.read_chosen_ocr_engine(folder) == "tesseract")
    (folder / "run-pdf-ocr-engine.json").write_text(json.dumps({"ocrEngine": "paper"}), encoding="utf-8")
    harness.check("an unknown recorded engine reads as the setting's",
                  pdf_source_service.read_chosen_ocr_engine(folder) == app_config.settings.pdf.ocr_engine)
    try:
        pdf_source_service.write_chosen_ocr_engine(folder, "paper")
        harness.check("an unknown engine is refused", False)
    except ValueError as error:
        harness.check("refused by name", "paper" in str(error), str(error))


def test_a_scanned_page_s_html_table_becomes_a_markdown_table():
    table = html_table_utils.render_markdown_table(
        "<table><tr><th>Tenant</th><th colspan='2'>Region and share</th></tr>"
        "<tr><td>Tenant 16</td><td>Dublin</td><td>a|b</td></tr><tr><td></td><td></td><td></td></tr></table>")
    harness.check("its first row the header, a spanned cell followed by an empty one, a pipe escaped, an empty row left out",
                  table == "| Tenant | Region and share |  |\n|---|---|---|\n| Tenant 16 | Dublin | a\\|b |", table)
    harness.check("a table with no row is nothing", html_table_utils.render_markdown_table("<table></table>") == "")


def test_a_tool_s_environment_python_is_started_by_its_link_defect():
    # The tool ran without its packages: the setting's environment python, a link to the bare interpreter, was
    # followed to that interpreter. Now a relative setting is joined to the playbook folder and its links kept
    folder = Path(tempfile.mkdtemp(dir=app_config.PLAYBOOK_ROOT / "tools"))
    try:
        (folder / "bin").mkdir()
        (folder / "bin" / "python").symlink_to("/usr/bin/python3")
        relative = str((folder / "bin" / "python").relative_to(app_config.PLAYBOOK_ROOT))
        resolved = knowledge_filter_service.resolve_playbook_path(relative)
        harness.check("the link itself, inside the environment", resolved == folder / "bin" / "python", str(resolved))
        harness.check("its .. taken out", knowledge_filter_service.resolve_playbook_path(
            f"tools/../{relative}") == folder / "bin" / "python")
    finally:
        shutil.rmtree(folder)


def test_the_setup_check_needs_the_tool_only_while_the_setting_names_it():
    settings = app_config.settings.model_copy(deep=True)
    previous = app_config.settings.pdf.unlimited_ocr_python
    app_config.settings.pdf.unlimited_ocr_python = "/no/such/python"
    setup_check_service.set_settings_reader(lambda: settings)
    try:
        settings.pdf.ocr_engine = "unlimited-ocr"
        tool = next(row for row in setup_check_service.check_setup()["runtimes"] if row["key"] == "unlimited-ocr")
        harness.check("needed and missing", tool["is_needed"] and not tool["is_installed"], str(tool))
        settings.pdf.ocr_engine = "tesseract"
        tool = next(row for row in setup_check_service.check_setup()["runtimes"] if row["key"] == "unlimited-ocr")
        harness.check("not needed with Tesseract", not tool["is_needed"], str(tool))
    finally:
        app_config.settings.pdf.unlimited_ocr_python = previous
        setup_check_service.set_settings_reader(setup_check_service.settings_service.read_next_job_settings)
