"""A PDF's default processing by its size: a small one read as pages as images, a
large one taken by the markdown conversion, any other read as text only."""

import tempfile
from pathlib import Path

import pymupdf

from support import harness

import config
from services import pdf_default_mode_service
from services import run_status_service


def _make_pdf(folder: Path,
              page_count: int) -> Path:
    path = folder / f"pages-{page_count}.pdf"
    document = pymupdf.open()
    for number in range(page_count):
        page = document.new_page()
        page.insert_text((72, 72), f"Gateway page {number + 1}")
    document.save(path)
    document.close()

    return path


def test_a_pdf_takes_its_default_processing_by_its_page_count():
    folder = Path(tempfile.mkdtemp())
    settings = config.PdfSettings(default_mode="by-size", images_page_limit=20, model_writing_page_limit=300)
    chosen = {count: pdf_default_mode_service.choose_default_mode(path=_make_pdf(folder, count), settings=settings)
              for count in (3, 20, 21, 300, 301)}
    harness.check("up to 20 pages as images, up to 300 text only, more the markdown conversion", chosen == {
        3: "pages-as-images", 20: "pages-as-images", 21: "text-only", 300: "text-only", 301: "markdown-conversion"},
        str(chosen))


def test_one_mode_for_every_pdf_reads_no_page_count():
    settings = config.PdfSettings(default_mode="text-only")
    harness.check("a named mode is taken whatever the size",
                  pdf_default_mode_service.choose_default_mode(path=Path("/no/such.pdf"), settings=settings)
                  == "text-only")


def test_the_inbox_lists_a_pdfs_pages_for_the_run_form():
    folder = Path(tempfile.mkdtemp())
    (folder / "inbox").mkdir()
    _make_pdf(folder / "inbox", 7).rename(folder / "inbox" / "Gateway guide.pdf")
    items = run_status_service.list_inbox_items(inbox_root=folder / "inbox", document_root=folder / "output")
    harness.check("the PDF's item says 7 pages", [item["page_count"] for item in items] == [7], str(items))


def test_both_default_settings_files_choose_by_size():
    defaults = config.settings.pdf
    harness.check("the owner's default is by size", defaults.default_mode == "by-size"
                  and defaults.images_page_limit == 20 and defaults.model_writing_page_limit == 300, str(defaults))
