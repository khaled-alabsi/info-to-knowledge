"""Phase 2's redundancy filter, video only: a small vision model is asked, a batch of consecutive
frames at a time, which frames another frame already holds whole, and those leave the run as the knowledge filter's
do: their image moves into `frames/dropped/`, and `droppedFrames` lists each with `droppedBy: "redundancy"`, the
frames said to hold it (`coveredBy`) and the model's reason.

The program decides what is removed, never the model alone: a reply is checked first
(`validators/frame_redundancy_validator.py`), and a removed frame whose covering frames were all removed too is kept
(`utils/frame_coverage_utils.py`). Measured once, offline.
"""

from __future__ import annotations

import collections
import difflib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy

from chains import chain_runner
from config import FrameSettings
from models import output_contract
from models.frame_judging_spec import FrameJudgingSpec
from prompts import frame_redundancy_messages
from services import apple_text_service
from services import attempt_log_service
from services import board_frame_service
from services import call_record_service
from services import chat_model_service
from services import knowledge_filter_service
from services import text_detection_service
from utils import change_capture_utils
from utils import chain_response_utils
from utils import frame_coverage_utils
from utils import frame_group_utils
from validators import frame_redundancy_validator

__all__ = ["filter_frames", "judge_calls", "build_judging_spec", "list_call_positions", "map_call_of_frames", "set_model_factory", "MODEL_FACTORY", "DROPPED_BY",
           "MAX_HOLDER_CHANGE", "FILTER_LOAD_SECONDS", "build_filter_model", "list_repeat_copies",
           "REPEAT_COPY_DROPPED_BY", "is_nothing_new", "list_new_numbers"]

DROPPED_BY = "redundancy"
# the phase its calls are counted under in the run's call metrics
CALL_PHASE = output_contract.FRAMES_PREPARING_PHASE
# A bare word: two letters or digits or more (a `return "hi"` changed into a real body differed only by a two-letter
# word, and a word check of three letters let its removal through: Lecture 9 frame-100)
_BARE_WORD = re.compile(r"[a-z0-9]{2,}")
# The most of the capture loop's blocks a removed frame may differ in from the frame said to hold it. Over 17 removals
# judged by looking: the right ones 1% to 12.3% (a zoomed view 36%, an empty prompt 37%), the wrong ones 17.2% (a web
# page's results list beside a loading page, its words too small to read) to 59% (a picture changed beside the same
# bullets, DEFECTS 94; a code slide gaining a note and a diagram)
MAX_HOLDER_CHANGE = 0.15
# The most blocks on which a removed frame shows marks where its holder shows empty background, compared with a
# pixel's shift allowed. A lecture's slide with handwritten labels and one with a handwritten trace, unread by the text
# reader at 360p and under 15% of the blocks changed, were removed as held by the same slide without them (25 and 9
# such blocks); the right removal of the same slide with the same notes had 0, and a meeting's panel scrolled away
# from its first rows 12 (DEFECTS 114)
MAX_ADDED_BLOCKS = 2

# How long LM Studio keeps the filter's model loaded once idle, when it is not phase 3's model: with the 24 hours every
# other call asks for, 38c3 talk 797's filter model (3.7 GB) stayed loaded beside phase 3's 35B model, memory ran out,
# and phase 3's first batch of 4 frames ran over an hour (DEFECTS 473). The filter's calls come about 43 s apart, so a
# stage never waits on a reload
FILTER_LOAD_SECONDS = 300


def build_filter_model(name: str) -> Any:
    """The filter's model under `name`, LM Studio unloading it `FILTER_LOAD_SECONDS` after its last call, unless it is
    the model phase 3 calls next, which keeps the usual time."""

    is_kept = chat_model_service.is_phase_model_spec(name, "frames_processing")

    return chat_model_service.get_named_model(name, chat_model_service.LMSTUDIO_LOAD_SECONDS if is_kept else FILTER_LOAD_SECONDS)


# What builds the filter's model from its name. A module-level name with a setter: the suite runs with no model host,
# so a test swaps in a fake that replies to each call.
MODEL_FACTORY: Callable[[str], Any] = build_filter_model


