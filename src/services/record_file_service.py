"""Phase 3's records: where they live, and the only code that writes or reads them.

The PROGRAM writes a record, from the answer it read out of a model's reply. No model is ever handed a path or a
write tool in a chain pipeline, so the layout is stated here once and the writer, the validator and the node all
reach it through this module.

A record is markdown, in the shape the model answers in (`utils/frame_markdown_utils.py`), so the file a person opens
under `03_processed-frames/` is the notes as the checks judged them, not a JSON file. Four kinds live there, each with
its own writer and reader: a frame's notes (`frame-005.md`), a batch's account (`frames-000-003-batch-summary.md`), a batch's critique (`frames-000-003-critique.md`) and its second
look (`frames-000-003-second-pass.md`). A file the program wrote and cannot read back raises, a playbook bug the
validator reports as one; a critique that does not read is the one exception: it is counted as no rules, with a
warning naming why, so phase 6 reads the critiques that do.
"""

from __future__ import annotations

import logging
import re

from pathlib import Path
from typing import Any

from agent_client import path_sandbox
from models import output_contract
from models.run_paths import RunPaths
from schemas import frame_record
from services import frame_records_service
from utils import critique_markdown_utils
from utils import frame_markdown_utils
from utils import frame_slug_utils

_LOG = logging.getLogger(__name__)

__all__ = ["fill_screen_pointer", "build_record_path", "remove_record", "write_frame_record", "read_frame_record", "write_batch_record",
           "read_batch_record", "write_stored_frame_record", "write_second_pass_record", "read_second_pass_record",
           "write_critique_record", "read_critique_record"]


def build_record_path(paths: RunPaths,
                      slug: str) -> Path:
    """Where one record lives. Refuses a slug that would land outside the output root."""

    # Resolved through the jail, never joined. A slug reaches here from the enumeration and from
    # `--item`, which an operator types: `--item ../../x` joined onto the output root is a write
    # outside it, and resolve-then-compare is the one check `../` does not defeat.
    return path_sandbox.resolve_within(paths.output_root, f"{output_contract.ITEMS_DIR_NAME}/{slug}.md")


def _write(paths: RunPaths,
           slug: str,
           text: str) -> Path:
    """Written whole and atomically: a run killed mid-write leaves the old file or the new one, never half of one."""

    record_path = build_record_path(paths, slug)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    staged = record_path.with_suffix(".md.tmp")
    staged.write_text(text, encoding="utf-8")
    staged.replace(record_path)

    return record_path


def _read(paths: RunPaths,
          slug: str,
          is_addition: bool) -> dict[str, Any]:
    text = build_record_path(paths, slug).read_text(encoding="utf-8")
    parsed = frame_markdown_utils.parse_answer(text, is_addition) if text.strip() else {
        "frames": [], "batch": None, "is_nothing_to_change": False, "errors": []}
    if parsed["errors"]:
        raise ValueError(f"{slug}.md cannot be read back: " + "; ".join(error["text"] for error in parsed["errors"]))

    return parsed


def remove_record(paths: RunPaths,
                  slug: str) -> None:
    """The record `slug` names gone from disk, when there is one."""

    build_record_path(paths, slug).unlink(missing_ok=True)


def _read_frame_screen(paths: RunPaths,
                       slug: str,
                       pointed: int | None) -> str:
    """The outline of frame `pointed`, which frame `slug`'s `## Screen` points to, by that frame's notes on disk; empty
    when it points to itself, to no frame, or to one whose notes cannot be read. "same as frame N" was refused four
    times in one run for frames phase 2 had not marked (a scroll, a twin record), each costing its batch a resume; the
    frame's own lines are then added to the outline as each record is written (add_unquoted_screen_lines)."""

    if pointed is None or frame_slug_utils.build_frame_slug(pointed) == slug:
        return ""
    # A frame later in the batch has no notes on disk yet: the pointer stays, and the outline check names it
    try:
        return str(read_frame_record(paths, frame_slug_utils.build_frame_slug(pointed)).get("screen") or "")
    except (OSError, ValueError) as error:
        _LOG.warning("%s points to frame %s, whose notes could not be read: %s", slug, pointed, error)
        return ""


