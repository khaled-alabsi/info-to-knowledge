"""Publishing: the program, with no model, turns phase 4's document into the folder a reader opens (R13).

Phase 4's chapters have already placed their own images, so nothing is chosen here. The program adopts those images,
writes `<meeting name>/<meeting name>.md` with `resources/` and
`provenance.json`, and judges the folder from disk.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from models import document_heading_spec
from models import output_contract
from models.attempt_request import AttemptRequest
from models.publication_result import PublicationResult
from models.run_context import RunContext
from services import document_file_service
from services import document_folder_service
from services import illustration_action_service
from services import integration_service
from services import phase_file_service
from services import validation_service
from utils import document_block_utils
from utils import json_store_utils

PUBLISH_KIND = "publish"
# where a PDF's figures placed unread are written as the PNG files the published folder copies
_PLACED_FIGURES_DIR_NAME = "placed-figures"
_PLACED_CUT_PREFIX = "placed-"                 # the cut id of a figure placed unread: `placed-frame-012`
# the headings of the sections around the chapters, in every language a document is written in: no figure is placed there
_OUTSIDE_CHAPTER_HEADINGS = frozenset(
    heading for spec in document_heading_spec.BY_LANGUAGE.values()
    for heading in (spec.executive_summary, spec.agenda, spec.contents, *spec.list_closing()))


def _read_paragraph_number(entry_id: str) -> int | None:
    """`S0415` as 415; None for an entry of another kind."""

    return int(entry_id[1:]) if entry_id.startswith("S") and entry_id[1:].isdigit() else None


def _find_placed_figure_place(blocks: list[dict],
                              place_before: str) -> tuple[int, dict]:
    """Where a figure placed unread goes, and the chapter paragraph it cites: before the first that cites the paragraph
    `place_before` names or a later one; right after the last chapter paragraph citing entries when none does, or when
    the figure stood after the last paragraph."""

    wanted = _read_paragraph_number(place_before)
    place, cited_block = len(blocks), {}
    for index, block in enumerate(blocks):
        is_chapter_paragraph = (block["kind"] == "paragraph" and block.get("entryIds")
                                and block.get("section") and block["section"] not in _OUTSIDE_CHAPTER_HEADINGS)
        if not is_chapter_paragraph:
            continue
        numbers = [_read_paragraph_number(entry_id) for entry_id in block["entryIds"]]
        cited = [number for number in numbers if number is not None]
        if wanted is not None and any(number >= wanted for number in cited):
            return index, block
        place, cited_block = index + 1, block

    return place, cited_block


def _add_placed_figures(output_root: Path,
                        blocks: list[dict],
                        placed_figures: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """`blocks` with each figure placed unread as an image block where it stood, citing the
    entries of the paragraph it stands before (or after, at the end), and the cuts its files are copied from."""

    placed_dir = output_root / output_contract.WRITING_DIR_NAME / _PLACED_FIGURES_DIR_NAME
    placed_dir.mkdir(parents=True, exist_ok=True)
    cuts: dict[str, dict] = {}
    for figure in placed_figures:
        # 1. its file as a PNG, since the published folder names every figure `figure-NN.png`
        cut_id = f"{_PLACED_CUT_PREFIX}{figure['slug']}"
        png = placed_dir / f"{figure['slug']}.png"
        Image.open(output_root / figure["path"]).save(png)
        cuts[cut_id] = {"file": str(png.relative_to(output_root)), "frame": figure["slug"], "lookRect": None,
                        "recordingRect": figure.get("box"), "warnings": [], "marks": [], "masks": [],
                        "isFromRecording": True}

        # 2. the image block, before the paragraph that followed it in the text
        index, neighbour = _find_placed_figure_place(blocks=blocks, place_before=str(figure.get("placeBefore") or ""))
        # after the figures already placed there, so two figures past the last paragraph keep their order
        while index < len(blocks) and str(blocks[index].get("cut") or "").startswith(_PLACED_CUT_PREFIX):
            index += 1
        caption = str(figure.get("caption") or "").strip() or f"Figure on page {figure['page']}"
        image = {"id": f"P{len(cuts):03d}", "kind": "image", "cut": cut_id, "frame": figure["slug"], "caption": caption,
                 "alt": caption, "entryIds": list(neighbour.get("entryIds") or []), "description": "",
                 "section": neighbour.get("section", ""), "sectionId": neighbour.get("sectionId", ""), "text": ""}
        blocks = [*blocks[:index], image, *blocks[index:]]

    return blocks, cuts


def _build_entry_index(document_provenance: dict) -> dict[str, list[str]]:
    """A paragraph's text, its spaces collapsed, -> the entries it rests on, from phase 4's provenance."""

    index = {}
    for part in [*document_provenance.get("paragraphs", []), *document_provenance.get("summary", [])]:
        index[" ".join(str(part.get("text", "")).split())] = [entry.get("id") for entry in part.get("entries", [])]

    return index


def publish_document(ctx: RunContext) -> PublicationResult:
    """Raises `MissingPhaseInput`, by name, when phase 4's document or its provenance is not on disk."""

    # STEP 1: phase 4's document and provenance, the frames and the merged entries
    provenance_path = ctx.paths.output_root / output_contract.DRAFT_PROVENANCE_FILE
    for required in (ctx.paths.summary, provenance_path):
        phase_file_service.require_phase_output(required, output_contract.PHASE_NUMBERS["finalize"])
    markdown = ctx.paths.summary.read_text(encoding="utf-8")
    document_provenance = json_store_utils.read_json(provenance_path)
    frames_file = phase_file_service.read_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"])
    integration = integration_service.build_integration(ctx.paths)
    frames = {item["slug"]: item for item in frames_file.get("items", [])}
    entries = {entry["id"]: entry for entry in integration.get("entries", [])}

    # STEP 2: the store the folder is written from: the document's blocks, its placed figures adopted, and the cuts
    written = document_file_service.read_image_store(ctx.paths)
    blocks, adopted = document_block_utils.convert_placed_images(
        document_block_utils.split_into_blocks(markdown, _build_entry_index(document_provenance)), written.get("cuts") or {})
    # a PDF's figures placed unread, each where it stood in the text
    blocks, placed_cuts = _add_placed_figures(output_root=ctx.paths.output_root,
                                              blocks=blocks,
                                              placed_figures=frames_file.get(output_contract.PLACED_FIGURES_KEY) or [])
    placed_frames = {figure["slug"]: figure for figure in frames_file.get(output_contract.PLACED_FIGURES_KEY) or []}
    frames.update(placed_frames)
    store = illustration_action_service.build_empty_store(blocks)
    store.update({"looks": written.get("looks") or {}, "cuts": {**(written.get("cuts") or {}), **placed_cuts},
                  "nextCut": int(written.get("nextCut") or 1), "adopted": adopted})

    # STEP 3: the folder written, then judged
    document = document_folder_service.write_document_folder(
        ctx.paths, store, document_provenance, entries, {slug: item["timestamp"] for slug, item in frames.items()})
    request = AttemptRequest(job_kind=PUBLISH_KIND, attempt=1, repository=ctx.repository, module=ctx.module, paths=ctx.paths,
                             ignore=ctx.ignore, inputs={"knownEntryIds": sorted(entries)})
    report = validation_service.validate_or_report_crash(ctx, request)

    return PublicationResult(document=document, image_count=sum(1 for block in store["blocks"] if block["kind"] == "image"),
                             is_ok=report.ok,
                             # The detail with the code: told only a check's name, a failed publication had to be
                             # reproduced by hand to learn which figure failed
                             report="" if report.ok else f"{report.render_summary()} ({report.render_hint()})")
