"""A PDF whose contents page holds one title and page number a line, read by phase 1's real reader (DEFECTS 494): a
scanned report's contents, read that way, stayed after their heading went, and a chapter was written from them. Made
data: an invented two-page PDF built with pymupdf, its contents spaced one entry a block as the scan's reading was."""

import tempfile
from pathlib import Path

import pymupdf

from support import harness
from services import pdf_source_service

_ENTRIES = ["SUMMARY 1", "INTRODUCTION 1", "GATEWAY RETRIES 2"]


def _build_pdf() -> Path:
    path = Path(tempfile.mkdtemp()) / "acmesync-report.pdf"
    document = pymupdf.open()
    contents = document.new_page(width=595, height=842)
    contents.insert_text((72, 100), "CONTENTS", fontsize=22)
    for number, entry in enumerate(_ENTRIES):
        contents.insert_text((72, 160 + 40 * number), entry, fontsize=11)
    body = document.new_page(width=595, height=842)
    body.insert_text((72, 100), "SUMMARY", fontsize=22)
    body.insert_text((72, 160), "The gateway sends twelve retries before it gives up on a partner.", fontsize=11)
    document.save(path)

    return path


def test_contents_written_one_entry_a_line_leave_phase_1_defect():
    # the contents' lines reached phase 4 as text
    transcript, removed = pdf_source_service.build_page_transcript(_build_pdf(), is_read_as_pages=False)
    text = " ".join(segment["text"] for segment in transcript["segments"])
    harness.check("no contents entry is left", not any(entry in text for entry in _ENTRIES), text)
    harness.check("the report's own text stays", "twelve retries" in text, text)
    harness.check("each entry is recorded as removed", sum(entry in r["text"] for r in removed for entry in _ENTRIES) == 3,
                  str(removed))
