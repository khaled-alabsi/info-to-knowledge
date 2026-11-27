"""Phase 4 of a PDF's markdown conversion, which calls no model: the document is the PDF's own text, as phase 2 wrote
it into `02_speech.md`, with every figure phase 1 cut placed where it stood, written and published by the program
alone. No merge, no plan, no conversation, no check of a model's text, since no model wrote any."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from models import output_contract
from models.pipeline_errors import MissingPhaseInput
from models.run_paths import RunPaths
from services import document_folder_service
from services import phase_file_service
from services import speech_file_service
from utils import document_text_utils
from utils import json_store_utils


def _read_line_number(line_id: str) -> int | None:
    """`S0415` as 415; None for no id, which places a figure after the last paragraph."""

    return int(line_id[1:]) if line_id.startswith("S") and line_id[1:].isdigit() else None


def _find_paragraph_index(paragraphs: list[dict[str, Any]],
                          place_before: str) -> int:
    """The index of the paragraph a figure goes before: the first whose lines reach the line `place_before` names;
    past the last paragraph when it names none or a line after them all."""

    wanted = _read_line_number(place_before)
    if wanted is None:
        return len(paragraphs)
    for index, paragraph in enumerate(paragraphs):
        last = _read_line_number(str(paragraph.get("last") or paragraph.get("first") or ""))
        if last is not None and last >= wanted:
            return index

    return len(paragraphs)


def _describe_figure(figure: dict[str, Any],
                     number: int) -> tuple[str, str]:
    """A figure's alt text and its caption line: the PDF's caption, else its page."""

    caption = " ".join(str(figure.get("caption") or "").split())
    if caption:
        return caption, f"*Figure {number}: {caption}*"

    return f"Figure on page {figure.get('page', '?')}", f"*Figure {number} (page {figure.get('page', '?')})*"


def render_converted_document(paragraphs: list[dict[str, Any]],
                              figures: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """The document's markdown, each paragraph's text as it stands and each figure before the paragraph that followed
    it in the PDF, numbered in the order they stand; and each figure with its number, file name and caption."""

    before: dict[int, list[dict[str, Any]]] = {}
    for figure in figures:
        before.setdefault(_find_paragraph_index(paragraphs, str(figure.get("placeBefore") or "")), []).append(figure)
    blocks: list[str] = []
    placed: list[dict[str, Any]] = []
    for index in range(len(paragraphs) + 1):
        for figure in before.get(index, []):
            number = len(placed) + 1
            name = document_folder_service.build_figure_file_name(number)
            caption, caption_line = _describe_figure(figure, number)
            blocks.append(f"![{caption}]({output_contract.RESOURCES_DIR_NAME}/{name})\n\n{caption_line}")
            placed.append({"number": number, "file": name, "caption": caption, "frame": figure.get("slug", ""),
                           "path": figure.get("path", ""), "page": figure.get("page")})
        if index < len(paragraphs) and paragraphs[index]["text"].strip():
            blocks.append(paragraphs[index]["text"].strip())

    return "\n\n".join(blocks) + "\n", placed


def publish_conversion(paths: RunPaths) -> Path:
    """`04_document.md`, its provenance and the published folder with `resources/`, from `02_speech.md` and the figures
    `02_frames.json` places; the published document's path. Raises `MissingPhaseInput` when phase 2's paragraphs are
    not on disk."""

    # STEP 1: phase 2's paragraphs and placed figures
    speech = speech_file_service.read_speech(paths)
    if speech is None:
        raise MissingPhaseInput(f"{output_contract.SPEECH_FILE} is missing or unreadable; phase 2 writes it")
    frames_file = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])
    figures = list(frames_file.get(output_contract.PLACED_FIGURES_KEY) or [])

    # STEP 2: the document, every web address's secret hidden, and what each paragraph rests on
    markdown, placed = render_converted_document(speech["paragraphs"], figures)
    text = document_text_utils.hide_secret_query_values(markdown)
    paths.summary.write_text(text, encoding="utf-8")
    facts = [{"text": paragraph["text"], "entries": [{"id": paragraph["first"]}, {"id": paragraph["last"]}]}
             for paragraph in speech["paragraphs"]]
    json_store_utils.write_json(paths.output_root / output_contract.DRAFT_PROVENANCE_FILE, {"paragraphs": facts})

    # STEP 3: the published folder, its figures as PNG files under the names the document links
    document = document_folder_service.build_document_path(paths)
    resources = document.parent / output_contract.RESOURCES_DIR_NAME
    resources.mkdir(parents=True, exist_ok=True)
    for figure in placed:
        Image.open(paths.output_root / figure["path"]).save(resources / figure["file"])
    document.write_text(document_folder_service.render_published_front_matter(paths) + text, encoding="utf-8")
    json_store_utils.write_json(document.parent / output_contract.DOCUMENT_PROVENANCE_FILE, {
        "schemaVersion": 1, "document": document.name, "leftOut": [], "removedUnreferenced": [], "facts": facts,
        "images": [{"file": f"{output_contract.RESOURCES_DIR_NAME}/{figure['file']}", "frame": figure["frame"],
                    "page": figure["page"], "caption": figure["caption"]} for figure in placed]})

    return document
