"""A PDF's chapter request, built from a made PDF read by phase 1's real reader in a run folder phase 2 marked a PDF
(DEFECTS 493): its paragraphs were listed as `[speech]`, called speech lines, and sent with the meeting's naming rule,
so the writer called a table's lead-in a speech line and weighed the rule against an author the report thanks. Made
data: an invented one-page report, its acknowledgement naming an invented person."""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from support import fixtures as fx
from support import harness
from models import output_contract
from prompts import document_messages
from services import pdf_source_service, phase_file_service

_THANKS = "Acknowledgement is made to Jonas Acme for his guidance of the gateway tests."


def _build_pdf() -> Path:
    path = Path(tempfile.mkdtemp()) / "acmesync-gateway-report.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "HISTORY", fontsize=22)
    page.insert_text((72, 160), "The gateway sends twelve retries before it gives up on a partner.", fontsize=11)
    page.insert_text((72, 200), _THANKS, fontsize=11)
    document.save(path)

    return path


def test_a_pdf_s_chapter_request_lists_page_text_with_the_document_s_naming_defect():
    # the request said [speech] and "speech lines" and sent the meeting's naming rule for a PDF
    transcript, _ = pdf_source_service.build_page_transcript(_build_pdf(), is_read_as_pages=False)
    entries = [{"id": f"S{number:04d}", "kind": "speech", "text": segment["text"]}
               for number, segment in enumerate(transcript["segments"])]
    paths = fx.make_paths()
    frames_number = output_contract.PHASE_NUMBERS[output_contract.FRAMES_PREPARING_PHASE]
    phase_file_service.write_phase_file(paths, frames_number, {"medium": output_contract.PDF_TEXT_ONLY_MEDIUM, "items": []})
    section = {"heading": "History", "topicId": "T01.1", "entries": entries}
    inputs = {"sections": [section], "chapter": 1, "chapterCount": 1, "chapterTitle": "History",
              "documentTitle": "Gateway report", "isPdf": phase_file_service.is_pdf_run(paths)}
    text = document_messages.build_chapter_text(SimpleNamespace(inputs=inputs, findings=[]))
    harness.check("the report's thanks reaches the request", "Jonas Acme" in text, text[:800])
    harness.check("each paragraph is listed as page text, none as speech",
                  "[page text]" in text and "[speech]" not in text and "speech lines" not in text, text[:800])
    harness.check("the document's naming rule is sent, not the meeting's",
                  document_messages.PDF_NAMING_RULE in text and "speaks in the meeting" not in text)
