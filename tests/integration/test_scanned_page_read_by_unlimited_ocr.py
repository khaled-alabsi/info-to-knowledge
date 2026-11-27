"""A PDF's scanned page read by Unlimited-OCR-2 through phase 1's own path: a page of invented
words is drawn in text, rendered to a picture, spoiled with grain as a bad scan is, and written as a PDF holding only
that picture. Tesseract, inside pymupdf4llm, lost every word of such a page on a noisy test set; read by
the real tool and model, the page's words come back. Skipped where the tool or its model is not installed."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy
import pymupdf

from support import harness

from services import pdf_source_service
from services import unlimited_ocr_reader_service

LINES = ["Gateway onboarding guide",
         "Every partner gets its own tenant and its own retry budget.",
         "A refund above two thousand euros is answered later.",
         "The gateway runs in Frankfurt and Dublin."]
_GRAIN = 28                                       # grey levels of noise, the grain that spoils
_SEED = 7


def _build_grainy_scan() -> Path:
    """A one-page PDF holding only a picture of `LINES`, grainy, with no text layer."""

    drawn = pymupdf.open()
    page = drawn.new_page()
    for index, line in enumerate(LINES):
        page.insert_text((72, 100 + index * 30), line, fontsize=13)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    pixels = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(pixmap.height, pixmap.width, 3)
    noisy = numpy.clip(pixels + numpy.random.default_rng(_SEED).normal(0, _GRAIN, pixels.shape), 0, 255)
    scan_pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap.width, pixmap.height, noisy.astype(numpy.uint8).tobytes(), False)
    scan = pymupdf.open()
    scan_page = scan.new_page(width=page.rect.width, height=page.rect.height)
    scan_page.insert_image(scan_page.rect, pixmap=scan_pixmap)
    path = Path(tempfile.mkdtemp()) / "grainy-guide.pdf"
    scan.save(path)

    return path


def test_a_grainy_scan_s_words_come_back_through_phase_1():
    if not unlimited_ocr_reader_service.is_installed():
        print("Unlimited-OCR-2 is not installed (tools/unlimited-ocr/setup.sh --download): skipped")
        return
    transcript, _removed = pdf_source_service.build_page_transcript(
        _build_grainy_scan(), is_read_as_pages=False,
        ocr_engine=pdf_source_service.UNLIMITED_OCR_ENGINE)
    text = " ".join(" ".join(segment["text"].split()) for segment in transcript["segments"])
    missing = [line for line in LINES if line not in text]
    harness.check("the page was read by the model", transcript.get("ocrPages") == [1], str(transcript.get("ocrPages")))
    harness.check("every line of the scan is in the transcript", not missing, f"missing {missing}: {text[:400]}")
