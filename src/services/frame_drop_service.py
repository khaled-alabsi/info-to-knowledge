"""Frames taken out of a run after phase 2, before phase 3 reads them: proposed by the frame knowledge classifier (the
detail view's Refine) or chosen by hand (Drop selected), and dropped only when the person confirms.

A dropped frame leaves the run the way phase 2's knowledge filter drops one: its image moves into `frames/dropped/`, its
entry goes from `items` to `droppedFrames` in `02_frames.json`, it leaves every run record's items (`logs/<run id>/
run.json`), so no resume queues it, and the speech said while it was on screen goes to the frame before it. A kept frame
that pointed at it as a repeat or a build's first frame is linked as phase 2 links one. Only a frame phase 3 has not
written notes for can be dropped: its notes are paid for.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import config
from models import output_contract
from models.frame_drop_result import FrameDropResult
from models.run_paths import RunPaths
from services import frame_records_service
from services import knowledge_correction_service
from services import knowledge_filter_service
from services import phase_file_service
from utils import json_store_utils
from utils import transcript_slice_utils

__all__ = ["DROPPED_BY_REFINE", "DROPPED_BY_HAND", "DECISION_DROP", "DECISION_KEEP", "list_droppable_items", "find_frames_to_drop", "drop_frames"]

DROPPED_BY_REFINE = "refine"
DROPPED_BY_HAND = "hand"
# What the classifier proposes for a frame, which the page and the drop read
DECISION_DROP = "drop"
DECISION_KEEP = "keep"
_TRANSCRIPT_PHASE_NUMBER = output_contract.PHASE_NUMBERS["transcribe"]
# The fields of a kept frame a drop recomputes, in the frames file and in every run record
_RECOMPUTED_FIELDS = ("transcriptBefore", "transcriptCurrent", "repeatOf", "stepOf")


def _read_frames_file(paths: RunPaths) -> dict:
    return phase_file_service.read_phase_file(paths, frame_records_service.FRAMES_PHASE_NUMBER)


def _build_resliced_items(paths: RunPaths,
                          kept: list[dict],
                          written: set[str]) -> list[dict]:
    """Each kept frame phase 3 has not read, with its speech cut again from the transcript as phase 2 cuts it: what a
    dropped frame heard goes to the frame before it. A frame with notes keeps the speech its notes were written from."""

    segments = phase_file_service.read_phase_file(paths, _TRANSCRIPT_PHASE_NUMBER).get("segments", [])
    end_s = max([float(segment.get("end", 0.0)) for segment in segments] + [float(item["timestamp"]) for item in kept]
                + [0.0])
    slices = transcript_slice_utils.build_frame_slices(segments, [item["timestamp"] for item in kept], end_s,
                                                       config.settings.prompt.transcript_before_seconds)

    return [item if item["slug"] in written
            else {**item, "transcriptBefore": spoken["before"], "transcriptCurrent": spoken["current"]}
            for item, spoken in zip(kept, slices)]


def _with_recomputed_fields(item: dict,
                            kept_item: dict) -> dict:
    """A run record's item holding, of the fields a drop recomputes, those its kept frame has."""

    return {**item, **{field: kept_item[field] for field in _RECOMPUTED_FIELDS if field in kept_item}}


def _update_run_records(paths: RunPaths,
                        kept: list[dict],
                        dropped_slugs: set[str]) -> None:
    """Every run record of the folder without the dropped frames, its kept frames holding the recomputed fields. A run
    record's item with no kept frame stays as it was."""

    by_slug = {item["slug"]: item for item in kept}
    for store_path in sorted(paths.runs_root.glob(f"*/{output_contract.RUN_STATE_FILE}")):
        data = json_store_utils.read_json(store_path)
        items = data.get("items") or {}
        if not dropped_slugs & set(items):
            continue
        data["items"] = {slug: _with_recomputed_fields(item, by_slug.get(slug, {}))
                         for slug, item in items.items() if slug not in dropped_slugs}
        json_store_utils.write_json(store_path, data)


def list_droppable_items(paths: RunPaths) -> list[dict]:
    """The frames of `02_frames.json` phase 3 has not written notes for, in order."""

    items = _read_frames_file(paths).get("items", [])
    written = frame_records_service.list_record_slugs(items)

    return [item for item in items if item["slug"] not in written]


