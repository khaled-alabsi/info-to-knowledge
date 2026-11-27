"""Phase 2: decide which frames exist, and give each one the speech around it. Plain code, but for the two model
stages (the redundancy filter and the similarity recheck), which call a small vision model when they are on.

The transcript is READ FROM DISK, from the file phase 1 wrote. That is what lets `--phase 2` cut the
frames again, with another mode or another threshold, without transcribing again, which is the
expensive half. A missing transcript is refused by name, never treated as a silent video: a video
that really has no speech still has a transcript file, and it says so.

Everything a frame's call will need is computed once, here, and put on the item: its frame, its
timestamp, and its two slices of the transcript.

The stages, in order, each behind its own switch: the extraction's own rules; the frame classifier's tags, people,
text or other, a frame tagged people (no knowledge on it) dropped (`frames.isKnowledgeFilterOn`,
`services/knowledge_filter_service.py`); the board stage (`frames.isBoardStageOn`, `services/board_frame_service.py`);
the redundancy filter, which drops the frames another kept frame holds whole (`frames.isRedundancyFilterOn`,
`services/frame_redundancy_service.py`); the similarity recheck of frames that look alike and no call showed together
(`frames.isSimilarityRecheckOn`, `services/frame_similarity_service.py`); and the text-only mark
(`frames.isTextOnlyMarkOn`, `services/text_only_service.py`); then a frame stored shrunk gets its words read on the
recording at its own size (`services/screen_words_service.py`). Every stage runs before the transcript is sliced, so a
dropped frame's speech goes to the frame before it.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import cv2

import config
from models import output_contract
from models.run_context import RunContext
from services import apple_text_service
from services import board_frame_service
from services import board_text_reader_service
from services import decision_filter_service
from services import empty_step_service
from services import frame_extraction_service
from services import frame_redundancy_service
from services import frame_similarity_service
from services import frames_preparing_progress_service as progress_service
from services import input_range_service
from services import knowledge_filter_service
from services import pdf_source_service
from services import phase_file_service
from services import screen_words_service
from services import slides_source_service
from services import text_detection_service
from services import text_only_service
from utils import frame_group_utils
from utils import screen_crop_utils
from utils import talk_end_utils
from utils import transcript_slice_utils

__all__ = ["prepare_frames"]

_TRANSCRIPT_PHASE_NUMBER = output_contract.PHASE_NUMBERS["transcribe"]
# Each frame's SigLIP vectors from the classifier, in the run's log folder: program state for the similarity recheck
_FRAME_VECTORS_FILE = "frame-vectors.npz"
# A frame before the talk's first words of substance with fewer words than this, on either reader, holds no text: the
# 38c3 opening animation read 2 words on Tesseract and none on Apple's, the 39c3 closing ceremony's title card 4 on Apple's
_BEFORE_TALK_WORDS = 3
# The frames the rules took, kept beside their images so a resume of the same run reads them again: a run stopped by
# a reboot after the rules took 20 minutes took them all again. `reset-to 1` removes it with `frames/`
_RULES_RESULT_FILE = ".rules-result.json"


def _write_rules_result(frames_dir: Path,
                        run_dir: Path,
                        frames: dict) -> dict:
    """`frames` as the rules took them, written for a resume of this run and handed back as written, so a fresh phase 2
    and a resumed one run their later stages on the same values."""

    record = {"run": run_dir.name, "settings": repr(config.settings.frames), "frames": frames}
    text = json.dumps(record)
    (frames_dir / _RULES_RESULT_FILE).write_text(text, encoding="utf-8")

    return json.loads(text)["frames"]


def _read_rules_result(frames_dir: Path,
                       run_dir: Path) -> dict | None:
    """The frames the rules of this same run took before it stopped, the images a later stage moved into
    `frames/dropped/` put back; None when there is no such record, it is another run's or other settings', or an image
    is missing. Read before the dropped folder is emptied."""

    path = frames_dir / _RULES_RESULT_FILE
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("run") != run_dir.name or record.get("settings") != repr(config.settings.frames):
        return None
    dropped_dir = frames_dir / knowledge_filter_service.DROPPED_DIR_NAME
    names = [Path(item["path"]).name for item in record["frames"]["items"]]
    for name in names:
        if not (frames_dir / name).is_file() and (dropped_dir / name).is_file():
            shutil.move(dropped_dir / name, frames_dir / name)
    if not all((frames_dir / name).is_file() for name in names):
        return None

    return record["frames"]


def _report_and_hold(ctx: RunContext,
                     stage: str,
                     done: int,
                     total: int | None) -> None:
    """A stage's progress written, then a pause the person asked for (the run's own, or the queue's) waited out here, so
    phase 2 holds between frames and model calls as phase 3 holds between batches."""

    progress_service.report_stage(ctx.run_dir, stage, done, total)
    _wait_out_pause(ctx)


def _wait_out_pause(ctx: RunContext) -> None:
    """A pause the person asked for (the run's own, or the queue's) waited out here, between two stages or two
    frames; what a stage is doing inside one call cannot be paused."""

    if ctx.control_check is not None:
        ctx.control_check()


def _run_similarity_recheck(ctx: RunContext,
                            kept: list[dict],
                            batches: dict[str, int],
                            frames_dir: Path,
                            vectors_path: Path) -> tuple[list[dict], list[dict]]:
    """The similarity recheck: the kept frames and the ones it dropped, its stage recorded."""

    settings = config.settings.frames
    vectors = frame_similarity_service.read_vectors(vectors_path)
    # The program first drops the alike frames that add nothing, with no model call: the model paired openpv's slide
    # shown again with its first showing and removed none
    copies = frame_similarity_service.list_alike_copies(kept, vectors, frames_dir, settings)
    kept, copied = _drop_copies(kept, frames_dir, copies, frame_similarity_service.DROPPED_BY)
    if copied:
        ctx.log.info(f"  similarity recheck: {len(copied)} frame(s) dropped with no model call, alike to an earlier kept "
                     f"frame and adding nothing to it")
    calls, group_count = frame_similarity_service.build_calls(kept, vectors, batches, settings)
    asked = sum(len(call) for call in calls)
    progress_service.start_stage(ctx.run_dir, progress_service.SIMILARITY, asked)
    spec = frame_similarity_service.build_judging_spec(
        settings, lambda done: _report_and_hold(ctx, progress_service.SIMILARITY, done, asked),
        ctx.run_dir)
    if calls:
        kept, dropped = frame_redundancy_service.judge_calls(kept, calls, frames_dir, spec, ctx.log)
    else:
        dropped = []
    # The done line counts each frame once, as the list line does: a list longer than a call is asked in calls that
    # overlap by half, and counted per call one recording's 25 frames read as 43 (DEFECTS 98)
    distinct = frame_group_utils.count_distinct_frames(calls)
    progress_service.finish_stage(ctx.run_dir, progress_service.SIMILARITY, distinct, ctx.log,
                                  f"{group_count} list(s) of similar frames ({distinct} frames, alike at "
                                  f"{settings.similarity_threshold:.2f} or more), {len(calls)} call(s)")
    progress_service.record_kept(ctx.run_dir, progress_service.SIMILARITY, len(kept))

    return kept, copied + dropped


def _add_dropped(frames_dir: Path,
                 dropped: list[dict],
                 stage_dropped: list[dict]) -> list[dict]:
    """Every frame dropped so far with the ones a stage just dropped, the list beside their images written at once: the
    page reads it while phase 2 runs."""

    dropped = dropped + stage_dropped
    knowledge_filter_service.write_dropped_list(frames_dir, dropped)

    return dropped


def _drop_after_talk(kept: list[dict],
                     segments: list[dict],
                     frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames taken after the talk's last segment of substance, and those frames, dropped as `after-talk`: a
    broadcast's closing cards, said over with thanks alone (DEFECTS 173). Nothing is dropped from a recording whose
    transcript has no such segment."""

    end = talk_end_utils.find_talk_end(segments)
    if end is None:
        return kept, []
    after = [item for item in kept if float(item.get("timestamp") or 0.0) > end]
    dropped = [knowledge_filter_service.move_to_dropped(frames_dir, item, None, "after-talk") for item in after]
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item not in after],
                                                             {entry["slug"] for entry in dropped})

    return staying, dropped


