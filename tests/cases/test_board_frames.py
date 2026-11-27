"""Phase 2's board stage: the frames tagged `board` are compared with the board frame kept before
them, with no model call. A board frame showing nothing new is dropped, one with a part changed becomes a step of its
board carrying the box of that part, and a frame of another kind breaks the run.

The frames are drawn here: a board of columns of cards on the left, a ticket panel on the right.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import cv2
import numpy

from support import frame_batch_fixtures
from support import harness

import config
from models.attempt_request import AttemptRequest
from prompts import frames_processing_messages
from services import phase_file_service
from services import board_frame_service

FRAME_SIZE = (1280, 720)                    # width, height
PANEL_LEFT = 900                            # the ticket panel runs from here to the right edge


def _draw_board(panel_lines: int,
                is_other: bool = False) -> numpy.ndarray:
    """A board: four columns of cards on the left, and a ticket panel with `panel_lines` lines of text on the right."""

    width, height = FRAME_SIZE
    frame = numpy.full((height, width, 3), 245, numpy.uint8)
    if is_other:
        frame[100:600, 100:1100] = 30

        return frame
    for column in range(4):
        for card in range(5):
            x0, y0 = 40 + column * 210, 80 + card * 120
            frame[y0:y0 + 90, x0:x0 + 180] = 210
            frame[y0 + 20:y0 + 30, x0 + 15:x0 + 150] = 60
    frame[:, PANEL_LEFT:PANEL_LEFT + 3] = 150
    for line in range(panel_lines):
        frame[60 + line * 40:70 + line * 40, PANEL_LEFT + 30:width - 40] = 50

    return frame


def _write_frames(frames: list[tuple[str, numpy.ndarray, str]]) -> tuple[Path, list[dict]]:
    """Each frame written as `<slug>.jpg` in a new frames folder, and its item with the tag given."""

    frames_dir = Path(tempfile.mkdtemp()) / "frames"
    frames_dir.mkdir()
    items = []
    for index, (slug, image, tag) in enumerate(frames):
        cv2.imwrite(str(frames_dir / f"{slug}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        items.append({"slug": slug, "index": index, "frameNumber": index * 30, "timestamp": float(index),
                      "path": f"frames/{slug}.jpg", "tag": tag})

    return frames_dir, items


def test_a_board_frame_showing_nothing_new_is_dropped_and_one_with_its_panel_changed_is_a_step():
    frames_dir, items = _write_frames([("frame-000", _draw_board(4), "board"),
                                       ("frame-001", _draw_board(4), "board"),
                                       ("frame-002", _draw_board(12), "board"),
                                       ("frame-003", _draw_board(12, is_other=True), "other"),
                                       ("frame-004", _draw_board(12), "board")])
    kept, dropped = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    by_slug = {item["slug"]: item for item in kept}
    harness.check("the unchanged board frame is dropped as covered by the one before",
                  [(entry["slug"], entry["droppedBy"], entry["coveredBy"]) for entry in dropped]
                  == [("frame-001", "board", ["frame-000"])], str(dropped))
    harness.check("its image moved into frames/dropped/",
                  (frames_dir / "dropped" / "frame-001.jpg").is_file() and not (frames_dir / "frame-001.jpg").exists())
    step = by_slug.get("frame-002", {})
    harness.check("the board with its panel changed is a step of the board's first frame",
                  step.get("stepOf") == "frame-000" and step.get("isBoard") is True, str(step))
    box = step.get("boardChange")
    harness.check("its change box holds the panel and leaves the board's columns out",
                  box is not None and box[0] >= PANEL_LEFT - 2 * FRAME_SIZE[0] // 32 and box[2] == FRAME_SIZE[0],
                  str(box))
    harness.check("a frame of another kind breaks the run: the board after it starts a new one",
                  "stepOf" not in by_slug.get("frame-004", {}) and by_slug.get("frame-004", {}).get("isBoard") is True
                  and "isBoard" not in by_slug.get("frame-003", {}), str(by_slug.get("frame-004")))


def test_a_card_whose_text_changed_in_small_spots_is_inside_the_change_box():
    """A column's cards moved by one place keep their layout, so only a few letters differ, each spot under 2% of its
    block: the 2% floor of the drop decision left a real board's first column out of the box (round 3 audit)."""

    board = _draw_board(4)
    changed = _draw_board(12)
    x0, y0 = 40, 320
    for spot in range(3):
        changed[y0 + 22:y0 + 26, x0 + 55 + spot * 40:x0 + 60 + spot * 40] = 245
    frames_dir, items = _write_frames([("frame-000", board, "board"), ("frame-001", changed, "board")])
    kept, _ = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    box = kept[1].get("boardChange")
    harness.check("the box reaches the changed card in the first column as well as the panel",
                  box is not None and box[0] <= x0 + 55 and box[2] == FRAME_SIZE[0], str(box))


