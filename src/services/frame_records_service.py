"""The accepted notes of each frame, kept in that frame's own item of `02_frames.json`. REQUIREMENTS.md R4.

Phase 2 lists the frames and phase 3 fills in what each one taught, in ONE file: an item holds its `record` and, under
`builtFrom`, the earlier frames whose notes it was shown. Until 2026-09-27 the notes were a second file,
`03_frame_analyses.json`, that repeated every frame's slug, index and time (the owner: "why they cant be one file").

Written as it changes, never once at the end: every accepted frame is put into the file the moment it is accepted, so
a run killed at frame 12 leaves eleven frames with notes, and the layer-2 merge can read them.

Two ways in, and they differ on purpose:

  a pass over every frame   CLEARS every item's notes first, so it never mixes this run's frames with notes an
                            earlier run produced under another prompt or another model
  `--worker N`              rewrites item N's notes and leaves every other item untouched

Frames are sequential, so after one frame is rewritten, the frames that were built on its OLD notes can be named.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from schemas import frame_record
from services import phase_file_service
from utils import document_text_utils

__all__ = ["read_frame_records", "clear_all_frame_records", "clear_frame_records", "save_frame_record",
           "find_repeated_screen", "read_frame_speech", "find_latest_diagram", "map_records_by_index",
           "has_every_frame_record", "list_previous_records", "list_stale_slugs", "list_record_slugs", "RECORD_CONTEXT_KEYS",
           "FRAMES_PHASE_NUMBER", "is_figure_placed_only_as_cut"]

FRAMES_PHASE_NUMBER = output_contract.PHASE_NUMBERS["frames_preparing"]
_RECORD_FIELD = "record"
_BUILT_FROM_FIELD = "builtFrom"
_NOTE_FIELDS = (_RECORD_FIELD, _BUILT_FROM_FIELD)
# The keys `read_frame_records` adds to a frame's notes from its item; taken off again before the notes are stored
RECORD_CONTEXT_KEYS = ("slug", "index", "timestamp", _BUILT_FROM_FIELD)


def _read_frames_file(paths: RunPaths) -> dict[str, Any]:
    return phase_file_service.read_phase_file(paths, FRAMES_PHASE_NUMBER)


def _list_items(paths: RunPaths) -> list[dict[str, Any]]:
    """The frames file's items; none when phase 2's file is not there yet."""

    if not phase_file_service.has_phase_file(paths, FRAMES_PHASE_NUMBER):
        return []

    return _read_frames_file(paths).get("items", [])


def _find_item(items: list[dict[str, Any]],
               slug: str) -> dict[str, Any]:
    """The item of the frames file with this slug; an empty one when there is none."""

    return next((item for item in items if item.get("slug") == slug), {})


def _has_record(item: dict[str, Any]) -> bool:
    return _RECORD_FIELD in item


def list_record_slugs(items: list[dict[str, Any]]) -> set[str]:
    """The slugs of the frames among `items` (the frames file's) that have notes."""

    return {item["slug"] for item in items if _has_record(item)}


def read_frame_records(paths: RunPaths) -> list[dict[str, Any]]:
    """Every frame that has notes, as `{"slug", "index", "timestamp", "builtFrom", **record}`, in frame order. No frames
    file, or no notes yet, is no entries."""

    items = sorted(_list_items(paths), key=lambda item: item["index"])

    # Read through the secret hiding too: notes stored before a hiding rule existed never carry a value on into the
    # merged notes, the document or the review (a second look once stored a password one line under its row)
    return [{"slug": item["slug"], "index": item["index"], "timestamp": item["timestamp"],
             _BUILT_FROM_FIELD: list(item.get(_BUILT_FROM_FIELD) or []),
             **document_text_utils.hide_secrets_in_record(item[_RECORD_FIELD])}
            for item in items if _has_record(item)]


def list_figure_indexes(paths: RunPaths) -> set[int]:
    """The index of every frame that is one figure of a PDF, which phase 4 always places as its cut."""

    return {int(item["index"]) for item in _list_items(paths) if item.get("medium") == output_contract.PDF_FIGURE_MEDIUM}


def map_records_by_index(paths: RunPaths) -> dict[int, dict[str, Any]]:
    """Every frame's record by its index (its place in the frame list, not its `frameNumber` in the video)."""

    return {int(record["index"]): record for record in read_frame_records(paths)}


def _rewrite_items(paths: RunPaths,
                   rewrite_item: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """The frames file written again with each item passed through `rewrite_item`."""

    frames_file = _read_frames_file(paths)
    items = [rewrite_item(item) for item in frames_file.get("items", [])]
    phase_file_service.write_phase_file(paths, FRAMES_PHASE_NUMBER, {**frames_file, "items": items})


def _drop_notes(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in _NOTE_FIELDS}


def has_every_frame_record(paths: RunPaths) -> bool:
    """Whether every frame phase 3 is to read has its notes: at least one frame, the ignored ones left out."""

    wanted = [item for item in _list_items(paths) if not item.get("ignored")]

    return bool(wanted) and all(_has_record(item) for item in wanted)


def clear_all_frame_records(paths: RunPaths) -> None:
    """Every item's notes removed; the frames themselves stay."""

    _rewrite_items(paths, _drop_notes)


def clear_frame_records(paths: RunPaths,
                        slugs: list[str]) -> None:
    """The notes of these frames removed, every other frame's kept: a batch blocked after its frames were accepted is
    asked again whole, and phase 3 never reads as finished while it is."""

    _rewrite_items(paths, lambda item: _drop_notes(item) if item["slug"] in slugs else item)


def save_frame_record(paths: RunPaths,
                      item: dict[str, Any],
                      record: dict[str, Any],
                      built_from: list[str]) -> None:
    """Put one accepted frame's notes into its own item, replacing its earlier notes and no other item's."""

    _rewrite_items(paths, lambda existing: {**existing, _RECORD_FIELD: dict(record), _BUILT_FROM_FIELD: list(built_from)}
                   if existing["slug"] == item["slug"] else existing)


def read_frame_medium(paths: RunPaths,
                      slug: str) -> str:
    """The frame's item `medium`: `pdf` for a PDF's page, `pdf-figure` for a figure of one read as text; empty else."""

    return str(_find_item(_list_items(paths), slug).get("medium") or "")


def is_figure_placed_only_as_cut(paths: RunPaths,
                                 slug: str) -> bool:
    """Whether the frame is a PDF's figure its notes hold no markdown block of, so it is placed as its cut and never
    left out; a figure written as a table or code is carried by that block and may be left out (DEFECTS 517)."""

    item = _find_item(_list_items(paths), slug)
    markdown = str((item.get("record") or {}).get("as_markdown") or "").strip()

    return (item.get("medium") == output_contract.PDF_FIGURE_MEDIUM
            and (not markdown or markdown == frame_record.EMPTY_MARK))


def read_frame_speech(paths: RunPaths,
                      slug: str) -> str:
    """What was said from this frame until the next one (`transcriptCurrent`); empty before phase 2's file is there."""

    return str(_find_item(_list_items(paths), slug).get("transcriptCurrent") or "")


def read_frame_image_path(paths: RunPaths,
                          slug: str) -> Path:
    """The frame's stored image, as phase 2 wrote it."""

    return paths.output_root / str(_find_item(_list_items(paths), slug).get("path") or "")


def read_frame_speech_before(paths: RunPaths,
                             slug: str) -> str:
    """What was said before this frame (`transcriptBefore`): for a PDF figure, its page's text before it."""

    return str(_find_item(_list_items(paths), slug).get("transcriptBefore") or "")


# A part that only heads the parts under it, as the program writes a step's or a screen's parts
_PARTS_BELOW = ": the parts below"


def _list_outline_parts(screen: str) -> set[str]:
    """An outline's parts as plain lines, with no indent, no bullet and no heading of a part's own ("...: the parts
    below")."""

    return {line.strip().lstrip("-").strip() for line in screen.splitlines()
            if line.strip() and not line.rstrip().endswith(_PARTS_BELOW)}


def _drop_held_outlines(screens: list[str]) -> list[str]:
    """The build's outlines, first step first, without each one a later step's outline holds line for line: a step
    outlined whole, as a step's notes are told not to, repeated the steps before it, and a repeat of it showed the
    slide three times (froscon talk 3143, frame 28 of the build of frames 24 to 26, DEFECTS 258)."""

    parts = [_list_outline_parts(screen) for screen in screens]

    return [screen for position, screen in enumerate(screens)
            if not any(parts[position] <= later for later in parts[position + 1:])]


def find_repeated_screen(paths: RunPaths,
                         slug: str) -> str:
    """The `## Screen` of the frame this one shows again (`repeatOf`), from its accepted notes; empty when the frame
    is no repeat or the frame it repeats has no accepted notes yet. A repeat of a later step of a build gets the
    build's outlines up to that step, first step first: the step's own outline holds only what it added, and a repeat
    given that alone was read as a screen that gains those parts there."""

    items = _list_items(paths)
    repeated = str(_find_item(items, slug).get("repeatOf") or "")
    if not repeated:
        return ""
    original = _find_item(items, repeated)
    start = str(original.get("stepOf") or "")
    build = ([item for item in items if (item.get("slug") == start or item.get("stepOf") == start)
              and int(item.get("index", 0)) <= int(original.get("index", 0))] if start else [original])
    screens = [str((item.get(_RECORD_FIELD) or {}).get("screen") or "").strip() for item in build]
    screen = "\n".join(_drop_held_outlines(screens)) if all(screens) else ""

    # Hidden as every read of the notes is: the repeat's own notes are written from this outline
    return document_text_utils.hide_secrets_in_record({"screen": screen})["screen"]


def list_previous_records(records: list[dict[str, Any]],
                          index: int,
                          limit: int | None) -> list[dict[str, Any]]:
    """Of `records` (`read_frame_records`), the frames before frame `index`, oldest first. A limit keeps the NEWEST
    ones whole: the notes nearest the frame are the ones it most needs."""

    earlier = [entry for entry in records if entry["index"] < index]

    return earlier if limit is None else earlier[-limit:]


def find_latest_diagram(records: list[dict[str, Any]],
                        index: int) -> dict[str, Any] | None:
    """The newest accepted entry before frame `index` that drew a diagram, however far back, as `{"index", "slug",
    "diagram"}`, or None. A diagram built up over several frames is extended from this one, so it must not fall out
    of view just because it is older than the newest few notes."""

    drawn = [entry for entry in records
             if entry["index"] < index and str(entry.get("diagram", "")).strip()]
    latest = None if not drawn else {"index": drawn[-1]["index"], "slug": drawn[-1]["slug"],
                                     "diagram": drawn[-1]["diagram"]}

    return latest


def list_stale_slugs(paths: RunPaths,
                     rewritten_slug: str) -> list[str]:
    """The frames that were shown the notes of `rewritten_slug` before it was rewritten."""

    return [entry["slug"] for entry in read_frame_records(paths) if rewritten_slug in entry.get(_BUILT_FROM_FIELD, [])]
