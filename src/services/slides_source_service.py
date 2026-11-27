"""Phase 1 of the PPT workflow: a PowerPoint (`.pptx`) or OpenDocument presentation (`.odp`)
deck turned into `01_slides.pdf` by LibreOffice, whose pages a PDF's "pages as images" path then reads, each slide a
picture the vision model reads.

What is said over a slide is its speaker notes, never the slide's own text (the owner: "only image to text, not other
way"): a slide's words handed over as speech would let the model read the slide without looking at it. The notes are
kept, since they hold what the presenter meant to say and no picture shows. A hidden slide is left out, as LibreOffice
leaves it out of the PDF and the audience never saw it. The PDF must hold one page per shown slide, or every later step
would give a slide another slide's notes.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pymupdf

from models import output_contract
from models.pipeline_errors import MissingPhaseInput
from services import office_source_service

__all__ = ["SLIDES_SUFFIXES", "SLIDES_PDF_NAME", "is_slides", "find_slides_pdf", "read_slide_notes",
           "build_slide_transcript"]

SLIDES_SUFFIXES = (".pptx", ".odp")
# The deck as LibreOffice drew it, in the run folder: the PDF every later step reads its pages from
SLIDES_PDF_NAME = "01_slides.pdf"

_PPTX_NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
_ODP_NS = {"draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
           "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
           "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}

_LOG = logging.getLogger(__name__)


def is_slides(path: Path) -> bool:
    return path.suffix.lower() in SLIDES_SUFFIXES


def find_slides_pdf(output_root: Path) -> Path:
    """The deck's PDF in the run folder, which phase 1 writes and later phases read pages from."""

    return output_root / SLIDES_PDF_NAME


def _read_xml(archive: zipfile.ZipFile,
              name: str) -> ElementTree.Element:
    return ElementTree.fromstring(archive.read(name))


def _read_pptx_notes(deck: Path) -> list[dict]:
    """Every slide of a `.pptx` in show order: `{"number", "hidden", "notes"}`, the notes the text of the notes slide's
    body, its slide number left out."""

    with zipfile.ZipFile(deck) as archive:
        presentation = _read_xml(archive, "ppt/presentation.xml")
        relations = {relation.get("Id"): relation.get("Target")
                     for relation in _read_xml(archive, "ppt/_rels/presentation.xml.rels")}
        slides: list[dict] = []
        for number, slide_id in enumerate(presentation.iterfind("p:sldIdLst/p:sldId", _PPTX_NS), start=1):
            target = relations[slide_id.get(f"{{{_PPTX_NS['r']}}}id")]
            slide_path = f"ppt/{target}" if not target.startswith("/") else target.lstrip("/")
            slide = _read_xml(archive, slide_path)
            notes = ""
            slide_rels = f"{Path(slide_path).parent}/_rels/{Path(slide_path).name}.rels"
            if slide_rels in archive.namelist():
                for relation in _read_xml(archive, slide_rels):
                    if relation.get("Type", "").endswith("/notesSlide"):
                        notes = _read_pptx_notes_body(archive, Path(slide_path).parent, relation.get("Target"))
            slides.append({"number": number, "hidden": slide.get("show") == "0", "notes": notes})

    return slides


def _read_pptx_notes_body(archive: zipfile.ZipFile,
                          slide_dir: Path,
                          target: str) -> str:
    """The notes slide's body text: its placeholder of type "body", never the slide number or the slide's picture."""

    parts = [part for part in (slide_dir / target).as_posix().split("/") if part]
    resolved: list[str] = []
    for part in parts:
        if part == "..":
            resolved.pop()
        elif part != ".":
            resolved.append(part)
    notes_slide = _read_xml(archive, "/".join(resolved))
    bodies = []
    for shape in notes_slide.iter(f"{{{_PPTX_NS['p']}}}sp"):
        placeholder = shape.find(".//p:nvPr/p:ph", _PPTX_NS)
        if placeholder is not None and placeholder.get("type") == "body":
            text_body = shape.find("p:txBody", _PPTX_NS)
            if text_body is not None:
                bodies.append(_join_paragraphs(text_body, f"{{{_PPTX_NS['a']}}}p", f"{{{_PPTX_NS['a']}}}t"))

    return "\n".join(body for body in bodies if body).strip()


