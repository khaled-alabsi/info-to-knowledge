"""Frames that hold only text: phase 2 asks the frame classifier once for every frame's
tag, people, text or other, and marks each kept frame tagged text `isTextOnly` with its `textOnlyScore`; phase 3 then
sends a marked frame as the lines the program reads on it instead of its image.

The classifier is a stand-in command with the real one's interface (`classifier filter DIR --out FILE --task TASK`),
so the program's own subprocess path runs; it writes each task it is asked into `asked.txt`, and tags text the frames
named in `texts.txt` beside it and every other frame other.
"""

from __future__ import annotations

import json
import logging
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

from support import frame_batch_fixtures
from support import harness
from support import media_fixtures

import config
from models.attempt_request import AttemptRequest
from models.run_paths import RunPaths
from prompts import frames_processing_messages
from services import frame_preparation_service
from services import outline_reread_service
from services import phase_file_service
from services import text_only_service
from services import run_paths_service
from validators import item_validator

FAKE_CLASSIFIER = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
folder, out = Path(sys.argv[2]), Path(sys.argv[4])
task = sys.argv[sys.argv.index("--task") + 1]
(Path(__file__).parent / "asked.txt").open("a").write(task + "\\n")
texts = (Path(__file__).parent / "texts.txt").read_text().split()
frames = [{"frame": path.name, "tag": "text" if path.name in texts else "other", "peopleScore": 0.1,
           "isPlainText": path.name in texts, "milliseconds": 1.0} for path in sorted(folder.glob("*.jpg"))]
