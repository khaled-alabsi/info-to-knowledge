"""The files of a takeaway run's phase 4: the call's takeaway with its ids, the
document a reader opens without them, and the published folder with what each line rests on. Pure file work.

- `04_writing/takeaway.md`: the call's takeaway as the program checked it, every line ending with its transcript ids.
- `04_document.md` and `04_document.provenance.json`: the document and its lines' sources, as phase 4 always writes.
- `<name>/<name>.md` and `<name>/provenance.json`: the published folder, named as every published folder is
  (`document_folder_service.build_document_dir`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models import output_contract
from models.pipeline_errors import MissingPhaseInput
from models.run_paths import RunPaths
from services import document_folder_service
from utils import document_text_utils
from utils import json_store_utils
from utils import takeaway_markdown_utils

__all__ = ["build_takeaway_path", "write_takeaway", "publish_takeaway"]

TAKEAWAY_FILE = "takeaway.md"


def build_takeaway_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.WRITING_DIR_NAME / TAKEAWAY_FILE


def write_takeaway(paths: RunPaths,
                   parsed: dict[str, Any]) -> Path:
    """The call's takeaway written with its ids, as the program's copy the check reads; the file's path."""

    path = build_takeaway_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(takeaway_markdown_utils.render_takeaway(parsed, is_cited=True), encoding="utf-8")

    return path


def _describe_entry(line: dict[str, Any]) -> dict[str, Any]:
    """A transcript line as a provenance entry: its id, time, speaker and words, a web address's secret hidden."""

    return {"id": line["id"], "start": line["start"], "end": line["end"], "speaker": line.get("speaker"),
            "text": document_text_utils.hide_secret_query_values(line["text"])}


def _list_facts(parsed: dict[str, Any],
                lines_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Each line of the takeaway with its section and the transcript lines it rests on, their time, speaker and words."""

    facts = []
    for heading, key in takeaway_markdown_utils.SECTION_KEYS.items():
        for item in parsed[key]:
            facts.append({"section": heading, "text": document_text_utils.hide_secret_query_values(item["text"]),
                          "entries": [_describe_entry(lines_by_id[line_id])
                                      for line_id in item["ids"] if line_id in lines_by_id]})

    return facts


def publish_takeaway(paths: RunPaths,
                     lines: list[dict[str, Any]]) -> Path:
    """`04_document.md`, its provenance and the published folder, from the checked `04_writing/takeaway.md` and the
    transcript `lines` its ids name; the published document's path."""

    # STEP 1: the reader's document, the ids taken out and every web address's secret hidden
    parsed, why = takeaway_markdown_utils.parse_takeaway(build_takeaway_path(paths).read_text(encoding="utf-8"))
    if parsed is None:
        raise MissingPhaseInput(f"{TAKEAWAY_FILE} cannot be read: {why}")
    parsed["title"] = parsed["title"] or Path(paths.module).stem
    text = document_text_utils.hide_secret_query_values(takeaway_markdown_utils.render_takeaway(parsed, is_cited=False))
    paths.summary.write_text(text, encoding="utf-8")
    facts = _list_facts(parsed, {line["id"]: line for line in lines})
    json_store_utils.write_json(paths.output_root / output_contract.DRAFT_PROVENANCE_FILE, {"facts": facts})

    # STEP 2: the published folder, under the name every published folder takes, opened by its front matter
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(document_folder_service.render_published_front_matter(paths) + text,
                        encoding="utf-8")
    json_store_utils.write_json(document.parent / output_contract.DOCUMENT_PROVENANCE_FILE, {
        "schemaVersion": 1, "document": document.name, "images": [], "leftOut": [], "removedUnreferenced": [],
        "facts": facts})

    return document