def _is_wordless(path: Path) -> bool:
    """Whether neither text reader finds `_BEFORE_TALK_WORDS` words on the frame, Apple's only where it runs."""

    if len(text_detection_service.find_words(path, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)) >= _BEFORE_TALK_WORDS:
        return False
    if not apple_text_service.is_available():
        return True
    image = cv2.imread(str(path))

    return image is None or len(apple_text_service.find_words_in_image(image)) < _BEFORE_TALK_WORDS


def _drop_before_talk(kept: list[dict],
                      segments: list[dict],
                      frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames taken before the talk's first segment of substance on which no reader finds
    `_BEFORE_TALK_WORDS` words, and those frames, dropped as `before-talk`: a broadcast's opening animation, the
    conference's logo on a grid (38c3 talks 797, fault-injection and openpv), and a room shot before the lecturer speaks
    (DEFECTS 468). A title card read there stays (the 39c3 closing ceremony's, four words Apple reads, none Tesseract
    does); over the public runs this names those four frames and no other."""

    start = talk_end_utils.find_talk_start(segments)
    if start is None or not text_detection_service.is_available():
        return kept, []
    before = [item for item in kept if float(item.get("timestamp") or 0.0) < start
              and _is_wordless(frames_dir / Path(item["path"]).name)]
    dropped = [knowledge_filter_service.move_to_dropped(frames_dir, item, None, "before-talk") for item in before]
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item not in before],
                                                             {entry["slug"] for entry in dropped})

    return staying, dropped


def _is_empty_screen(path: Path) -> bool:
    """Whether the stored frame shows nothing between its chrome bands (`screen_crop_utils.is_empty_inside`)."""

    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)

    return gray is not None and screen_crop_utils.is_empty_inside(gray)


def _drop_empty_screens(kept: list[dict],
                        frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames with every frame that shows nothing but its window's bars dropped as `empty-screen`, and those
    frames: a cleared terminal (DEFECTS 558), whose speech then goes to the frame before it."""

    empty = [item for item in kept if _is_empty_screen(frames_dir / Path(item["path"]).name)]
    dropped = [knowledge_filter_service.move_to_dropped(frames_dir, item, None, "empty-screen") for item in empty]
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item not in empty],
                                                             {entry["slug"] for entry in dropped})

    return staying, dropped


