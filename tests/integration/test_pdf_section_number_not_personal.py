"""A PDF's section number written after "customer number" is no person's record number (worker report on a wiki
export's chapter 1): the made wiki export (tests/resources/pdf/out/01-confluence-export.pdf, every word invented) is read
by phase 1, a chapter sentence naming one of its numbered sections beside the label goes through phase 4's text check,
and only a real customer number is refused."""

import re
from pathlib import Path

from support import harness

from models.validation_report import ValidationReport
from services import pdf_source_service
from validators import document_validator

PDF = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "out" / "01-confluence-export.pdf"


def test_a_pdf_s_section_number_beside_customer_number_passes_the_text_check():
    transcript, _removed = pdf_source_service.build_page_transcript(PDF, is_read_as_pages=False)
    text = "\n".join(str(segment.get("text", "")) for segment in transcript["segments"])
    numbers = re.findall(r"\b\d+\.\d+(?:\.\d+)+\b", text) or ["2.6.22"]
    section = ValidationReport(subject="chapter-01")
    document_validator.check_text(section, "chapter-01",
                                  f"The steps go on with customer number selection ({numbers[0]}) [S0001].")
    harness.check("a section number passes", "DOCUMENT_PERSONAL_IN_TEXT" not in harness.codes(section),
                  str(section.findings))
    record = ValidationReport(subject="chapter-01")
    document_validator.check_text(record, "chapter-01", "The customer number 4415 5027 is shown [S0001].")
    harness.check("a customer number is refused", "DOCUMENT_PERSONAL_IN_TEXT" in harness.codes(record),
                  str(record.findings))