out.write_text(json.dumps({"task": task, "folder": str(folder), "frames": frames}))
"""


def _build_classifier(folder: Path,
                      texts: list[str]) -> Path:
    command = folder / "classifier"
    command.write_text(FAKE_CLASSIFIER, encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    (folder / "texts.txt").write_text("\n".join(texts), encoding="utf-8")

    return command


def _build_context(video: Path) -> SimpleNamespace:
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=video.parent / "out")
    run_paths_service.ensure_run_paths(paths)
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "hasAudio": True, "language": "de",
                                                   "text": "eins", "segments": [{"text": " eins", "start": 0.0,
                                                                                 "end": 6.0, "words": []}]})
    run_dir = paths.output_root / "logs" / "2026-10-01_09-00"
    run_dir.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(repository=paths.repository, module=paths.module, paths=paths, run_dir=run_dir,
                           log=logging.getLogger("test"), control_check=None)


def _prepare_with(ctx: SimpleNamespace,
                  is_mark_on: bool,
                  command: Path) -> dict:
    frames = config.settings.frames
    previous = (frames.selection_mode, frames.min_scene_seconds, frames.is_knowledge_filter_on,
                frames.knowledge_classifier_command, frames.is_text_only_mark_on)
    frames.selection_mode, frames.min_scene_seconds = "scene", 0.0
    frames.is_knowledge_filter_on, frames.knowledge_classifier_command = True, str(command)
    frames.is_text_only_mark_on = is_mark_on
    try:
        return frame_preparation_service.prepare_frames(ctx)
    finally:
        (frames.selection_mode, frames.min_scene_seconds, frames.is_knowledge_filter_on,
         frames.knowledge_classifier_command, frames.is_text_only_mark_on) = previous


def test_phase_2_marks_each_kept_frame_with_the_text_only_task_s_decision():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    command = _build_classifier(folder, [])
    first = _prepare_with(ctx, True, command)
    names = [Path(item["path"]).name for item in first["items"]]
    harness.check("the scene video gives frames to mark", len(names) >= 2, str(names))
    (folder / "texts.txt").write_text(names[0], encoding="utf-8")
    (folder / "asked.txt").unlink()
    items = _prepare_with(ctx, True, command)["items"]
    asked = (folder / "asked.txt").read_text().split()
    harness.check("the classifier is asked once, for the tags", asked == ["tags"], str(asked))
    harness.check("the frame it calls text-only is marked so, with its score",
                  items[0]["isTextOnly"] is True and items[0]["textOnlyScore"] == 1.0, json.dumps(items[0], default=str))
    harness.check("every other kept frame is marked not text-only",
                  all(item["isTextOnly"] is False for item in items[1:]), str([item.get("isTextOnly") for item in items]))


def test_with_the_mark_off_no_frame_is_marked():
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(media_fixtures.build_three_scene_video(folder))
    command = _build_classifier(folder, [])
    items = _prepare_with(ctx, False, command)["items"]
    harness.check("no frame carries the mark", all("isTextOnly" not in item for item in items))
    harness.check("the classifier is asked once, for the knowledge filter", (folder / "asked.txt").read_text().split() == ["tags"])


def _count_frames(messages) -> int:
    return sum(part.get("type") == "image_url" for message in messages if isinstance(message.content, list)
               for part in message.content if isinstance(part, dict))


def _build_batch_messages(is_sent_as_text: bool,
                          is_second_reader_reading: bool = True):
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [{**ctx.store.get_item("frame-000"), "isTextOnly": True}, ctx.store.get_item("frame-001")]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-001", "items": items, "previousRecords": []})
    settings = config.settings.frames_processing
    previous = (settings.is_text_only_frame_sent_as_text, item_validator.list_screen_text_items,
                outline_reread_service.is_read, frames_processing_messages.apple_text_service.is_available)
    frames_processing_messages.apple_text_service.is_available = lambda: True
    settings.is_text_only_frame_sent_as_text = is_sent_as_text
    # the fixture's frames are stand-in bytes the text reader reads nothing on; the text-only frame gets two lines
    item_validator.list_screen_text_items = lambda paths, slug: (
        [item_validator.ScreenTextItem(kind="line", lines=["Release planning", "Retries stop after three attempts"])]
        if slug == "frame-000" else [])
    # and the second reader, where it runs, read them (`is_second_reader_reading` False: it read nothing)
    outline_reread_service.is_read = lambda path: is_second_reader_reading
    frames_processing_messages._is_read.cache_clear()
    try:
        return frames_processing_messages.build_frame_batch_messages(request)
    finally:
        (settings.is_text_only_frame_sent_as_text, item_validator.list_screen_text_items,
         outline_reread_service.is_read, frames_processing_messages.apple_text_service.is_available) = previous
        frames_processing_messages._is_read.cache_clear()


def test_phase_3_sends_a_text_only_frame_as_its_read_text_instead_of_its_image():
    messages = _build_batch_messages(True)
    harness.check("only the frame that is not text-only goes as an image", _count_frames(messages) == 1)
    first_frame = messages[2].content
    harness.check("the text-only frame's message has no image part",
                  all(part.get("type") != "image_url" for part in first_frame), str(first_frame))
    harness.check("and says the frame is sent as its read text",
                  frames_processing_messages.TEXT_ONLY_FRAME_NOTE.strip()[:60] in first_frame[0]["text"],
                  first_frame[0]["text"][-600:])


def test_a_text_only_frame_whose_second_reader_finds_a_line_more_goes_as_its_image_defect():
    # a build's step was sent as Tesseract's lines alone, which lacked its new third list item, so its notes left the
    # item out; now a frame the second reader finds a missed line on goes as its image, the line named
    previous = outline_reread_service.list_missed_lines
    outline_reread_service.list_missed_lines = lambda path, listed: (["3. Performance issues."]
                                                                     if path.name.startswith("frame-000") else [])
    frames_processing_messages._read_missed_lines.cache_clear()
    try:
        messages = _build_batch_messages(True)
    finally:
        outline_reread_service.list_missed_lines = previous
        frames_processing_messages._read_missed_lines.cache_clear()
    harness.check("both frames go as images", _count_frames(messages) == 2)
    first_frame = " ".join(str(part.get("text", "")) for part in messages[2].content)
    harness.check("the missed line is named", '"3. Performance issues."' in first_frame, first_frame[-400:])
    harness.check("and the frame is not said to be sent as text",
                  frames_processing_messages.TEXT_ONLY_FRAME_NOTE.strip()[:60] not in first_frame)

def test_a_text_only_frame_the_second_reader_read_nothing_on_goes_as_its_image_defect():
    # A slide went as Tesseract's lines, which had lost a quoted word, while Apple's recognizer failed and read nothing;
    # "no line missed" was taken from a reading that never came, and the worker guessed the word. Now it goes as its image
    messages = _build_batch_messages(True, is_second_reader_reading=False)
    harness.check("both frames go as images", _count_frames(messages) == 2)
    harness.check("and no frame is said to be sent as text",
                  frames_processing_messages.TEXT_ONLY_FRAME_NOTE.strip()[:60] not in messages[2].content[0]["text"])


def test_a_text_only_frame_with_no_line_read_goes_as_its_image():
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [{**ctx.store.get_item("frame-001"), "isTextOnly": True}]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-001-001", "items": items, "previousRecords": []})
    settings = config.settings.frames_processing
    previous = settings.is_text_only_frame_sent_as_text
    settings.is_text_only_frame_sent_as_text = True
    try:
        messages = frames_processing_messages.build_frame_batch_messages(request)
    finally:
        settings.is_text_only_frame_sent_as_text = previous
    harness.check("with nothing read, the frame is sent as its image", _count_frames(messages) == 1)


def test_with_the_setting_off_every_frame_goes_as_an_image():
    messages = _build_batch_messages(False)
    harness.check("both frames go as images", _count_frames(messages) == 2)
    harness.check("and no frame is said to be sent as text",
                  frames_processing_messages.TEXT_ONLY_FRAME_NOTE.strip()[:60] not in messages[2].content[0]["text"])


def test_a_follow_up_gets_a_text_only_frame_s_read_lines_in_place_of_its_image():
    ctx = frame_batch_fixtures.load_run_context(count=3)
    items = [{**ctx.store.get_item("frame-000"), "isTextOnly": True}, ctx.store.get_item("frame-001")]
    batch = {"frames": [0, 1], "start": 0.0, "end": 2.0, "accepted": [], "spoken": [], "items": items}
    request = AttemptRequest(job_kind="critique", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore, inputs={"slug": "frames-000-001", "batch": batch})
    settings = config.settings.frames_processing
    previous = settings.is_text_only_frame_sent_as_text, item_validator.list_screen_text_items, outline_reread_service.is_read
    settings.is_text_only_frame_sent_as_text = True
    item_validator.list_screen_text_items = lambda paths, slug: (
        [item_validator.ScreenTextItem(kind="line", lines=["Release planning"])] if slug == "frame-000" else [])
    # the fixture's stand-in frames hold nothing a reader reads; the second reader is taken to have read them
    outline_reread_service.is_read = lambda path: True
    frames_processing_messages._is_read.cache_clear()
    try:
        messages = frames_processing_messages.build_critique_messages(request)
    finally:
        settings.is_text_only_frame_sent_as_text, item_validator.list_screen_text_items, outline_reread_service.is_read = previous
        frames_processing_messages._is_read.cache_clear()
    harness.check("the frame that is not text-only goes as an image, the other does not", _count_frames(messages) == 1)
    first_frame = " ".join(str(part.get("text", "")) for part in messages[2].content)
    harness.check("the text-only frame carries its read lines", '"Release planning"' in first_frame, first_frame)


def test_each_frame_takes_its_classifier_tag_and_a_missing_tag_stops_phase_2():
    items = [{"path": "frames/frame-000.jpg"}, {"path": "frames/frame-001.jpg"}]
    tags = {"frame-000.jpg": {"tag": "text", "isPlainText": True}, "frame-001.jpg": {"tag": "other", "isPlainText": False}}
    marked = text_only_service.mark_text_only(items, tags)
    harness.check("a frame tagged text is text-only with score 1, another is not",
                  [(item["isTextOnly"], item["textOnlyScore"]) for item in marked] == [(True, 1.0), (False, 0.0)], str(marked))
    try:
        text_only_service.mark_text_only(items, {"frame-000.jpg": tags["frame-000.jpg"]})
        outcome = "marked"
    except RuntimeError as error:
        outcome = str(error)
    harness.check("a frame with no tag stops phase 2 by name", "frame-001.jpg" in outcome, outcome)


def test_a_frame_the_board_stage_made_a_board_keeps_its_board_tag():
    """The board stage tags a frame just under the classifier's board bar beside a board frame board (DEFECTS 126); the
    text-only mark wrote the classifier's tag back over it, so the run's record said other for a frame handled as a
    board."""

    items = [{"path": "frames/frame-000.jpg", "tag": "board", "isBoardByNeighbour": True}]
    marked = text_only_service.mark_text_only(items, {"frame-000.jpg": {"tag": "text", "isPlainText": True}})
    harness.check("the frame keeps its board tag and is no text-only frame",
                  marked[0]["tag"] == "board" and marked[0]["isTextOnly"] is False, str(marked))