def test_a_short_field_changed_beside_the_main_change_is_inside_the_change_box():
    """A ticket panel's date changed in 2 blocks while a card on the left changed in many: patches under 3 blocks were
    left out as the pointer, and the date fell outside the box (round 3 audit). Only the window's top and bottom
    bands (address bar, clock) are left out now."""

    board = _draw_board(4)
    changed = board.copy()
    changed[320:410, 40:220] = 120
    changed[100:110, PANEL_LEFT + 60:PANEL_LEFT + 110] = 245
    frames_dir, items = _write_frames([("frame-000", board, "board"), ("frame-001", changed, "board")])
    kept, _ = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    box = kept[1].get("boardChange")
    harness.check("the box reaches the panel's changed date as well as the card", box is not None
                  and box[0] <= 40 and box[2] >= PANEL_LEFT + 110 and box[1] <= 100, str(box))


def test_a_change_in_the_window_s_top_or_bottom_band_alone_is_left_out_of_the_box():
    board = _draw_board(4)
    changed = board.copy()
    changed[320:410, 40:220] = 120
    changed[FRAME_SIZE[1] - 30:FRAME_SIZE[1] - 10, FRAME_SIZE[0] - 120:FRAME_SIZE[0] - 20] = 0
    frames_dir, items = _write_frames([("frame-000", board, "board"), ("frame-001", changed, "board")])
    kept, _ = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    box = kept[1].get("boardChange")
    harness.check("a clock changed in the bottom band does not widen the box", box is not None
                  and box[2] <= 300 and box[3] <= 480, str(box))


def test_a_board_frame_changed_in_most_of_its_blocks_is_another_board_kept_whole():
    frames_dir, items = _write_frames([("frame-000", _draw_board(4), "board"),
                                       ("frame-001", 255 - _draw_board(4), "board")])
    kept, dropped = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    harness.check("nothing dropped, and the second board is no step and carries no change box",
                  not dropped and "stepOf" not in kept[1] and "boardChange" not in kept[1] and kept[1]["isBoard"],
                  str(kept[1]))


def test_every_step_of_a_board_points_at_the_board_s_first_frame():
    """Each step is compared with the step before it, and named a step of the run's first frame."""

    frames_dir, items = _write_frames([("frame-000", _draw_board(4), "board"),
                                       ("frame-001", _draw_board(8), "board"),
                                       ("frame-002", _draw_board(12), "board")])
    kept, _ = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    harness.check("both later boards are steps of the first",
                  [item.get("stepOf") for item in kept] == [None, "frame-000", "frame-000"],
                  str([item.get("stepOf") for item in kept]))


def _read_sent_images(messages: list) -> list[numpy.ndarray]:
    """Every image the messages carry, decoded."""

    images = []
    for message in messages:
        for part in message.content if isinstance(message.content, list) else []:
            if part.get("type") == "image_url":
                encoded = part["image_url"]["url"].split(",", 1)[1]
                images.append(cv2.imdecode(numpy.frombuffer(base64.b64decode(encoded), numpy.uint8), cv2.IMREAD_COLOR))

    return images