def _drop_cut_aways(kept: list[dict],
                    frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames with every camera cut-away between two showings of one screen dropped as `cut-away`
    (`knowledge_filter_service.list_cut_aways`, DEFECTS 102), and those frames."""

    cut_aways = knowledge_filter_service.list_cut_aways(kept)
    dropped = [{**knowledge_filter_service.move_to_dropped(frames_dir, item, item.get("dropScore"), "cut-away"),
                "coveredBy": item["coveredBy"]} for item in cut_aways]
    gone = {entry["slug"] for entry in dropped}
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item["slug"] not in gone], gone,
                                                             {entry["slug"]: entry["coveredBy"] for entry in dropped})

    return staying, dropped


def _drop_replacing_step_marks(kept: list[dict],
                            frames_dir: Path) -> tuple[list[dict], list[str]]:
    """The kept frames with every build step that replaced the text of the frame before it made a screen of its own,
    the later steps of its build made steps of it, and those frames' slugs (DEFECTS 326): a quote shown in place of a
    slide's question was marked its step, and its notes said the quote was added below the question."""

    replacing = set(empty_step_service.list_replacing_steps(kept, frames_dir))
    staying: list[dict] = []
    renamed: dict[str, str] = {}
    for item in kept:
        start = renamed.get(str(item.get("stepOf") or ""), item.get("stepOf"))
        if item["slug"] in replacing:
            renamed[str(item["stepOf"])] = item["slug"]
            staying.append({key: value for key, value in item.items() if key != "stepOf"})
        else:
            staying.append({**item, "stepOf": start} if item.get("stepOf") else item)

    return staying, sorted(replacing)


def _drop_empty_steps(kept: list[dict],
                      frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames with every build step that adds nothing to the frame before it dropped as `empty-step`
    (DEFECTS 150), and those frames."""

    empty = empty_step_service.list_empty_steps(kept, frames_dir)
    dropped = [{**knowledge_filter_service.move_to_dropped(frames_dir, item, None, "empty-step"), "coveredBy": item["coveredBy"]}
               for item in empty]
    gone = {entry["slug"] for entry in dropped}
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item["slug"] not in gone], gone,
                                                             {entry["slug"]: entry["coveredBy"] for entry in dropped})

    return staying, dropped


def _drop_passed_build_starts(kept: list[dict],
                              frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames with every build's first frame that was on screen under `frames.passThroughSeconds` and whose
    words its next step shows dropped as `pass-through` (DEFECTS 470), the step starting the build, and those frames."""

    passed = empty_step_service.list_passed_build_starts(kept, frames_dir, config.settings.frames.pass_through_seconds)
    dropped = [{**knowledge_filter_service.move_to_dropped(frames_dir, item, None, "pass-through"), "coveredBy": item["coveredBy"]}
               for item in passed]
    gone = {entry["slug"] for entry in dropped}
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item["slug"] not in gone], gone,
                                                             {entry["slug"]: entry["coveredBy"] for entry in dropped})

    return staying, dropped