def _describe_broken_rules(broken_rules: collections.Counter[str]) -> str:
    """`31 decision(s) kept against a reply that broke a rule (DISCARD without a reason: 20, two decisions: 11)`."""

    total = sum(broken_rules.values())
    listed = ", ".join(f"{rule}: {count}" for rule, count in broken_rules.most_common())

    return f"{total} decision(s) kept against a reply that broke a rule" + (f" ({listed})" if listed else "")


def _save_call(spec: FrameJudgingSpec,
               slugs: list[str],
               messages: list,
               reply_text: str,
               is_readable: bool) -> None:
    """One call as an attempt of its own (`logs/<session>/frames_preparing/<layer>-<first>-<last>/attempt-1/`): what it
    was sent, the frame each image is (`images.json`, in order: the model names frames by their place in the call), its
    reply and whether the reply could be read. Read by the detail view's "What the worker saw"."""

    if spec.run_dir is None:
        return
    log = attempt_log_service.claim_attempt_dir(spec.run_dir, CALL_PHASE, f"{spec.dropped_by}-{slugs[0]}-{slugs[-1]}", 1)
    system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
    log.write_system_prompt(system_text)
    log.write_human_message(human_text)
    (log.directory / attempt_log_service.IMAGES_FILE).write_text(json.dumps([{"slug": slug} for slug in slugs]), encoding="utf-8")
    (log.directory / "reply.md").write_text(reply_text, encoding="utf-8")
    log.write_decision({"accepted": is_readable})


def _read_reply_text(reply: Any) -> str:
    """A chat reply's text: a string, or the text parts of a list of content parts."""

    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)

    return str(content)


def _count_bare_words(item: dict,
                      frames_dir: Path) -> collections.Counter[str]:
    """How often each word the text reader finds on a frame stands there, as bare runs of two letters or digits or
    more, lower case: its punctuation and quote marks read differently on two frames of one screen."""

    words = text_detection_service.find_words(frames_dir / Path(item["path"]).name)

    return collections.Counter(token for word in words for token in _BARE_WORD.findall(str(word["text"]).lower()))


def _list_missing_words(item: dict,
                        holders: list[dict],
                        frames_dir: Path) -> list[str]:
    """The removed frame's words its holders show fewer times, once per time short. Counted, never as sets: a
    terminal's result lines repeat words that stand once in a table above on the holder, and as sets nothing was
    missing (DEFECTS 89)."""

    held = collections.Counter()
    for holder in holders:
        held |= _count_bare_words(holder, frames_dir)

    return list((_count_bare_words(item, frames_dir) - held).elements())


def _shrink_whole_frame(item: dict,
                        frames_dir: Path) -> numpy.ndarray:
    """A frame's image in grey, shrunk whole to the size the capture loop compares."""

    gray = board_frame_service.read_gray_frame(frames_dir, item)

    return change_capture_utils.shrink_screen(gray, (0, 0, gray.shape[1], gray.shape[0]))


def _compute_holder_change(changed_maps: list[numpy.ndarray]) -> float:
    """The least share of the capture loop's blocks the removed frame differs in from any of its holders, from each
    holder's map of changed blocks."""

    ignored = numpy.zeros((change_capture_utils.BLOCK_ROWS, change_capture_utils.BLOCK_COLUMNS), bool)

    return min(change_capture_utils.compute_change(changed, ignored) for changed in changed_maps)


def _count_added_blocks(removed: numpy.ndarray,
                        held: list[numpy.ndarray],
                        changed_maps: list[numpy.ndarray]) -> int:
    """The fewest blocks, over the holders, on which the removed frame shows marks where that holder shows empty
    background."""

    is_removed_empty = change_capture_utils.map_empty_blocks(removed)

    return min(int((changed & change_capture_utils.map_empty_blocks(holder) & ~is_removed_empty).sum())
               for holder, changed in zip(held, changed_maps))


# Why a standing removal is kept back, each checked in this order, the first that holds named, and how the stage's
# console line counts it: a new check is one entry here and one branch in `_find_keep_back_reason`
_WORDS_MISSING, _FEW_WORDS, _PICTURE_CHANGED, _MARKS_ADDED = "words missing", "few words", "picture changed", "marks added"
_KEEP_BACK_CLAUSES = {_WORDS_MISSING: "kept back because their holders lack their words",
                      _FEW_WORDS: "kept back because they show under {min_words} different words to check",
                      _PICTURE_CHANGED: "kept back because they differ from their holders in over {max_change:.0%} of "
                                        "the screen",
                      _MARKS_ADDED: "kept back because they show marks where their holders are blank"}


