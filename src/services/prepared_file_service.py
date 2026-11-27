"""`03_prepared/` in the run folder: phase 3's prepared notes of an audio recording, one markdown file per call, written
by the program from the model's answer and read back by the validator, the next steps and the merge.

A step over the whole speech writes `03_prepared/<step id>.md`; a step that goes through the items of an earlier step
writes `03_prepared/<step id>/<NN>.md`, NN the item's number in that step's file, from 01.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from models import output_contract
from models.preprocessing_step_spec import PreprocessingStepSpec
from models.run_paths import RunPaths
from services import preprocessing_step_service
from utils import prepared_markdown_utils

__all__ = ["CALL_SLUG_PREFIX", "build_prepared_dir", "build_prepared_path", "build_call_slug", "write_prepared",
           "read_prepared", "list_prepared_items", "list_step_paths", "ENTRY_KIND"]

# The kind of a prepared note among phase 4's entries
ENTRY_KIND = "prepared"

CALL_SLUG_PREFIX = output_contract.PREPARED_CALL_SLUG_PREFIX

_LOG = logging.getLogger(__name__)


def build_prepared_dir(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.PREPARED_DIR


def build_prepared_path(paths: RunPaths,
                        step_id: str,
                        item_number: int | None) -> Path:
    folder = build_prepared_dir(paths)

    return folder / f"{step_id}.md" if item_number is None else folder / step_id / f"{item_number:02d}.md"


def build_call_slug(step_id: str,
                    item_number: int | None) -> str:
    """The slug of one call: its call log, its attempts in the run's record and its line on the console."""

    return f"{CALL_SLUG_PREFIX}-{step_id}" if item_number is None else f"{CALL_SLUG_PREFIX}-{step_id}-{item_number:02d}"


def write_prepared(paths: RunPaths,
                   step_id: str,
                   step_title: str,
                   item_number: int | None,
                   parsed: dict[str, Any]) -> Path:
    """The answer written as the file's own markdown; the file's path."""

    path = build_prepared_path(paths, step_id, item_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prepared_markdown_utils.render_prepared(step_title, parsed), encoding="utf-8")

    return path


def read_prepared(paths: RunPaths,
                  step_id: str,
                  item_number: int | None) -> dict[str, Any] | None:
    """`{"items"}` of one call's file; None when it is not there or cannot be read."""

    path = build_prepared_path(paths, step_id, item_number)
    if not path.is_file():
        return None
    parsed, _why = prepared_markdown_utils.parse_prepared(path.read_text(encoding="utf-8"))

    return parsed


def list_step_paths(paths: RunPaths,
                    step_id: str) -> list[Path]:
    """The numbered files a step going through items wrote, in the order of their numbers; a file whose name is no
    number is no item's."""

    folder = build_prepared_dir(paths) / step_id

    return sorted((path for path in folder.glob("*.md") if path.stem.isdigit()), key=lambda path: int(path.stem))


def _read_step_file(paths: RunPaths,
                    step_id: str,
                    item_number: int | None) -> dict[str, Any]:
    """One call's prepared file read back; one that is missing or cannot be read holds no item, and says so when it is
    there: its items would be lost from phase 4 with no trace."""

    parsed = read_prepared(paths, step_id, item_number)
    path = build_prepared_path(paths, step_id, item_number)
    if parsed is None and path.is_file():
        _LOG.warning("%s cannot be read back, its items are left out", path.name)

    return parsed or {"items": []}


def _list_step_items(paths: RunPaths,
                     step: PreprocessingStepSpec) -> list[tuple[int, dict[str, Any]]]:
    """`(item number, item)` of every item one step wrote, in order: a step over the whole speech numbers its items in
    its one file, a step going through items takes each file's number."""

    if step.detailed_step_id is None:
        parsed = _read_step_file(paths, step.step_id, None)

        return list(enumerate(parsed["items"], start=1))
    listed = []
    for path in list_step_paths(paths, step.step_id):
        parsed = _read_step_file(paths, step.step_id, int(path.stem))
        # A detail file holds exactly one item: the first is taken, any more is ignored
        listed += [(int(path.stem), item) for item in parsed["items"][:1]]

    return listed


def _build_item_entries(step_number: int,
                        step: PreprocessingStepSpec,
                        item_number: int,
                        item: dict[str, Any],
                        paragraphs: list[dict[str, Any]],
                        line_starts: dict[str, float]) -> list[dict[str, Any]]:
    """One item's sections as entries `P<step>.<item>.<section>`, a section that says nothing or cites no paragraph left
    out."""

    entries = []
    for section_number, section in enumerate(item["sections"], start=1):
        cited, _unknown = prepared_markdown_utils.resolve_cited_paragraphs(section["cited"], paragraphs)
        if prepared_markdown_utils.is_not_said(section["text"]) or not cited:
            continue
        starts = [line_starts[paragraph_id] for paragraph_id in cited if paragraph_id in line_starts]
        entries.append({"id": f"P{step_number}.{item_number}.{section_number}", "kind": ENTRY_KIND,
                        "start": min(starts or [0.0]), "end": max(starts or [0.0]),
                        "text": f"{section['name']}: {section['text']}", "paragraphs": cited,
                        "item": item["title"], "step": step.title})

    return entries


def list_prepared_items(paths: RunPaths,
                        steps: tuple[PreprocessingStepSpec, ...],
                        paragraphs: list[dict[str, Any]],
                        line_starts: dict[str, float]) -> list[dict[str, Any]]:
    """Every item phase 4 writes from, `{"step", "title", "start", "entries"}`, in the order the recording first
    takes it: the items of each step no later step goes through, each section one entry `P<step>.<item>.<section>`
    (numbered by position, a section that says nothing or cites no paragraph left out) holding its text and the
    paragraphs it cites."""

    detailed = preprocessing_step_service.list_detailed_step_ids(steps)
    items = []
    for step_number, step in enumerate(steps, start=1):
        if step.step_id in detailed:
            continue
        for item_number, item in _list_step_items(paths, step):
            entries = _build_item_entries(step_number, step, item_number, item, paragraphs, line_starts)
            if entries:
                # An item's sections stay together, at the time the item starts, so a chapter never splits them
                start = min(entry["start"] for entry in entries)
                items.append({"step": step.title, "title": item["title"], "start": start,
                              "entries": [{**entry, "start": start} for entry in entries]})

    return sorted(items, key=lambda item: item["start"])