def test_phase_3_sends_a_board_step_as_its_changed_part_enlarged_and_says_so():
    ctx = frame_batch_fixtures.load_run_context(count=2)
    for index, lines in ((0, 4), (1, 12)):
        cv2.imwrite(str(ctx.paths.output_root / "frames" / f"frame-{index:03d}.jpg"), _draw_board(lines))
    panel = [PANEL_LEFT - 40, 0, FRAME_SIZE[0], FRAME_SIZE[1]]
    items = [{**ctx.store.get_item("frame-000"), "isBoard": True},
             {**ctx.store.get_item("frame-001"), "isBoard": True, "stepOf": "frame-000", "boardChange": panel}]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-001", "items": items, "previousRecords": []})
    previous_handler = config.settings.frames.board_handler
    config.settings.frames.board_handler = "image-diff-only"
    try:
        messages = frames_processing_messages.build_frame_batch_messages(request)
    finally:
        config.settings.frames.board_handler = previous_handler
    images = _read_sent_images(messages)
    width = config.settings.frames_processing.min_sent_frame_width
    harness.check("the board goes whole, and its step as the panel alone enlarged to the sent width",
                  len(images) == 2 and images[0].shape[1] == FRAME_SIZE[0] and images[1].shape[1] == width
                  and abs(images[1].shape[0] - round(FRAME_SIZE[1] * width / (panel[2] - panel[0]))) <= 1,
                  str([image.shape for image in images]))
    texts = [part["text"] for message in messages if isinstance(message.content, list)
             for part in message.content if part.get("type") == "text"]
    harness.check("only the step's message says it shows the changed part alone",
                  sum(frames_processing_messages.BOARD_REGION_NOTE.strip()[:50] in text for text in texts) == 1)


def test_with_text_diff_integration_a_board_step_goes_whole_with_the_changes_read():
    ctx = frame_batch_fixtures.load_run_context(count=2)
    for index, lines in ((0, 4), (1, 12)):
        cv2.imwrite(str(ctx.paths.output_root / "frames" / f"frame-{index:03d}.jpg"), _draw_board(lines))
    diff = {"against": "frame-000", "scrolled": 0,
            "changes": [{"kind": "changed", "text": "Status Done", "old": "Status Open"},
                        {"kind": "added", "text": "Gateway timeout on refunds", "old": ""}]}
    items = [{**ctx.store.get_item("frame-000"), "isBoard": True},
             {**ctx.store.get_item("frame-001"), "isBoard": True, "stepOf": "frame-000",
              "boardChange": [PANEL_LEFT - 40, 0, FRAME_SIZE[0], FRAME_SIZE[1]], "boardTextDiff": diff}]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-001", "items": items, "previousRecords": []})
    previous_handler = config.settings.frames.board_handler
    config.settings.frames.board_handler = "text-diff-integration"
    try:
        messages = frames_processing_messages.build_frame_batch_messages(request)
    finally:
        config.settings.frames.board_handler = previous_handler
    images = _read_sent_images(messages)
    harness.check("both frames go whole", [image.shape[:2] for image in images] == [(FRAME_SIZE[1], FRAME_SIZE[0])] * 2,
                  str([image.shape for image in images]))
    texts = " ".join(part["text"] for message in messages if isinstance(message.content, list)
                     for part in message.content if part.get("type") == "text")
    harness.check("the step's message lists the changes read against the frame before",
                  "since frame 0" in texts and '- changed: "Status Open" → "Status Done"' in texts
                  and '- added: "Gateway timeout on refunds"' in texts, texts[-600:])
    harness.check("and never says only the changed part is shown",
                  frames_processing_messages.BOARD_REGION_NOTE.strip()[:50] not in texts)