def _join_paragraphs(element: ElementTree.Element,
                     paragraph_tag: str,
                     text_tag: str) -> str:
    """The words of `element`, one line per paragraph, each paragraph's runs joined."""

    lines = ["".join(run.text or "" for run in paragraph.iter(text_tag)).strip()
             for paragraph in element.iter(paragraph_tag)]

    return "\n".join(line for line in lines if line)


def _read_odp_notes(deck: Path) -> list[dict]:
    """Every slide of an `.odp` in show order, with its notes; a hidden slide is not told apart here, since an `.odp`
    hides it through a style LibreOffice reads, and the page count check names a mismatch."""

    with zipfile.ZipFile(deck) as archive:
        content = _read_xml(archive, "content.xml")
    slides = []
    for number, page in enumerate(content.iter(f"{{{_ODP_NS['draw']}}}page"), start=1):
        notes = page.find("presentation:notes", _ODP_NS)
        lines = [] if notes is None else ["".join(paragraph.itertext()).strip()
                                          for paragraph in notes.iter(f"{{{_ODP_NS['text']}}}p")]
        text = "\n".join(line for line in lines if line)
        slides.append({"number": number, "hidden": False, "notes": text.strip()})

    return slides


def read_slide_notes(deck: Path) -> list[dict]:
    """Every slide of the deck in show order: `{"number", "hidden", "notes"}`."""

    return _read_odp_notes(deck) if deck.suffix.lower() == ".odp" else _read_pptx_notes(deck)


def build_slide_transcript(deck: Path,
                           output_root: Path,
                           log: logging.Logger = _LOG) -> tuple[dict, list[dict]]:
    """The deck as phase 1's transcript: `01_slides.pdf` written, one segment per shown slide with notes, at its page's
    minute, the notes as its text, `slides` listing every slide with its page in the PDF (0 for a hidden one); and the
    hidden slides as removals `{"page", "rule", "text"}`."""

    # STEP 1: the PDF, by LibreOffice; without it a deck cannot be read at all
    if office_source_service.find_libreoffice() is None:
        raise MissingPhaseInput("LibreOffice is needed to read a PowerPoint deck: install it with "
                                "`brew install --cask --appdir=~/Applications libreoffice`")
    pdf = find_slides_pdf(output_root)
    work_dir = output_root / f"{SLIDES_PDF_NAME}-work"
    converted = office_source_service.convert_with_libreoffice([deck], "pdf", work_dir)
    if deck not in converted:
        raise RuntimeError(f"LibreOffice wrote no PDF for {deck.name}")
    converted[deck].replace(pdf)
    work_dir.rmdir()

    # STEP 2: the slides and their notes, the hidden ones left out, one page each
    slides = read_slide_notes(deck)
    shown = [slide for slide in slides if not slide["hidden"]]
    with pymupdf.open(pdf) as document:
        page_count = document.page_count
    if page_count != len(shown):
        raise RuntimeError(f"{deck.name} has {len(shown)} shown slide(s) and LibreOffice drew {page_count} page(s): a "
                           f"slide would get another slide's notes")
    removed = [{"page": 0, "rule": "hidden slide", "text": f"slide {slide['number']}"} for slide in slides if slide["hidden"]]
    if removed:
        log.info(f"  {len(removed)} hidden slide(s) left out: " + ", ".join(entry["text"] for entry in removed))

    # STEP 3: the notes as what is said over each slide, at its page's minute
    segments = []
    pages = []
    for page_number, slide in enumerate(shown, start=1):
        pages.append({"number": slide["number"], "page": page_number})
        if slide["notes"]:
            start = (page_number - 1) * output_contract.PDF_PAGE_SECONDS
            segments.append({"text": slide["notes"], "start": start, "end": start + output_contract.PDF_PAGE_SECONDS,
                             "speaker": None, "words": [], "page": page_number})
    log.info(f"  {len(shown)} slide(s) drawn into {SLIDES_PDF_NAME}, {len(segments)} with speaker notes")
    transcript = {"language": None, "languageProbability": None, "engine": "libreoffice", "model": "",
                  "text": "\n\n".join(segment["text"] for segment in segments), "segments": segments, "slides": pages}

    return transcript, removed
