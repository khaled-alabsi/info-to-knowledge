"""Phase 2's knowledge filter: the frames the extraction took are tagged by the frame classifier, and a frame it tags
`people` (no knowledge: only people, a gallery, a blank or unreadable screen) is dropped before phase 3 pays a call
for it.

The classifier is trained in its own project (`~/projects/frame-knowledge-classifier/`); its inference code is copied
into this repository's `frame-classifier/`, with its own image model, run in the program's environment as its
command, `classifier filter <frames dir> --out <file> --task tags`, and its tags read back:
one model gives each frame `people`, `text` or `other`, with its people score. The same tags mark the text-only frames
(`text_only_service`), so the classifier runs once a phase 2. A dropped frame's image is moved into `frames/dropped/`,
where the UI shows it, and its entry keeps its slug, time and drop score (the people score); a kept frame keeps its
drop score too. A kept frame that was marked a repeat of a dropped frame becomes an
original, and the first kept step of a build whose first frame was dropped becomes that build's first frame.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import config
from config import FrameSettings

__all__ = ["DROPPED_DIR_NAME", "PEOPLE", "TAGS_TASK", "filter_frames", "build_relinked_frames", "resolve_playbook_path",
           "resolve_classifier_command", "read_decisions", "read_tags", "move_to_dropped", "DROPPED_LIST_NAME",
           "clear_dropped_frames", "clear_stale_frames", "write_dropped_list", "find_tag", "add_tags", "list_cut_aways"]

# The dropped frames' images, inside `frames/`: `frames/dropped/frame-012.jpg`.
DROPPED_DIR_NAME = "dropped"
# The dropped frames' entries while phase 2 still runs, beside their images (`frames/dropped/dropped-frames.json`):
# `02_frames.json` is written only at the phase's end, and until then the page could not say who dropped a frame
DROPPED_LIST_NAME = "dropped-frames.json"
# The classifier's tag for a frame with no knowledge on it
PEOPLE = "people"
# The classifier's task that tags every frame people, text or other
TAGS_TASK = "tags"
# The people score from which a frame between two showings of one screen is a camera cut-away (DEFECTS 102): over the
# 50 such frames of 15 public runs, froscon 3143's speaker before the blurred slide scored 0.628 and a lecture's room
# shot of the projected slide 0.668; a lecturer before a chalk diagram scored 0.577, the next highest 0.417
CUT_AWAY_PEOPLE_SCORE = 0.6


def _build_classifier_env() -> dict[str, str]:
    """This process's environment, plus the folder of the classifier weights this repository keeps when one is set."""

    weights = config.settings.frames.knowledge_classifier_weights
    if not weights:
        return dict(os.environ)

    return {**os.environ, "FRAME_CLASSIFIER_WEIGHTS_DIR": str(resolve_playbook_path(weights))}


def resolve_playbook_path(raw: str) -> Path:
    """A path a setting names: `~` expanded, and a relative one taken from the playbook folder."""

    path = Path(raw).expanduser()

    # Its `..` taken out, its links never followed: an environment's `bin/python` is a link to the bare interpreter,
    # and started by the link's target it ran without the environment's packages (DEFECTS 487)
    return path if path.is_absolute() else Path(os.path.normpath(config.PLAYBOOK_ROOT / path))


def read_decisions(frames_dir: Path,
                   command: Path,
                   task: str,
                   vectors_path: Path | None = None) -> dict:
    """The classifier's output for every image of `frames_dir` under its `task`: for `tags`, `{"peopleThreshold",
    "cuts", "frames": [{"frame", "tag", "peopleScore", "boardScore", "isPlainText"}]}` with the tag `people`, `text` or `other`. With
    `vectors_path` (tags only), each frame's SigLIP vectors are saved there too, for the similarity recheck."""

    with tempfile.TemporaryDirectory() as scratch:
        out_path = Path(scratch) / "decisions.json"
        vectors = ["--vectors-out", str(vectors_path)] if vectors_path is not None else []
        finished = subprocess.run([str(command), "filter", str(frames_dir), "--out", str(out_path), "--task", task,
                                   *vectors], capture_output=True, text=True, env=_build_classifier_env())
        if finished.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"the frame knowledge classifier ({command}) failed with exit code "
                               f"{finished.returncode}: {finished.stderr.strip()[-2000:]}")

        return json.loads(out_path.read_text(encoding="utf-8"))