def test_with_text_only_integration_a_board_goes_as_the_words_its_reader_read():
    """The owner: a board frame sent as its text, never its image (text-only-integration); its words are the board
    reader's, and the outline check reads the same words."""

    ctx = frame_batch_fixtures.load_run_context(count=1)
    cv2.imwrite(str(ctx.paths.output_root / "frames" / "frame-000.jpg"), _draw_board(4))
    middle = FRAME_SIZE[1] // 2
    words = [{"text": text, "box": [100 + 90 * place, middle + 30 * row, 180 + 90 * place, middle + 30 * row + 20],
              "line": row}
             for row, line in enumerate(["Gateway timeout on refunds", "Status Open"])
             for place, text in enumerate(line.split())]
    items = [{**ctx.store.get_item("frame-000"), "isBoard": True, "boardWords": words, "isBoardTextOnly": True}]
    # The outline check and the message read the frame's item from phase 2's file, as a run writes it there
    frames_file = phase_file_service.read_phase_file(ctx.paths, 2)
    phase_file_service.write_phase_file(ctx.paths, 2, {**frames_file, "items": [
        {**item, "boardWords": words, "isBoardTextOnly": True} if item["slug"] == "frame-000" else item
        for item in frames_file["items"]]})
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-000", "items": items, "previousRecords": []})
    previous_handler = config.settings.frames.board_handler
    config.settings.frames.board_handler = "text-only-integration"
    try:
        messages = frames_processing_messages.build_frame_batch_messages(request)
    finally:
        config.settings.frames.board_handler = previous_handler
    texts = " ".join(part["text"] for message in messages if isinstance(message.content, list)
                     for part in message.content if part.get("type") == "text")
    harness.check("no image is sent", _read_sent_images(messages) == [], str(len(_read_sent_images(messages))))
    harness.check("the message lists the board reader's lines and says it holds only text",
                  '"Gateway timeout on refunds"' in texts and '"Status Open"' in texts
                  and frames_processing_messages.TEXT_ONLY_FRAME_NOTE.strip()[:40] in texts, texts[-500:])


def test_phase_3_sends_a_board_step_whose_change_covers_most_of_the_frame_whole():
    ctx = frame_batch_fixtures.load_run_context(count=2)
    cv2.imwrite(str(ctx.paths.output_root / "frames" / "frame-001.jpg"), _draw_board(12))
    items = [{**ctx.store.get_item("frame-001"), "isBoard": True, "stepOf": "frame-000",
              "boardChange": [0, 0, FRAME_SIZE[0], FRAME_SIZE[1] - 100]}]
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-001-001", "items": items, "previousRecords": []})
    images = _read_sent_images(frames_processing_messages.build_frame_batch_messages(request))
    harness.check("the frame goes whole", len(images) == 1 and images[0].shape[:2] == (FRAME_SIZE[1], FRAME_SIZE[0]),
                  str([image.shape for image in images]))


def test_a_frame_just_under_the_board_bar_next_to_a_board_is_a_board():
    """11 of 199 real board frames scored 0.61 to 0.83 against the bar 0.84, each next to a frame tagged board (a ticket
    page, a board beside a call's settings panel). One scoring at least 0.6 beside a board frame, chained, is a board;
    one lower, or with no board beside it, keeps its tag."""

    frames_dir, items = _write_frames([("frame-000", _draw_board(2), "other"), ("frame-001", _draw_board(4), "other"),
                                       ("frame-002", _draw_board(6), "board"), ("frame-003", _draw_board(8), "other"),
                                       ("frame-004", _draw_board(12, is_other=True), "other"),
                                       ("frame-005", _draw_board(12, is_other=True), "other")])
    for item, score in zip(items, (0.65, 0.7, 0.95, 0.5, 0.75, 0.9)):
        item["boardScore"] = score
    kept, _ = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    by_slug = {item["slug"]: item for item in kept}
    harness.check("the frames at 0.7 and 0.65 before the board, chained, are boards",
                  by_slug["frame-001"].get("isBoard") and by_slug["frame-000"].get("isBoard")
                  and by_slug["frame-000"]["tag"] == "board", str(kept[:2]))
    harness.check("the frame at 0.5 beside the board keeps its tag, and so does the run after it with no board beside",
                  by_slug["frame-003"]["tag"] == "other" and by_slug["frame-004"]["tag"] == "other"
                  and by_slug["frame-005"]["tag"] == "other", str(kept[3:]))
    harness.check("a frame made a board says so", by_slug["frame-001"].get("isBoardByNeighbour") is True
                  and "isBoardByNeighbour" not in by_slug["frame-002"], str(kept[1:3]))


