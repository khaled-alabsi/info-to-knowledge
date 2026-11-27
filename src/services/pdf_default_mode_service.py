"""The processing a PDF takes when its run chooses none: `pdf.defaultMode`, or with
"by-size", by the PDF's page count. A small PDF is read as pages as images, the richest; a large one takes the
markdown conversion, since no model writing conversation holds it whole; any other is read as text only. The run line
and the run form read the same rule, so a run started from the inbox takes what the form shows as its default."""

from __future__ import annotations

from pathlib import Path

import pymupdf

import config
from models import output_contract

__all__ = ["BY_SIZE", "count_pages", "choose_mode_for_pages", "choose_default_mode"]

BY_SIZE = "by-size"


def count_pages(path: Path) -> int:
    with pymupdf.open(path) as document:
        page_count = document.page_count

    return page_count


def choose_mode_for_pages(page_count: int,
                          settings: config.PdfSettings) -> str:
    """The mode `settings.default_mode` gives a PDF of `page_count` pages."""

    if settings.default_mode != BY_SIZE:
        return settings.default_mode
    if page_count <= settings.images_page_limit:
        return output_contract.PDF_MODE_PAGES
    if page_count > settings.model_writing_page_limit:
        return output_contract.PDF_MODE_MARKDOWN_CONVERSION

    return output_contract.PDF_MODE_TEXT


def choose_default_mode(path: Path,
                        settings: config.PdfSettings) -> str:
    """The mode a PDF at `path` takes when its run chooses none; the page count is read only for "by-size"."""

    page_count = count_pages(path) if settings.default_mode == BY_SIZE else 0

    return choose_mode_for_pages(page_count=page_count, settings=settings)