def _find_kept_holder(slug: str,
                      holders: dict[str, list[str]],
                      kept_slugs: set[str]) -> str | None:
    """The first kept frame holding `slug`, following holders that were dropped too (an empty step held by the step
    before it, itself dropped); None when the chain reaches no kept frame."""

    seen, waiting = set(), list(holders.get(slug, []))
    while waiting:
        holder = waiting.pop(0)
        if holder in kept_slugs:
            return holder
        if holder not in seen:
            seen.add(holder)
            waiting += holders.get(holder, [])

    return None


def build_relinked_frames(kept: list[dict],
                         dropped_slugs: set[str],
                         holders: dict[str, list[str]] | None = None) -> list[dict]:
    """`repeatOf` and `stepOf` never name a dropped frame: a repeat of one repeats the first kept frame `holders` says
    holds it (a moved copy's or an empty step's frame, a model stage's `coveredBy`), and becomes an original when none
    does; the first kept step of a build whose first frame was dropped starts the build for the steps after it."""

    shown_before: set[str] = set()
    new_starts: dict[str, str] = {}
    relinked = []
    for item in kept:
        links = {}
        if item.get("repeatOf") in dropped_slugs:
            # A slide shown again after the talk repeated a moved copy of its first showing; with the copy dropped and
            # the mark cleared, it was read as a new slide (froscon talk 3143, DEFECTS 251). Only a frame shown before
            # it can be its original: a closing card's first showing, dropped as held by this very frame, made the
            # frame a repeat of itself (38c3-fault-injection's frame 57, DEFECTS 640)
            links["repeatOf"] = _find_kept_holder(item["repeatOf"], holders or {}, shown_before)
        if item.get("stepOf") in dropped_slugs:
            links["stepOf"] = new_starts.get(item["stepOf"])
            new_starts.setdefault(item["stepOf"], item["slug"])
        relinked.append({**item, **links})
        shown_before.add(item["slug"])

    return relinked


def move_to_dropped(frames_dir: Path,
                    item: dict,
                    drop_score: float | None,
                    dropped_by: str) -> dict:
    """The frame's image moved into `frames/dropped/`, and its entry for `droppedFrames`: `dropped_by` is the layer
    that dropped it, `filter` (phase 2's knowledge filter), `after-talk`, `before-talk`, `empty-screen`, `empty-step`, `pass-through`, `moved-copy`, `repeat-copy`, `board`, `redundancy` or `similarity`
    (phase 2's other stages), `refine` (a proposal accepted in the detail view) or `hand` (chosen there by hand)."""

    name = Path(item["path"]).name
    dropped_dir = frames_dir / DROPPED_DIR_NAME
    dropped_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / name).replace(dropped_dir / name)

    return {"slug": item["slug"], "index": item["index"], "frameNumber": item["frameNumber"],
            "timestamp": item["timestamp"], "path": str(Path(item["path"]).parent / DROPPED_DIR_NAME / name),
            "dropScore": drop_score, "droppedBy": dropped_by}


def clear_dropped_frames(frames_dir: Path) -> None:
    """`frames/dropped/` emptied of what an earlier phase 2 dropped, before this one's stages drop anything."""

    dropped_dir = frames_dir / DROPPED_DIR_NAME
    if dropped_dir.exists():
        shutil.rmtree(dropped_dir)


def clear_stale_frames(frames_dir: Path,
                       taken_names: set[str]) -> None:
    """The frame images an earlier phase 2 wrote and this one did not take removed from `frames/`: left there, the
    classifier, handed the whole folder, tagged them too."""

    for path in frames_dir.glob("frame-*.jpg"):
        if path.name not in taken_names:
            path.unlink()


def write_dropped_list(frames_dir: Path,
                       dropped: list[dict]) -> None:
    """Every frame a filter of this phase 2 dropped so far, written beside their images for the page to read; nothing
    is written when no frame was dropped, so `frames/dropped/` exists only when it holds one."""

    if not dropped:
        return
    dropped_dir = frames_dir / DROPPED_DIR_NAME
    dropped_dir.mkdir(parents=True, exist_ok=True)
    (dropped_dir / DROPPED_LIST_NAME).write_text(json.dumps(dropped, indent=2), encoding="utf-8")