def _describe_kept_back(kept_back: collections.Counter[str],
                        min_words: int) -> str:
    """The console line's counts of removals kept back, one clause per reason in `_KEEP_BACK_CLAUSES`' order."""

    return ", ".join(f"{kept_back[reason]} " + clause.format(min_words=min_words, max_change=MAX_HOLDER_CHANGE)
                     for reason, clause in _KEEP_BACK_CLAUSES.items())


def _find_keep_back_reason(item: dict,
                           holders: list[dict],
                           frames_dir: Path,
                           spec: FrameJudgingSpec) -> str | None:
    """Why the removal of `item`, held by `holders`, is kept back, or None when it stands. Every check sits under the
    word check (`frames.redundancyMaxMissingWords`), as it was built: with that off, the model's reason alone decides."""

    if len(_list_missing_words(item, holders, frames_dir)) > spec.max_missing_words:
        return _WORDS_MISSING
    # A frame with too few words to compare (a photo, a room shot, a title card) passes the word check unseen: on four
    # recordings every wrong removal that passed it showed 0 to 2 words
    if len(_count_bare_words(item, frames_dir)) < spec.min_words:
        return _FEW_WORDS
    # Each image read and shrunk once, and each holder compared once, for the two picture checks
    removed = _shrink_whole_frame(item, frames_dir)
    held = [_shrink_whole_frame(holder, frames_dir) for holder in holders]
    changed_maps = [change_capture_utils.map_changed_blocks(removed, holder, change_capture_utils.SHIFT_PX)
                    for holder in held]
    # A picture that changed beside the same words passes the word check: the removed frame must look like a holder too
    if _compute_holder_change(changed_maps) > MAX_HOLDER_CHANGE:
        return _PICTURE_CHANGED
    # Marks drawn where the holder is blank (handwriting the text reader cannot read) are knowledge it lacks
    if _count_added_blocks(removed, held, changed_maps) > MAX_ADDED_BLOCKS:
        return _MARKS_ADDED

    return None


REPEAT_COPY_DROPPED_BY = "repeat-copy"
# A return's line, read by Apple's recognizer, is one an earlier frame shows when the two read alike at this share
# (difflib's ratio). Read by Tesseract, two pictures of one slide differed in 4 to 9 words of small type ("fiir" for
# "für"), and twelve identical returns of postgresql's talk reached phase 3 (DEFECTS 666). Digits are no exception:
# a footer's tiny "© 2024" read "02224" on one of two identical pictures
_RETURN_LINE_SAME = 0.8
# A line shorter than this is not weighed: a lone bullet, a page number
_RETURN_LINE_MIN_CHARS = 4
# The kept frames before a return whose lines count as shown, beside its first showing: a terminal scrolled back shows
# lines several earlier frames printed (measured on six replayed runs)
_RETURN_LOOK_BACK = 12
# Read about this wide, the stored frame enlarged by a whole factor: small type read at 640 lost its letters
_LINE_READ_WIDTH = 1500