def test_with_text_only_integration_a_ticket_keeps_its_image_and_a_board_goes_as_text():
    """A board's columns and a list's rows are read right; a ticket's panel tables and linked rows are
    not, so a frame showing a ticket's labelled fields keeps its image."""

    ticket = [{"text": text, "box": [0, 0, 1, 1], "line": 0}
              for text in ("Status:", "Assignee:", "Priority:", "Labels:", "Reporter:", "Open")]
    board = [{"text": text, "box": [0, 0, 1, 1], "line": 0} for text in ("To", "do", "Gateway", "timeout", "ASP-412")]
    original = board_frame_service._read_whole_board
    try:
        board_frame_service._read_whole_board = lambda item, frames_dir, video_path: ticket
        shown_ticket = board_frame_service._add_board_text({"slug": "frame-001"}, Path("."), None)
        board_frame_service._read_whole_board = lambda item, frames_dir, video_path: board
        shown_board = board_frame_service._add_board_text({"slug": "frame-002"}, Path("."), None)
    finally:
        board_frame_service._read_whole_board = original
    harness.check("a ticket keeps its image", shown_ticket["isBoardTextOnly"] is False and shown_ticket["boardWords"])
    harness.check("a board goes as its text", shown_board["isBoardTextOnly"] is True)


def test_a_board_whose_only_change_is_the_window_s_top_bar_is_nothing_new_under_the_text_handler():
    """The step's box leaves out a change lying wholly in the window's top or bottom rows (an address bar, a tab bar),
    so a board that changed only there had no box, and the text handler stopped phase 2 unpacking it."""

    changed = _draw_board(4)
    changed[0:60, :] = 120
    frames_dir, items = _write_frames([("frame-000", _draw_board(4), "board"), ("frame-001", changed, "board")])
    kept, dropped = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.TEXT_DIFF_HANDLER,
                                                            None)
    harness.check("the frame is dropped as showing nothing new, naming the bars",
                  [entry["slug"] for entry in dropped] == ["frame-001"] and "bars" in dropped[0]["reason"]
                  and [item["slug"] for item in kept] == ["frame-000"], f"{kept} {dropped}")


def test_with_the_knowledge_filter_off_the_board_stage_still_reads_each_frame_s_board_score():
    """Only the knowledge filter copied the classifier's `boardScore` onto the items: with it off, every frame read 0,
    and a frame just under the board bar beside a board was never made a board."""

    import logging
    from types import SimpleNamespace
    from services import frame_preparation_service
    from services import knowledge_filter_service

    frames_dir, items = _write_frames([("frame-000", _draw_board(4), "board"), ("frame-001", _draw_board(6), "other")])
    for item in items:
        del item["tag"]
    tags = {"frame-000.jpg": {"frame": "frame-000.jpg", "tag": "board", "peopleScore": 0.0, "boardScore": 0.95},
            "frame-001.jpg": {"frame": "frame-001.jpg", "tag": "other", "peopleScore": 0.0, "boardScore": 0.7}}
    ctx = SimpleNamespace(log=logging.getLogger("test"), repository=frames_dir.parent, module="none.mp4")
    previous = config.settings.frames.board_handler
    config.settings.frames.board_handler = board_frame_service.IMAGE_DIFF_HANDLER
    try:
        kept, _ = frame_preparation_service._run_board_stage(ctx, knowledge_filter_service.add_tags(items, tags),
                                                             frames_dir)
    finally:
        config.settings.frames.board_handler = previous
    harness.check("the frame scored 0.7 beside the board is a board", kept[1].get("isBoardByNeighbour") is True, str(kept))