def _fill_pointer_head(paths: RunPaths,
                       slug: str,
                       record: dict[str, Any]) -> dict[str, Any]:
    """An outline opening on "same as frame N, but <difference>" with frame N's outline in place of that line, and the
    difference after it as a part of its own holding the lines the answer wrote under it."""

    head = frame_markdown_utils.split_pointer_head(str(record.get("screen") or ""))
    if head is None:
        return record
    pointed, difference, rest = head
    original = _read_frame_screen(paths, slug, pointed)
    if not original:
        return record
    label = f"- {difference[:1].upper() + difference[1:]}:" if difference else "- What this frame shows besides it:"
    is_rest_nested = all(line.startswith("  ") for line in rest)

    return {**record, "screen": "\n".join([original.rstrip(), label, *(rest if is_rest_nested else ["  " + line for line in rest])])}


def _fill_pointer_parts(paths: RunPaths,
                        slug: str,
                        record: dict[str, Any]) -> dict[str, Any]:
    """The outline with each part described as another frame's filled from that frame's stored outline."""

    screen = frame_markdown_utils.drop_pointer_asides(str(record.get("screen") or ""))
    record = {**record, "screen": screen} if record.get("screen") else record
    pointed = {int(number) for number in re.findall(r"same\s+as\s+(?:in\s+|on\s+)?frame\s*#?\s*(\d+)", screen,
                                                  re.IGNORECASE)}
    outlines = {number: _read_frame_screen(paths, slug, number) for number in pointed}
    outlines = {number: outline for number, outline in outlines.items() if outline}
    if not outlines:
        return record

    return {**record, "screen": frame_markdown_utils.fill_pointer_parts(screen, outlines)}


def fill_screen_pointer(paths: RunPaths,
                        slug: str,
                        record: dict[str, Any]) -> dict[str, Any]:
    """A `## Screen` that points at another frame, with that frame's outline in its place: the frame it repeats (its
    build's outlines up to it), else the frame it names."""

    # The screens are the same by the capture rule that marked the frame a repeat, so the program knows the outline and
    # no call writes it twice; a later step still reads one frame's notes standing alone, which is what the rule asking
    # for the whole outline is for (a repeat was blocked for pointing at the frame it repeats).
    screen = str(record.get("screen") or "")
    if not frame_markdown_utils.is_screen_pointer(screen):
        return _fill_pointer_parts(paths, slug, _fill_pointer_head(paths, slug, record))
    original = frame_records_service.find_repeated_screen(paths, slug)
    if not original:
        original = _read_frame_screen(paths, slug, frame_markdown_utils.read_pointed_frame(screen))

    return {**record, "screen": original} if original else record


def write_frame_record(paths: RunPaths,
                       slug: str,
                       record: dict[str, Any]) -> Path:
    record = fill_screen_pointer(paths, slug, record)
    record = {**record, "take_aways": frame_markdown_utils.mark_unsaid_screen_quotes(
        list(record.get("take_aways") or []), frame_records_service.read_frame_speech(paths, slug), record.get("screen"))}

    title = frame_record.DOCUMENT_FRAME_TITLES.get(frame_records_service.read_frame_medium(paths, slug),
                                                   frame_record.FRAME_TITLE)

    return _write(paths, slug, frame_markdown_utils.render_frame_document(record, title))


def read_frame_record(paths: RunPaths,
                      slug: str) -> dict[str, Any]:
    """The one frame's notes the file holds. Raises when it holds none, or more than one."""

    frames = _read(paths, slug, is_addition=False)["frames"]
    if len(frames) != 1:
        raise ValueError(f"{slug}.md holds {len(frames)} frame document(s), not one")

    return frames[0]