def find_frames_to_drop(paths: RunPaths) -> list[dict]:
    """Every droppable frame with the classifier's drop score and decision, `{"slug", "path", "timestamp",
    "dropScore", "decision"}`, in order. Nothing in the run folder changes."""

    # STEP 1: the classifier judges only the droppable frames, through links in a folder of their own
    command = knowledge_filter_service.resolve_classifier_command(config.settings.frames)
    droppable = list_droppable_items(paths)
    if not droppable:
        return []
    with tempfile.TemporaryDirectory() as scratch:
        for item in droppable:
            (Path(scratch) / Path(item["path"]).name).symlink_to(paths.output_root / item["path"])
        decisions = knowledge_filter_service.read_decisions(Path(scratch), command, knowledge_filter_service.TAGS_TASK)
        tags = {row["frame"]: row for row in decisions["frames"]}

    # STEP 2: each frame with its score and decision: drop for a frame tagged people, its people score as drop score
    proposals = []
    for item in droppable:
        tag = tags[Path(item["path"]).name]
        proposals.append({"slug": item["slug"], "path": item["path"], "timestamp": item["timestamp"],
                          "dropScore": tag["peopleScore"],
                          "decision": DECISION_DROP if tag["tag"] == knowledge_filter_service.PEOPLE else DECISION_KEEP})

    return proposals


def drop_frames(paths: RunPaths,
                slugs: list[str],
                proposed: list[str],
                drop_scores: dict[str, float]) -> FrameDropResult:
    """The frames `slugs` dropped from the run, as the module says. Each is recorded as dropped by `refine` when it is
    among the frames a Refine `proposed`, else by `hand`, with its score from `drop_scores` when it has one. With
    `drop_scores` (a Refine ran), the proposal is accepted: the classifier's mistakes the person corrected are saved as
    learning data, and `slugs` may be empty. A slug that is not a droppable frame of the run is refused by name, and
    nothing is dropped."""

    # STEP 1: every slug names a frame phase 3 has not read
    frames_file = _read_frames_file(paths)
    items = frames_file.get("items", [])
    written = frame_records_service.list_record_slugs(items)
    known = {item["slug"] for item in items}
    unknown = [slug for slug in slugs if slug not in known]
    if unknown:
        raise ValueError(f"not a frame of this run's list: {', '.join(unknown)}")
    already_read = [slug for slug in slugs if slug in written]
    if already_read:
        raise ValueError(f"phase 3 already wrote notes for {', '.join(already_read)}; such a frame is not dropped")

    # STEP 2: an accepted proposal's mistakes saved as learning data, while every image is still where it was
    false_drops, missed_drops, corrections_dir = [], [], None
    if drop_scores:
        false_drops, missed_drops, corrections_dir = knowledge_correction_service.save_corrections(
            paths, items, proposed, slugs, drop_scores)
    if not slugs:
        return FrameDropResult([], false_drops, missed_drops, corrections_dir)

    # STEP 3: the images move into frames/dropped/, the entries into droppedFrames. A frame dropped after the kept
    # frame before it was read is marked: that frame's notes never heard its speech, so the merge keeps it as speech no
    # frame read, and the document must still cite it
    chosen = set(slugs)
    frames_dir = paths.output_root / output_contract.FRAME_IMAGES_DIR_NAME
    dropped, kept_before = [], None
    for item in items:
        if item["slug"] not in chosen:
            kept_before = item["slug"]
            continue
        entry = knowledge_filter_service.move_to_dropped(
            frames_dir, item, drop_scores.get(item["slug"], item.get("dropScore")),
            DROPPED_BY_REFINE if item["slug"] in proposed else DROPPED_BY_HAND)
        dropped.append({**entry, "isSpeechUnheard": kept_before in written})

    # STEP 4: the kept frames: links to a dropped frame redone, speech shared out again
    kept = knowledge_filter_service.build_relinked_frames([item for item in items if item["slug"] not in chosen], chosen)
    kept = _build_resliced_items(paths, kept, written)

    # STEP 5: the frames file and every run record, so no later phase or resume sees the dropped frames
    phase_file_service.write_phase_file(paths, frame_records_service.FRAMES_PHASE_NUMBER, {
        **frames_file, "items": kept, "droppedFrames": [*frames_file.get("droppedFrames", []), *dropped]})
    _update_run_records(paths, kept, chosen)

    return FrameDropResult([entry["slug"] for entry in dropped], false_drops, missed_drops, corrections_dir)