def test_a_frame_of_an_editor_split_in_two_panes_names_each_pane_s_file_defect():
    # An editor split one pane above the other was outlined as one pane, the other pane's file named as the open one.
    # The frame's message now names each pane with the file on its own status line
    from services import editor_pane_service

    ctx = frame_batch_fixtures.load_run_context(count=1)
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-000", "items": [ctx.store.get_item("frame-000")],
                                     "previousRecords": []})
    found = [{"row": 320, "status": "acmesync.py 12% 4:22", "file": "acmesync.py"},
             {"row": 640, "status": "NORMAL unix | utf-8 | python 11:23", "file": ""}]
    previous = editor_pane_service.list_split_panes
    editor_pane_service.list_split_panes = lambda path: found
    try:
        texts = " ".join(part["text"] for message in frames_processing_messages.build_frame_batch_messages(request)
                         if isinstance(message.content, list) for part in message.content if part.get("type") == "text")
    finally:
        editor_pane_service.list_split_panes = previous
    harness.check("the message names the split and each pane's file",
                  "split into 2 panes" in texts and '- the first pane from the top: "acmesync.py"' in texts
                  and "- the second pane from the top: its file name is not read" in texts, texts[-700:])


def test_a_value_said_over_a_frame_and_shown_only_on_the_next_one_is_named_as_announced_defect():
    # A demo's speech ran ahead of its screen: the notes gave a frame the value the presenter announced for the next
    # one. The frame's message now names such words as said, never as shown on this frame
    from services import text_detection_service

    ctx = frame_batch_fixtures.load_run_context(count=2)
    item = {**ctx.store.get_item("frame-000"), "transcriptCurrent": "the report says utils has 18 statements"}
    request = AttemptRequest(job_kind="frame_batch", attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore,
                             inputs={"slug": "frames-000-000", "items": [item], "previousRecords": []})
    screens = {"frame-000.jpg": ["def", "fizzbuzz", "utils.py"],
               "frame-001.jpg": ["Coverage", "report", "statements", "utils.py", "18"]}
    previous = text_detection_service.find_words, text_detection_service.is_available
    text_detection_service.find_words = lambda path, *args, **kwargs: [
        {"text": text, "box": [0, 0, 10, 10], "block": 1, "line": 1} for text in screens.get(Path(path).name, [])]
    text_detection_service.is_available = lambda: True
    try:
        note = frames_processing_messages.render_announced_words_note(request, item)
        last = frames_processing_messages.render_announced_words_note(
            request, {**ctx.store.get_item("frame-001"), "transcriptCurrent": "the report says 18 statements"})
    finally:
        text_detection_service.find_words, text_detection_service.is_available = previous
    harness.check("the words only the next screen shows are named as announced",
                  '"18", "report", "statements"' in note and "never as shown on this frame" in note, note)
    harness.check("the last frame has no next screen and no note", last == "", last)