def _read_lines(path: Path) -> list[str]:
    """The lines Apple's recognizer reads on a frame about `_LINE_READ_WIDTH` wide, lower case, spaces squeezed."""

    image = cv2.imread(str(path))
    scale = max(1, -(-_LINE_READ_WIDTH // image.shape[1]))
    scaled = image if scale == 1 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    lines: dict[int, list[str]] = {}
    for word in apple_text_service.find_words_in_image(scaled):
        lines.setdefault(int(word.get("line", 0)), []).append(str(word["text"]))

    return [" ".join(" ".join(words).lower().split()) for _, words in sorted(lines.items())]


def list_new_numbers(item: dict,
                     original: dict,
                     frames_dir: Path,
                     read: dict[str, list[str]]) -> set[str]:
    """The digit runs Apple's recognizer reads on `item` that it reads nowhere on `original` (`read` keeps each frame's
    lines by slug): two slides of one template that differ only in their numbers read alike as words and as lines
    ("PostgreSQL 15" and "PostgreSQL 16", postgresql's frames 022 and 025)."""

    for frame in (item, original):
        if frame["slug"] not in read:
            read[frame["slug"]] = _read_lines(frames_dir / Path(frame["path"]).name)
    numbers = [set(re.findall(r"\d+", " ".join(read[frame["slug"]]))) for frame in (item, original)]

    return numbers[0] - numbers[1]


def _is_line_shown(line: str,
                   shown: list[str]) -> bool:
    return any(difflib.SequenceMatcher(None, line, other).ratio() >= _RETURN_LINE_SAME for other in shown)


def _list_new_lines(item: dict,
                    earlier: list[dict],
                    frames_dir: Path,
                    read: dict[str, list[str]]) -> list[str]:
    """The lines of `item` no frame of `earlier` shows, each frame read once into `read` by its slug."""

    for frame in [item, *earlier]:
        if frame["slug"] not in read:
            read[frame["slug"]] = _read_lines(frames_dir / Path(frame["path"]).name)
    shown = [line for frame in earlier for line in read[frame["slug"]]]

    return [line for line in read[item["slug"]] if len(line) >= _RETURN_LINE_MIN_CHARS and not _is_line_shown(line, shown)]


def _is_unchanged_by_words(item: dict,
                           original: dict,
                           frames_dir: Path,
                           settings: FrameSettings) -> bool:
    """Whether Tesseract finds every word of the return on its first showing but for
    `frames.redundancyMaxMissingWords`, of at least `frames.redundancyMinWords` words."""

    if len(_list_missing_words(item, [original], frames_dir)) > (settings.redundancy_max_missing_words or 0):
        return False
    # A return of few words is told apart by those few: a section title "PostgreSQL 17" marked a return of
    # "PostgreSQL 15" (DEFECTS 412) misses one of its three words, which the allowance above lets pass. Allowing
    # none instead fails on a title the text reader reads no word of (those two cards read empty, so nothing misses)

    return len(_count_bare_words(item, frames_dir)) >= settings.redundancy_min_words


def _is_unchanged_by_lines(item: dict,
                           earlier: list[dict],
                           frames_dir: Path,
                           read: dict[str, list[str]],
                           settings: FrameSettings) -> bool:
    """Whether Apple's recognizer reads no line on the return that `earlier` lacks, of at least
    `frames.redundancyMinWords` words: a frame of few words is told apart by those few, which read alike as lines
    ("gateway 17" is "gateway 15" at 92%, DEFECTS 412's section titles)."""

    if _list_new_lines(item, earlier, frames_dir, read):
        return False

    return sum(len(line.split()) for line in read[item["slug"]]) >= settings.redundancy_min_words


def list_repeat_copies(kept: list[dict],
                       frames_dir: Path,
                       settings: FrameSettings) -> list[dict]:
    """The kept frames the capture marked a return to an earlier kept frame (`repeatOf`) that add nothing to it, each
    with that frame as its `coveredBy`: Apple's recognizer reads no line on it that its first showing or the
    `_RETURN_LOOK_BACK` kept frames before it show, and it shows marks on at most `MAX_ADDED_BLOCKS` blocks its first
    showing leaves blank. Where Apple's recognizer does not run, Tesseract's words stand in: every word on it stands on
    the first showing but for `frames.redundancyMaxMissingWords`, of at least `frames.redundancyMinWords` words.
    The capture marks a return only when its picture matches, so the picture's size is not compared again (a return in
    the broadcast's other view is framed otherwise). Returns that showed nothing new reached phase 3 and were dropped by
    hand on six runs (DEFECTS 603); a return that brings a new line, a typed value or a drawing stays."""

    by_slug = {item["slug"]: item for item in kept}
    read: dict[str, list[str]] = {}
    copies = []
    for index, item in enumerate(kept):
        original = by_slug.get(str(item.get("repeatOf") or ""))
        # A screen held 3 minutes is taken again on purpose, so the speech said over it is split (DEFECTS 157): that
        # re-take adds nothing by design, and dropped here it gave all its speech back to the frame before (DEFECTS 665)
        if original is None or item.get("isHeldRetake"):
            continue
        earlier = [original, *kept[max(0, index - _RETURN_LOOK_BACK):index]]
        if is_nothing_new(item=item, earlier=earlier, frames_dir=frames_dir, read=read, settings=settings):
            copies.append({**item, "coveredBy": [original["slug"]]})

    return copies


def is_nothing_new(item: dict,
                   earlier: list[dict],
                   frames_dir: Path,
                   read: dict[str, list[str]],
                   settings: FrameSettings) -> bool:
    """Whether a kept frame adds nothing to `earlier[0]`, the frame it shows again: marks on at most `MAX_ADDED_BLOCKS`
    blocks that frame leaves blank, and either Tesseract's words all on that frame but for
    `frames.redundancyMaxMissingWords`, or, where Apple's recognizer runs, no line it reads that `earlier` lacks (each
    from `frames.redundancyMinWords` words read). `read` keeps Apple's lines by slug across calls. The repeat-copy step
    and the similarity recheck share it."""

    original = earlier[0]
    removed, held = _shrink_whole_frame(item, frames_dir), _shrink_whole_frame(original, frames_dir)
    changed = change_capture_utils.map_changed_blocks(removed, held, change_capture_utils.SHIFT_PX)
    if _count_added_blocks(removed, [held], [changed]) > MAX_ADDED_BLOCKS:
        return False
    # Each reader misreads its own frames into new words: Tesseract a slide's small type, Apple a map's tiny labels
    # ("geper 5", openpv's frame 020). A frame adds nothing when either finds nothing new; over the six replayed runs
    # this loses no frame the audits kept for what it shows
    if _is_unchanged_by_words(item, original, frames_dir, settings):
        return True

    return apple_text_service.is_available() and _is_unchanged_by_lines(item, earlier, frames_dir, read, settings)


def set_model_factory(factory: Callable[[str], Any]) -> None:
    global MODEL_FACTORY
    MODEL_FACTORY = factory


def build_judging_spec(settings: FrameSettings,
                       report_progress: Callable[[int], None] | None = None,
                       run_dir: Path | None = None) -> FrameJudgingSpec:
    """How the redundancy filter asks its model, for `filter_frames`; the similarity recheck copies it with its own
    model, label and `droppedBy`, so it keeps every check of the filter."""

    return FrameJudgingSpec(model=settings.redundancy_model, frame_width=settings.redundancy_frame_width,
                            dropped_by=DROPPED_BY, label=f"redundancy filter ({settings.redundancy_model})",
                            report_progress=report_progress, max_missing_words=settings.redundancy_max_missing_words,
                            run_dir=run_dir, min_words=settings.redundancy_min_words)


def filter_frames(items: list[dict],
                  frames_dir: Path,
                  spec: FrameJudgingSpec,
                  frames_per_call: int,
                  log: logging.Logger) -> tuple[list[dict], list[dict]]:
    """`items` asked `frames_per_call` consecutive frames a call. The kept items and the dropped ones, each dropped one
    `{"slug", "index", "frameNumber", "timestamp", "path", "dropScore", "droppedBy", "coveredBy", "reason"}` with its
    image moved into `frames/dropped/`; no kept item points at a dropped one afterwards."""

    return judge_calls(items, list_call_positions(len(items), frames_per_call), frames_dir, spec, log)


def list_call_positions(count: int,
                        frames_per_call: int) -> list[list[int]]:
    """The filter's calls over `count` frames, as lists of positions: `frames_per_call` consecutive frames a call."""

    return [list(range(start, min(start + frames_per_call, count))) for start in range(0, count, frames_per_call)]


def map_call_of_frames(items: list[dict],
                       frames_per_call: int) -> dict[str, int]:
    """Each frame's slug -> the number of the filter's call that showed it, from the same calls `filter_frames` sends:
    the similarity recheck asks only frames no call showed together."""

    return {items[position]["slug"]: number
            for number, call in enumerate(list_call_positions(len(items), frames_per_call)) for position in call}


def judge_calls(items: list[dict],
                calls: list[list[int]],
                frames_dir: Path,
                spec: FrameJudgingSpec,
                log: logging.Logger) -> tuple[list[dict], list[dict]]:
    """`items` judged by `spec.model`, one call per list of positions in `calls`: the kept items and the dropped ones,
    each dropped one listed under `spec.dropped_by` with the frames said to hold it and the model's reason."""

    # STEP 1: every frame gets an id by its place, F001 ..., the name the model reads and writes
    ids = [f"F{number:03d}" for number in range(1, len(items) + 1)]
    item_by_id = dict(zip(ids, items))
    model = MODEL_FACTORY(spec.model)
    # Asked with no thinking, as an offline measurement did for every model; the model's own sampling is kept (a model
    # with no thinking mode, such as LFM, only gets LM Studio's warning)
    if hasattr(model, "bind"):
        model = model.bind(reasoning_effort="none")

    # STEP 2: one call per list of frames, its reply checked before anything is removed
    removed: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    unreadable_count, judged, started = 0, 0, time.perf_counter()
    # each rule a reply broke, with how many decisions it turned into KEEP, so a run's overrules can be judged
    broken_rules: collections.Counter[str] = collections.Counter()
    is_word_check_off = spec.max_missing_words is None
    # each call a row of phase 2 under the stage's name, so phase 2's closing line counts its calls and tokens (it said
    # "no model call" after 85 calls of its two model stages); a spec with no run folder writes none
    if spec.run_dir is None:
        call_record_service.clear_place()
    else:
        call_record_service.set_place(run_dir=spec.run_dir,
                                      phase=CALL_PHASE,
                                      slug=spec.dropped_by,
                                      attempt=1)
    for call in calls:
        call_ids = [ids[position] for position in call]
        judged += len(call_ids)
        if len(call_ids) < 2:
            continue
        messages = frame_redundancy_messages.build_frame_redundancy_messages(
            [(frame_id, frames_dir / Path(item_by_id[frame_id]["path"]).name) for frame_id in call_ids],
            spec.frame_width)
        reply = chain_runner.invoke_reply(model=model,
                                          messages=messages)
        reply_text = _read_reply_text(reply)
        decisions, is_readable = frame_redundancy_validator.check_reply(reply_text, call_ids, is_word_check_off)
        _save_call(spec, [item_by_id[frame_id]["slug"] for frame_id in call_ids], messages, reply_text, is_readable)
        unreadable_count += not is_readable
        # A decision its checks turned into KEEP: the model's reply broke a rule (`kept_reason` says which)
        if is_readable:
            broken_rules.update(decision.kept_reason for decision in decisions if decision.kept_reason)
        frame_coverage_utils.apply_decisions(removed, decisions)
        reasons.update({decision.frame_id: decision.reason for decision in decisions if decision.reason})
        if spec.report_progress is not None:
            spec.report_progress(judged)

    # STEP 3: a removal stands only when a frame that stays holds the frame's knowledge, and when its holders show the
    # removed frame's words (the model named a holder that does not hold it on 4 of Lecture 9's 8 wrong removals)
    held = frame_coverage_utils.list_standing_removals(removed, ids)
    kept_back: collections.Counter[str] = collections.Counter()
    standing = {}
    for frame_id, holders in held.items():
        reason = None if is_word_check_off else _find_keep_back_reason(
            item_by_id[frame_id], [item_by_id[i] for i in holders], frames_dir, spec)
        if reason is None:
            standing[frame_id] = holders
        else:
            kept_back[reason] += 1
    kept, dropped = [], []
    for frame_id in ids:
        item = item_by_id[frame_id]
        if frame_id not in standing:
            kept.append(item)
            continue
        entry = knowledge_filter_service.move_to_dropped(frames_dir, item, None, spec.dropped_by)
        covering = [item_by_id[i]["slug"] for i in standing[frame_id]]
        dropped.append({**entry, "coveredBy": covering, "reason": reasons.get(frame_id, "")})
    # A call of one frame is never sent, so its frame is not counted as asked
    log.info(f"  {spec.label}: {len(dropped)} of {frame_group_utils.count_distinct_frames([call for call in calls if len(call) >= 2])} "
             f"frame(s) asked dropped as "
             f"held whole by another frame, {len(removed) - len(held)} kept back "
             f"because no frame that stays held them, {_describe_kept_back(kept_back, spec.min_words)}, "
             f"{_describe_broken_rules(broken_rules)}, {unreadable_count} "
             f"unreadable repl(ies), {time.perf_counter() - started:.1f} s")

    # STEP 4: no kept frame points at a dropped one
    kept = knowledge_filter_service.build_relinked_frames(kept, {entry["slug"] for entry in dropped},
                                                         {entry["slug"]: entry["coveredBy"] for entry in dropped})

    return kept, dropped