def _drop_moved_copies(kept: list[dict],
                       frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames with every frame that is the frame before it moved or scaled, no build step, dropped as
    `moved-copy` (DEFECTS 197), and those frames: a talk's broadcast showed one slide a few pixels off as frames no step
    joined, and the redundancy filter's word check kept them, the text reader missing small words on one of the two."""

    copies = empty_step_service.list_moved_copies(kept, frames_dir)
    dropped = [{**knowledge_filter_service.move_to_dropped(frames_dir, item, None, "moved-copy"), "coveredBy": item["coveredBy"]}
               for item in copies]
    gone = {entry["slug"] for entry in dropped}
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item["slug"] not in gone], gone,
                                                             {entry["slug"]: entry["coveredBy"] for entry in dropped})

    return staying, dropped


def _drop_repeat_copies(kept: list[dict],
                        frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The kept frames with every return that adds nothing to the earlier frame it repeats dropped as `repeat-copy`
    (`frame_redundancy_service.list_repeat_copies`, DEFECTS 603), and those frames."""

    copies = frame_redundancy_service.list_repeat_copies(kept, frames_dir, config.settings.frames)

    return _drop_copies(kept, frames_dir, copies, frame_redundancy_service.REPEAT_COPY_DROPPED_BY)


def _drop_copies(kept: list[dict],
                 frames_dir: Path,
                 copies: list[dict],
                 dropped_by: str) -> tuple[list[dict], list[dict]]:
    """The kept frames with `copies` (each holding its `coveredBy`) dropped as `dropped_by`, a kept frame that repeats
    one of them relinked to its holder, and the dropped entries."""

    dropped = [{**knowledge_filter_service.move_to_dropped(frames_dir, item, None, dropped_by),
                "coveredBy": item["coveredBy"]} for item in copies]
    gone = {entry["slug"] for entry in dropped}
    staying = knowledge_filter_service.build_relinked_frames([item for item in kept if item["slug"] not in gone], gone,
                                                             {entry["slug"]: entry["coveredBy"] for entry in dropped})

    return staying, dropped


def _run_board_stage(ctx: RunContext,
                     kept: list[dict],
                     frames_dir: Path) -> tuple[list[dict], list[dict]]:
    """The board stage: a board frame with nothing new leaves, one with a part changed becomes a step of its board with
    the box of that part, with no model call. The kept frames and the ones it dropped."""

    settings = config.settings.frames
    started = time.perf_counter()
    reader = ""
    if settings.board_handler in board_frame_service.TEXT_HANDLERS:
        board_text_reader_service.set_reader(settings.board_text_reader)
        reader = f", reading text with {board_text_reader_service.describe_reader()}"
    ctx.log.info(f"  board stage: {settings.board_handler}{reader} …")
    kept, boards = board_frame_service.handle_board_frames(kept, frames_dir, settings.board_handler,
                                                           ctx.repository / ctx.module)
    ctx.log.info(f"  board stage: {sum(1 for item in kept if item.get('isBoard'))} board frame(s) kept, "
                 f"{sum(1 for item in kept if item.get('boardChange'))} of them steps with their changed part, "
                 f"{len(boards)} dropped as showing nothing new, {time.perf_counter() - started:.1f} s")

    return kept, boards


def _run_redundancy_filter(ctx: RunContext,
                           kept: list[dict],
                           frames_dir: Path) -> tuple[list[dict], list[dict], dict[str, int]]:
    """The redundancy filter: a small vision model names the frames another kept frame holds whole, and the program
    drops those its checks let stand. The kept frames, the ones it dropped, and each frame's call by its slug, which
    the similarity recheck reads to ask only frames no call showed together."""

    settings = config.settings.frames
    judged = len(kept)
    batches = frame_redundancy_service.map_call_of_frames(kept, settings.redundancy_frames_per_call)
    progress_service.start_stage(ctx.run_dir, progress_service.MODEL, judged)
    spec = frame_redundancy_service.build_judging_spec(
        settings, lambda done: _report_and_hold(ctx, progress_service.MODEL, done, judged),
        ctx.run_dir)
    kept, redundant = frame_redundancy_service.filter_frames(kept, frames_dir, spec, settings.redundancy_frames_per_call,
                                                             ctx.log)
    progress_service.finish_stage(ctx.run_dir, progress_service.MODEL, judged, ctx.log)
    progress_service.record_kept(ctx.run_dir, progress_service.MODEL, len(kept))

    return kept, redundant, batches


def _prepare_pdf_pages(ctx: RunContext,
                       transcript: dict,
                       pdf: Path) -> dict:
    """A PDF's frames: every page rendered, none filtered (a page holds no camera and no person), each given its page's
    text as what is said while it is on screen (a deck's: its speaker notes), and a page repeating an earlier one marked
    so."""

    progress_service.open_progress(ctx.run_dir, [progress_service.RULES])
    progress_service.start_stage(ctx.run_dir, progress_service.RULES, None)
    page_indexes = input_range_service.list_page_indexes(input_range=input_range_service.read_range(
        folder=ctx.paths.output_root), page_count=pdf_source_service.count_pages(pdf))
    frames = pdf_source_service.render_pages(pdf, ctx.paths.output_root / output_contract.FRAME_IMAGES_DIR_NAME,
                                             page_indexes)
    progress_service.finish_stage(ctx.run_dir, progress_service.RULES, len(frames["items"]), ctx.log,
                                  f"{len(frames['items'])} page(s) rendered")
    progress_service.record_kept(ctx.run_dir, progress_service.RULES, len(frames["items"]))
    page_numbers = [item["page"] for item in frames["items"]]
    slices = pdf_source_service.build_page_slices(transcript.get("segments", []), page_numbers)

    # Each item names its medium, which adds the PDF's rules to phase 3's instructions (context_map.json)
    items = [{**item, "medium": output_contract.PDF_MEDIUM, "transcriptBefore": spoken["before"],
              "transcriptCurrent": spoken["current"]} for item, spoken in zip(frames["items"], slices)]

    return {"medium": output_contract.PDF_MEDIUM, "droppedFrames": [],
            "items": pdf_source_service.mark_repeated_pages(
                items, ctx.paths.output_root / output_contract.FRAME_IMAGES_DIR_NAME)}


def prepare_frames(ctx: RunContext) -> dict:
    """`{"items", "droppedFrames"}`: every frame kept of the video at `ctx.repository / ctx.module`, in chronological
    order, as `{"slug", "index", "frameNumber", "timestamp", "path", "score", "transcriptBefore", "transcriptCurrent"}`,
    in `change` mode also `box`, `screenBox`, `repeatOf` and `stepOf`, the classifier's `tag` and `boardScore` while
    a stage that reads them is on, `dropScore` while the knowledge filter is on, the board stage's `isBoard`,
    `isBoardByNeighbour`, `boardChange` and its text fields, `screenWords` on a frame stored shrunk, and `isTextOnly`
    and `textOnlyScore` while the text-only mark is on; and every frame a stage dropped, as `{"slug", "index", "frameNumber", "timestamp", "path", "dropScore",
    "droppedBy"}`, the board stage's, the redundancy filter's and the recheck's also with `coveredBy` and `reason`."""

    # STEP 1: the transcript, from the file phase 1 wrote, and the stages this phase will run, all waiting
    transcript = phase_file_service.read_phase_file(ctx.paths, _TRANSCRIPT_PHASE_NUMBER)
    # What an earlier phase 2 dropped goes first, whichever stages drop this time, a PDF's hand-dropped pages too
    frames_dir = ctx.paths.output_root / output_contract.FRAME_IMAGES_DIR_NAME
    taken_before = _read_rules_result(frames_dir, ctx.run_dir)
    knowledge_filter_service.clear_dropped_frames(frames_dir)
    if pdf_source_service.is_pdf(ctx.repository / ctx.module):
        return _prepare_pdf_pages(ctx, transcript, ctx.repository / ctx.module)
    # a deck's slides are the pages of the PDF phase 1 drew it into
    if slides_source_service.is_slides(ctx.repository / ctx.module):
        return _prepare_pdf_pages(ctx, transcript, slides_source_service.find_slides_pdf(ctx.paths.output_root))
    text_detection_service.set_recording_language(str(transcript.get("language") or ""))
    settings = config.settings.frames
    is_classifier_run = settings.is_knowledge_filter_on or settings.is_text_only_mark_on or settings.is_board_stage_on
    stages = [progress_service.RULES]
    if is_classifier_run:
        stages.append(progress_service.CLASSIFIER)
    if settings.is_redundancy_filter_on:
        stages.append(progress_service.MODEL)
    # The recheck compares the classifier's vectors, which only a classifier stage has it write
    is_rechecked = settings.is_similarity_recheck_on and is_classifier_run
    if settings.is_similarity_recheck_on and not is_classifier_run:
        ctx.log.info("  similarity recheck skipped: it compares the frame classifier's vectors, and no stage that runs "
                     "the classifier is on")
    if is_rechecked:
        stages.append(progress_service.SIMILARITY)
    progress_service.open_progress(ctx.run_dir, stages)

    # STEP 2: the rules choose the frames and write each one as a JPEG under the output folder; an earlier phase 2's
    # images this one did not take go, so no later stage reads them
    progress_service.start_stage(ctx.run_dir, progress_service.RULES, None)
    if taken_before is None:
        frames = _write_rules_result(frames_dir, ctx.run_dir, frame_extraction_service.extract_frames(
            ctx.repository / ctx.module,
            frames_dir,
            settings,
            lambda done, total: _report_and_hold(ctx, progress_service.RULES, done, total),
            input_range_service.read_range(folder=ctx.paths.output_root)))
        how = ""
    else:
        frames, how = taken_before, ", read again: this run took them before it stopped"
    progress_service.finish_stage(ctx.run_dir, progress_service.RULES, len(frames["items"]), ctx.log,
                                  f"{frames['mode']} mode, {frames['video']['durationS']:.1f} s of video at "
                                  f"{frames['video']['fps']:.2f} fps{how}")
    progress_service.record_kept(ctx.run_dir, progress_service.RULES, len(frames["items"]))
    knowledge_filter_service.clear_stale_frames(frames_dir, {Path(item["path"]).name for item in frames["items"]})

    # STEP 3: the frame classifier tags every frame once, people, text or other, when a stage that reads its tags is on
    kept, dropped, tags = frames["items"], [], {}
    vectors_path = ctx.run_dir / _FRAME_VECTORS_FILE
    if is_classifier_run:
        _wait_out_pause(ctx)
        progress_service.start_stage(ctx.run_dir, progress_service.CLASSIFIER, len(frames["items"]))
        # The classifier's SigLIP vectors are kept for the similarity recheck, which then loads no model of its own
        tags = knowledge_filter_service.read_tags(frames_dir, settings, vectors_path if is_rechecked else None)
        # The decision model, when the run chose it, decides which frames hold no knowledge; the classifier's other
        # tags stay
        filter_model = decision_filter_service.read_chosen_model(ctx.paths.output_root)
        if settings.is_knowledge_filter_on and filter_model == decision_filter_service.DECISION_MODEL:
            tags = decision_filter_service.apply_decisions(
                tags, decision_filter_service.read_decisions(frames_dir, settings), settings.decision_filter_cut)
            ctx.log.info(f"  knowledge filter model: {filter_model}, a frame under "
                         f"{settings.decision_filter_cut:.2f} knowledge is dropped")
        progress_service.finish_stage(ctx.run_dir, progress_service.CLASSIFIER, len(frames["items"]), ctx.log)
        # Each frame gets the classifier's tag and board score once, here, for every stage that reads them
        kept = knowledge_filter_service.add_tags(frames["items"], tags)

    # STEP 4: the knowledge filter: the frames tagged people (no knowledge on them) leave the list
    if settings.is_knowledge_filter_on:
        kept, filtered = knowledge_filter_service.filter_frames(kept, frames_dir, tags)
        ctx.log.info(f"  knowledge filter: {len(filtered)} of {len(frames['items'])} frame(s) dropped as holding no "
                     f"knowledge, {len(kept)} kept")
        dropped = _add_dropped(frames_dir, dropped, filtered)
        kept, cut_aways = _drop_cut_aways(kept, frames_dir)
        if cut_aways:
            ctx.log.info(f"  cut-aways: {len(cut_aways)} frame(s) dropped, the speaker between two showings of one screen")
            dropped = _add_dropped(frames_dir, dropped, cut_aways)
    if is_classifier_run:
        progress_service.record_kept(ctx.run_dir, progress_service.CLASSIFIER, len(kept))

    # STEP 4b: the frames after the talk's last words of substance, a broadcast's closing cards, leave the list before
    # any model reads them
    kept, after_talk = _drop_after_talk(kept, transcript.get("segments", []), frames_dir)
    if after_talk:
        ctx.log.info(f"  after the talk: {len(after_talk)} frame(s) dropped, taken after its last words of substance")
        dropped = _add_dropped(frames_dir, dropped, after_talk)
    kept, before_talk = _drop_before_talk(kept, transcript.get("segments", []), frames_dir)
    if before_talk:
        ctx.log.info(f"  before the talk: {len(before_talk)} frame(s) dropped, taken before its first words of "
                     "substance with no text on them")
        dropped = _add_dropped(frames_dir, dropped, before_talk)
    kept, empty_screens = _drop_empty_screens(kept, frames_dir)
    if empty_screens:
        ctx.log.info(f"  empty screens: {len(empty_screens)} frame(s) dropped, nothing on them but the window's bars")
        dropped = _add_dropped(frames_dir, dropped, empty_screens)

    # STEP 4c: the build steps that replaced the text of the frame before are screens of their own; then the build
    # steps that add nothing to the frame before them (a drawn hand moved, the same slide zoomed)
    kept, replacing = _drop_replacing_step_marks(kept, frames_dir)
    if replacing:
        ctx.log.info(f"  replaced text: {len(replacing)} frame(s) no longer build steps, the frame before's text replaced")
    kept, empty_steps = _drop_empty_steps(kept, frames_dir)
    if empty_steps:
        ctx.log.info(f"  empty steps: {len(empty_steps)} frame(s) dropped, build steps adding nothing to the frame before")
        dropped = _add_dropped(frames_dir, dropped, empty_steps)
    kept, passed_starts = _drop_passed_build_starts(kept, frames_dir)
    if passed_starts:
        ctx.log.info(f"  passed build starts: {len(passed_starts)} frame(s) dropped, on screen under "
                     f"{settings.pass_through_seconds:g} s before a step showing all of them")
        dropped = _add_dropped(frames_dir, dropped, passed_starts)
    kept, moved_copies = _drop_moved_copies(kept, frames_dir)
    if moved_copies:
        ctx.log.info(f"  moved copies: {len(moved_copies)} frame(s) dropped, the frame before shown again a few pixels off")
        dropped = _add_dropped(frames_dir, dropped, moved_copies)
    kept, repeat_copies = _drop_repeat_copies(kept, frames_dir)
    if repeat_copies:
        ctx.log.info(f"  repeat copies: {len(repeat_copies)} frame(s) dropped, returns adding nothing to the frame they repeat")
        dropped = _add_dropped(frames_dir, dropped, repeat_copies)

    # STEP 5: the board stage: a board frame with nothing new leaves, one with a part changed becomes a step of its
    # board with the box of that part, with no model call
    if settings.is_board_stage_on:
        _wait_out_pause(ctx)
        kept, boards = _run_board_stage(ctx, kept, frames_dir)
        dropped = _add_dropped(frames_dir, dropped, boards)

    # STEP 6: the redundancy filter: a small vision model names the frames another kept frame holds whole, and the program
    # drops those its checks let stand
    batches = {}
    if settings.is_redundancy_filter_on:
        kept, redundant, batches = _run_redundancy_filter(ctx, kept, frames_dir)
        dropped = _add_dropped(frames_dir, dropped, redundant)

    # STEP 7: the similarity recheck: frames that look alike but sat in different calls of the redundancy filter are
    # asked again; with that filter off, every list of frames that look alike is asked
    if is_rechecked:
        kept, rechecked = _run_similarity_recheck(ctx, kept, batches, frames_dir, vectors_path)
        dropped = _add_dropped(frames_dir, dropped, rechecked)

    # STEP 8: the text-only mark: whether each kept frame holds only text an OCR carries whole
    if settings.is_text_only_mark_on:
        kept = text_only_service.mark_text_only(kept, tags)
        ctx.log.info(f"  text-only mark: {sum(item['isTextOnly'] for item in kept)} of {len(kept)} kept frame(s) hold "
                     f"only text")

    # STEP 8b: a frame stored shrunk gets its words read on the recording at its own size, which phase 3's outline check
    # and completion quote: a small code font shrunk to the stored width is below what the text reader reads
    _wait_out_pause(ctx)
    started = time.perf_counter()
    kept = screen_words_service.add_screen_words(kept, frames_dir, ctx.repository / ctx.module)
    read = sum(1 for item in kept if "screenWords" in item)
    if read:
        ctx.log.info(f"  screen words: {read} of {len(kept)} kept frame(s) stored shrunk, read on the recording at its "
                     f"own size, {time.perf_counter() - started:.1f} s")

    # STEP 9: give every kept frame what was said before it and what is said while it is on screen
    slices = transcript_slice_utils.build_frame_slices(
        transcript.get("segments", []),
        [item["timestamp"] for item in kept],
        frames["video"]["durationS"],
        config.settings.prompt.transcript_before_seconds)
    items = [{**item, "transcriptBefore": spoken["before"], "transcriptCurrent": spoken["current"]}
             for item, spoken in zip(kept, slices)]

    return {"items": items, "droppedFrames": dropped}