def test_a_word_struck_through_on_the_screen_is_found_by_its_line_defect():
    # A slide struck three words out and the outline struck only two: the text readers return a struck word as any
    # other (DEFECTS 627). A word with a line drawn through its letters is now found and named in the frame's message
    from services import struck_word_service

    gray = numpy.full((200, 600), 20, dtype=numpy.uint8)
    cv2.putText(gray, "gpujs", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 230, 2)
    cv2.line(gray, (18, 40), (112, 40), 230, 2)                    # struck through the middle of its letters
    cv2.putText(gray, "WebGL", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 230, 2)
    cv2.putText(gray, "link", (300, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 230, 2)
    cv2.line(gray, (298, 56), (366, 56), 230, 2)                   # underlined, at the letters' foot
    cv2.rectangle(gray, (300, 85), (420, 120), 200, -1)            # a filled badge
    cv2.putText(gray, "pass", (310, 112), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 20, 2)
    harness.check("the struck word is found", struck_word_service.is_struck(gray, [16, 26, 116, 58]))
    harness.check("a plain word is not", not struck_word_service.is_struck(gray, [16, 86, 120, 116]))
    harness.check("an underlined word is not", not struck_word_service.is_struck(gray, [296, 26, 370, 60]))
    harness.check("a word on a filled badge is not", not struck_word_service.is_struck(gray, [302, 88, 418, 118]))


def test_a_question_another_speaker_asks_over_a_frame_is_named_in_its_message_defect():
    # A question round said over a talk's closing slide left the frame's questions asked empty (DEFECTS 538). The
    # frame's message now names each question someone other than the main speaker asks; a filler "Yes?" is not named
    speech = ("Speaker B: So the year is only there to say who holds the rights, nothing legal. "
              "Speaker I: Yes? So you were talking about the docs in Markdown. Is there some other way to do it? "
              "Speaker B: Sure, there are docstrings and javadocs, but Markdown is rendered directly, so I recommend it.")
    note = frames_processing_messages.render_asked_questions_note({"transcriptCurrent": speech})
    harness.check("the attendee's question is named, the filler is not",
                  '"Is there some other way to do it?"' in note and '"Yes?"' not in note, note)
    harness.check("a frame where only the main speaker talks gets no note",
                  frames_processing_messages.render_asked_questions_note(
                      {"transcriptCurrent": "Speaker B: Why would you do that? Because it is simple."}) == "", "")


def test_a_board_frame_marked_a_repeat_carries_what_its_text_changed_against_the_earlier_one_defect():
    # Another ticket in the same panel, a few fields different, passed as a repeat and its notes said "same as" the
    # earlier ticket. A board frame marked a repeat is now compared with that frame, and the changes go in its message
    first, other = _draw_board(5), _draw_board(5, is_other=True)
    twin = first.copy()
    twin[100:110, PANEL_LEFT + 30:FRAME_SIZE[0] - 40] = 245           # the panel's second line ends shorter
    twin[100:110, PANEL_LEFT + 30:PANEL_LEFT + 120] = 50
    frames_dir, items = _write_frames([("frame-000", first, "board"), ("frame-001", other, "board"),
                                       ("frame-002", twin, "board"), ("frame-003", other.copy(), "board"),
                                       ("frame-004", first.copy(), "board")])
    items[2]["repeatOf"], items[4]["repeatOf"] = "frame-000", "frame-000"
    compared = []

    def compare(item, earlier, *_):
        compared.append((item["slug"], earlier["slug"], item["boardChange"]))
        return {"against": earlier["slug"], "scrolled": 0, "changes": [{"kind": "changed", "text": "Status: Done", "old": "Status: Open"}]}

    previous = board_frame_service._compare_board_text
    board_frame_service._compare_board_text = compare
    try:
        kept, _ = board_frame_service.handle_board_frames(items, frames_dir, "text-diff-integration", None)
    finally:
        board_frame_service._compare_board_text = previous
    by_slug = {item["slug"]: item for item in kept}
    harness.check("the twin is compared with the frame it repeats, inside the panel's changed line",
                  [entry[:2] for entry in compared if entry[0] == "frame-002"] == [("frame-002", "frame-000")]
                  and (by_slug["frame-002"].get("boardTextDiff") or {}).get("against") == "frame-000"
                  and compared[[entry[0] for entry in compared].index("frame-002")][2][0] >= PANEL_LEFT - 40, str(compared))
    harness.check("a true repeat, its pixels unchanged, gets no comparison",
                  not by_slug["frame-004"].get("boardTextDiff") and "frame-004" not in [entry[0] for entry in compared],
                  str(compared))


def test_a_board_scored_just_under_0_6_beside_a_board_is_a_board_defect():
    # Retrained, the classifier scored a real board 0.5965 beside board frames, under the bar of 0.6, and the frame was
    # handled as other; the bar is 0.55 now, over every non-board beside a board (0.47 at most)
    frames_dir, items = _write_frames([("frame-000", _draw_board(2), "board"), ("frame-001", _draw_board(4), "other"),
                                       ("frame-002", _draw_board(12, is_other=True), "other")])
    for item, score in zip(items, (0.9, 0.5965, 0.47)):
        item["boardScore"] = score
    kept, _ = board_frame_service.handle_board_frames(items, frames_dir, board_frame_service.IMAGE_DIFF_HANDLER, None)
    by_slug = {item["slug"]: item for item in kept}
    harness.check("the frame at 0.5965 beside the board is a board", by_slug["frame-001"].get("isBoard") is True,
                  str(kept[1]))
    harness.check("the frame at 0.47 keeps its tag", by_slug["frame-002"]["tag"] == "other", str(kept[2]))