def resolve_classifier_command(settings: FrameSettings) -> Path:
    """The classifier's command, relative to the playbook folder unless absolute; refused by name when missing."""

    command = resolve_playbook_path(settings.knowledge_classifier_command)
    if not command.is_file():
        raise FileNotFoundError(f"frames.knowledgeClassifierCommand points at {command}, which does not exist: install "
                                f"the frame classifier there, or set frames.isKnowledgeFilterOn, "
                                f"frames.isTextOnlyMarkOn and frames.isBoardStageOn to false")

    return command


def read_tags(frames_dir: Path,
              settings: FrameSettings,
              vectors_path: Path | None = None) -> dict[str, dict]:
    """Every image of `frames_dir` by file name -> its `{"frame", "tag", "peopleScore", "boardScore", "isPlainText"}`."""

    command = resolve_classifier_command(settings)

    return {row["frame"]: row for row in read_decisions(frames_dir, command, TAGS_TASK, vectors_path)["frames"]}


def find_tag(tags: dict[str, dict],
             item: dict) -> dict:
    """The classifier's tag row of `item`'s image (`read_tags`); a frame it returned none for stops phase 2 by name."""

    name = Path(item["path"]).name
    tagged = tags.get(name)
    if tagged is None:
        raise RuntimeError(f"the frame classifier returned no tag for {name}")

    return tagged


def add_tags(items: list[dict],
             tags: dict[str, dict]) -> list[dict]:
    """Every item with the classifier's `tag` and `boardScore` from `tags`, whichever stage reads them: the board stage
    reads both, and with only the knowledge filter writing the score, its neighbour rule read 0 with the filter off."""

    return [{**item, "tag": tagged["tag"], "boardScore": tagged.get("boardScore")}
            for item, tagged in ((item, find_tag(tags, item)) for item in items)]


def filter_frames(items: list[dict],
                  frames_dir: Path,
                  tags: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """The kept items and the dropped ones (each `{"slug", "index", "frameNumber", "timestamp", "path", "dropScore",
    "droppedBy", "tag"}`, its image moved into `frames/dropped/`), from the classifier's `tags` (`read_tags`); every
    kept item gets its `dropScore` and `tag` too."""

    # STEP 1: a frame tagged people leaves the list; its image goes into frames/dropped/
    kept, dropped = [], []
    for item in items:
        tagged = find_tag(tags, item)
        if tagged["tag"] != PEOPLE:
            # The tag and board score `add_tags` wrote stay: written again here, a later retag could be undone (DEFECTS 126)
            kept.append({**item, "dropScore": tagged["peopleScore"], "tag": item.get("tag", tagged["tag"]),
                         "boardScore": item.get("boardScore", tagged.get("boardScore"))})
            continue
        # Dropped by the decision model, its score is the share it gives no knowledge and its entry names the kind of
        # empty frame (`decision_filter_service.apply_decisions`)
        if "pKnowledge" in tagged:
            entry = move_to_dropped(frames_dir, item, round(1.0 - float(tagged["pKnowledge"]), 4), "filter")
            dropped.append({**entry, "tag": item.get("tag", tagged["tag"]), "dropClass": tagged.get("dropClass", "")})
            continue
        dropped.append({**move_to_dropped(frames_dir, item, tagged["peopleScore"], "filter"),
                        "tag": item.get("tag", tagged["tag"])})

    # STEP 2: no kept frame points at a dropped one
    kept = build_relinked_frames(kept, {entry["slug"] for entry in dropped})

    return kept, dropped


def list_cut_aways(items: list[dict]) -> list[dict]:
    """Each kept item between two showings of one screen (the next item repeats the one before it, or that one's
    original or build) whose people score (`dropScore`) is at least `CUT_AWAY_PEOPLE_SCORE`, with `coveredBy` the item
    before it: a broadcast cut to the speaker, the slide blurred behind them, between a slide and its repeat, under the
    people bar the classifier's right knowledge frames set (DEFECTS 102)."""

    cut_aways = []
    for before, middle, after in zip(items, items[1:], items[2:]):
        shown = {before["slug"], before.get("repeatOf"), before.get("stepOf")} - {None}
        if after.get("repeatOf") in shown and float(middle.get("dropScore") or 0.0) >= CUT_AWAY_PEOPLE_SCORE:
            cut_aways.append({**middle, "coveredBy": [before["slug"]]})

    return cut_aways