def write_batch_record(paths: RunPaths,
                       slug: str,
                       batch: dict[str, Any],
                       batch_entry: dict[str, Any] | None = None) -> Path:
    """R15 and R18: a batch's account and how it felt, travelling together, because one batch has one mood. With `batch_entry`
    (its entry in `03_batch_summaries.json`) the file also holds the batch's name, frames, start, end and source."""

    text = frame_markdown_utils.render_batch_document(batch)

    return _write(paths, slug, text if batch_entry is None else text + "\n" + frame_markdown_utils.render_batch_data(batch_entry))


def read_batch_record(paths: RunPaths,
                      slug: str) -> dict[str, Any]:
    batch = _read(paths, slug, is_addition=False)["batch"]

    return dict(batch) if batch else {field: "" for field in frame_record.BATCH_SECTIONS}


def write_stored_frame_record(paths: RunPaths,
                              slug: str,
                              entry: dict[str, Any],
                              item: dict[str, Any] | None = None) -> Path:
    """A frame's file from its accepted notes as `02_frames.json` keeps them, so the file holds every addition the
    second pass made to them. With `item` (the frame's entry in that file) it also holds the frame's time, frame number,
    file, score, boxes, the frames whose notes it was shown and the words spoken (no missing data)."""

    text = frame_markdown_utils.render_stored_notes(entry)

    return _write(paths, slug, text if item is None else text + "\n" + frame_markdown_utils.render_frame_data(item))


def write_critique_record(paths: RunPaths,
                          slug: str,
                          answer: dict[str, Any]) -> Path:
    """A batch's critique, whole, as it was answered: the anti-pattern rules it reports. It is an ARTIFACT - no frame
    is changed by it - and nothing to say at all is an empty file."""

    is_empty = not answer.get("is_nothing_to_criticise") and not answer.get("rules")

    return _write(paths, slug, "" if is_empty else critique_markdown_utils.render_critique_document(answer))


def _build_no_critique() -> dict[str, Any]:
    """A critique with no rule (no file, or a file holding none), its own list each time."""

    return {"is_nothing_to_criticise": False, "rules": []}


def read_critique_record(paths: RunPaths,
                         slug: str) -> dict[str, Any]:
    """`{"is_nothing_to_criticise", "rules"}` as the critique answered them; no rule when the file is not there."""

    path = build_record_path(paths, slug)
    if not path.is_file():
        return _build_no_critique()
    text = path.read_text(encoding="utf-8")
    parsed = critique_markdown_utils.parse_critique(text)
    # An empty file is the critique's "nothing to say"; any other file it cannot read loses its rules, and says so
    if parsed.critique is None and text.strip():
        _LOG.warning("%s.md holds no readable critique, counted as no rules: %s", slug, parsed.why)

    return parsed.critique or _build_no_critique()


def write_second_pass_record(paths: RunPaths,
                             slug: str,
                             answer: dict[str, Any]) -> Path:
    """R19: a batch's second look, whole, as it was answered. Nothing to say at all is an empty file."""

    is_empty = not answer.get("is_nothing_to_change") and not answer.get("additions") and not any(
        answer.get(field) for field in frame_record.BATCH_SECTIONS)

    return _write(paths, slug, "" if is_empty else frame_markdown_utils.render_second_pass_document(answer))


def read_second_pass_record(paths: RunPaths,
                            slug: str) -> dict[str, Any]:
    """`{"is_nothing_to_change", "additions"}` and each field of `frame_record.BATCH_SECTIONS`, as the second pass
    answered them."""

    parsed = _read(paths, slug, is_addition=True)
    batch = parsed["batch"] or {}

    return {"is_nothing_to_change": parsed["is_nothing_to_change"], "additions": parsed["frames"],
            **{field: batch.get(field, "") for field in frame_record.BATCH_SECTIONS}}
