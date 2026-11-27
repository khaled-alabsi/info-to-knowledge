"""The video workflow's profiles: quality, the workflow phases 3 to 6 always ran, and
fast-processing: each frame described from its image alone, a few frames to a session, no check; one call writes the
document; phases 5 and 6 off.

The models are fakes with the real ones' interface (`bind_tools`, `bind`, `invoke(messages)` -> an `AIMessage`), set
through each service's `set_model_factory`; they record every call.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from support import harness

import config
from cli import run_command
from graph.nodes import collect_critiques_node
from graph.nodes import process_frames_node
from graph.nodes import review_document_node
from graph.nodes import write_document_node
from models import output_contract
from models import video_workflow_vocabulary
from models.run_identity import RunIdentity
from models.run_job import RunJobRequest
from models.run_paths import RunPaths
from services import fast_document_service
from services import fast_frame_note_service
from services import job_queue_service
from services import run_paths_service
from services import run_status_service
from services import run_store_service
from services import video_workflow_service
from services import worker_live_service
from services import worker_message_service

_RUN_ID = "2026-10-01_09-00"


class _FakeModel:
    """Answers each call with `submit_note` for the frame it was last given (or with `text`, for a document), and keeps
    every call's messages and every bound argument."""

    def __init__(self, text: str = "", is_calling_tool: bool = True, cut_frame: str = ""):
        self.text = text
        self.cut_frame = cut_frame
        self.is_calling_tool = is_calling_tool
        self.calls: list[list] = []
        self.tools: list = []
        self.bound: dict = {}

    def bind_tools(self, tools: list) -> "_FakeModel":
        self.tools = list(tools)

        return self

    def bind(self, **kwargs) -> "_FakeModel":
        self.bound.update(kwargs)

        return self

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(list(messages))
        if self.text:
            return AIMessage(content=self.text)
        frame = messages[-1].content[0]["text"].split(" ")[0] + " " + messages[-1].content[0]["text"].split(" ")[1]
        if frame == self.cut_frame:
            return AIMessage(content="the same line " * 50, response_metadata={"finish_reason": "length"})
        if not self.is_calling_tool:
            return AIMessage(content=f"plain notes of {frame}")

        return AIMessage(content="", tool_calls=[{"name": "submit_note", "args": {"description": f"- notes of {frame}"},
                                                  "id": f"call-{len(self.calls)}"}],
                         additional_kwargs={"reasoning_content": f"looking at {frame}"})


def _make_run(frame_count: int) -> SimpleNamespace:
    """A fast-processing run folder after phase 2: `frame_count` frames on disk, in `02_frames.json` and in the store."""

    root = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=root / "inbox", module="Gateway-demo.mp4", output_root_override=root / "Gateway-demo")
    run_paths_service.ensure_run_paths(paths)
    (paths.output_root / "frames").mkdir(parents=True, exist_ok=True)
    items = []
    for number in range(frame_count):
        slug = f"frame-{number:03d}"
        cv2.imwrite(str(paths.output_root / "frames" / f"{slug}.jpg"), numpy.full((90, 160, 3), 40 * number % 255, numpy.uint8))
        items.append({"slug": slug, "index": number, "path": f"frames/{slug}.jpg", "timestamp": 10.0 * number,
                      "transcriptCurrent": f"Speaker A: the gateway step {number}"})
    (paths.output_root / output_contract.LOOP_ITEMS_FILE).write_text(json.dumps({"schemaVersion": 1, "items": items}),
                                                                       encoding="utf-8")
    store = run_store_service.RunStore.create(paths.run_state(_RUN_ID), RunIdentity.for_run(_RUN_ID, paths),
                                              budgets={}, models=[])
    store.seed_items(items)
    video_workflow_service.write_chosen_workflow(paths.output_root, video_workflow_vocabulary.FAST_PROCESSING, "user")
    lines: list[str] = []
    log = logging.getLogger(f"fast-{root.name}")
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    return SimpleNamespace(paths=paths, store=store, run_dir=paths.run_dir(_RUN_ID), log=log, lines=lines,
                           phase_windows={}, control_check=None, selected_slugs=[], module=paths.module,
                           repository=paths.repository, is_dry_run=False)


def _run_phase_3(ctx: SimpleNamespace,
                 model: _FakeModel) -> list[dict]:
    """The node driven as the graph drives it, until the queue is empty; each node call's update."""

    previous = fast_frame_note_service.set_model_factory(lambda name: model)
    state = {"queue": [slug for slug in ctx.store.list_items()], "cursor": 0}
    updates = []
    try:
        while state["cursor"] < len(state["queue"]):
            update = process_frames_node.process_frames_node(state, {"configurable": {"ctx": ctx}})
            updates.append(update)
            state = {**state, "cursor": update["cursor"]}
    finally:
        fast_frame_note_service.set_model_factory(previous)

    return updates


def test_a_run_takes_the_workflow_chosen_for_it_else_the_settings_one():
    folder = Path(tempfile.mkdtemp())
    default = video_workflow_service.resolve_workflow(folder)
    video_workflow_service.write_chosen_workflow(folder, video_workflow_vocabulary.FAST_PROCESSING, "user")
    try:
        video_workflow_service.write_chosen_workflow(folder, "turbo", "user")
        refusal = ""
    except ValueError as exc:
        refusal = str(exc)
    harness.check("with nothing chosen, the settings' video.workflow", default == config.settings.video.workflow == "quality",
                  default)
    harness.check("a chosen workflow is kept with who chose it",
                  video_workflow_service.read_chosen_workflow(folder) == {"workflow": "fast-processing", "chosenBy": "user"}
                  and video_workflow_service.is_fast_processing(folder))
    harness.check("an unknown workflow is refused by name, listing the workflows",
                  "turbo" in refusal and "fast-processing" in refusal, refusal)


def test_phase_3_describes_each_frame_alone_in_sessions_of_the_follow_up_limit():
    ctx = _make_run(7)
    model = _FakeModel()
    fast = config.settings.video.fast_processing
    previous = fast.frame_follow_ups
    fast.frame_follow_ups = 5
    try:
        updates = _run_phase_3(ctx, model)
    finally:
        fast.frame_follow_ups = previous
    humans = [[message for message in call if isinstance(message, HumanMessage)] for call in model.calls]
    harness.check("7 frames at 5 follow-ups: two sessions, 5 frames then 2",
                  [update["cursor"] for update in updates] == [5, 7] and len(model.calls) == 7, str(updates))
    harness.check("each session opens with the instruction, and each frame is one more follow-up of it",
                  all(isinstance(call[0], SystemMessage) for call in model.calls)
                  and [len(found) for found in humans] == [1, 2, 3, 4, 5, 1, 2], str([len(found) for found in humans]))
    harness.check("a frame's message holds its number and its image, never its speech",
                  humans[0][0].content[0]["text"].startswith("Frame 0: Describe this frame whole") and humans[0][0].content[1]["type"] == "image_url"
                  and "gateway" not in json.dumps(humans[0][0].content[0]), str(humans[0][0].content[0]))
    harness.check("the session's model has submit_note and thinks nothing, as the settings say",
                  model.tools == [fast_frame_note_service.SUBMIT_NOTE_TOOL] and model.bound.get("reasoning_effort") == "none")
    text = fast_frame_note_service.build_descriptions_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("every frame's section is appended in order with its time, its speech and its note",
                  fast_frame_note_service.list_described_frames(ctx.paths) == set(range(7))
                  and text.index("## Frame 0 · 00:00") < text.index("## Frame 6 · 01:00")
                  and "Said while it is on screen: Speaker A: the gateway step 6" in text and "- notes of Frame 6:" in text,
                  text[:400])
    harness.check("every frame is written and phase 3 complete",
                  ctx.store.count_by_status()["written"] == 7 and ctx.store.get_phase("frames_processing") == "complete")
    harness.check("each frame says on the console that it was described",
                  sum("described:" in line for line in ctx.lines) == 7, "\n".join(ctx.lines))


def test_a_reply_with_no_tool_call_keeps_its_text_and_says_so():
    ctx = _make_run(2)
    _run_phase_3(ctx, _FakeModel(is_calling_tool=False))
    text = fast_frame_note_service.build_descriptions_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("the reply's text is the description, nothing is refused",
                  "plain notes of Frame 1:" in text and ctx.store.count_by_status()["written"] == 2, text)
    harness.check("the console says the text was kept", any("no submit_note: its text kept" in line for line in ctx.lines))


def test_a_reply_cut_at_its_output_limit_keeps_its_text_and_leaves_the_session():
    ctx = _make_run(3)
    model = _FakeModel(cut_frame="Frame 1:")
    _run_phase_3(ctx, model)
    third = [" ".join(message.content[0]["text"].split()[:2]) for message in model.calls[2] if isinstance(message, HumanMessage)]
    text = fast_frame_note_service.build_descriptions_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("each call may write at most frameMaxTokens",
                  model.bound.get("max_tokens") == config.settings.video.fast_processing.frame_max_tokens, str(model.bound))
    harness.check("the next frame's call holds frame 0 and itself, never the cut frame",
                  third == ["Frame 0:", "Frame 2:"], str(third))
    harness.check("the cut reply's text is still frame 1's description, and the console says it was cut",
                  "the same line" in text and any("cut at" in line for line in ctx.lines), "\n".join(ctx.lines))


def test_a_fast_runs_phase_3_and_4_show_each_turn_with_its_thinking_and_phase_3_takes_a_message_defect():
    # The defect: a fast run's phase 3 and 4 eye opened an empty panel, and the Frames table offered no way to a
    # session's turns. The fix: each call is kept as a turn (thinking, words, tool call), and a message reaches phase 3.
    ctx = _make_run(3)
    messages_path = worker_message_service.build_phase_messages_path(ctx.paths.output_root, "frames_processing")
    worker_message_service.queue_message(messages_path, {"text": "name every field of the form", "role": "user",
                                                         "is_question": False})
    model = _FakeModel()
    _run_phase_3(ctx, model)
    writer = _FakeModel(text="# Gateway demo\n\nThe gateway signs every token.\n")
    previous = fast_document_service.set_model_factory(lambda name: writer)
    try:
        fast_document_service.write_document(ctx, "Gateway-demo.mp4")
    finally:
        fast_document_service.set_model_factory(previous)
    root = ctx.paths.output_root
    phase_3 = worker_live_service.describe_phase_worker(root, 3, 0.0)
    session = worker_live_service.describe_fast_session(root, "fast-notes-000-002")
    phase_4 = worker_live_service.describe_phase_worker(root, 4, 0.0)
    first = phase_3["turns"][0]
    harness.check("phase 3's eye shows one turn a frame, each with its thinking and its submit_note",
                  [turn["thinking"] for turn in phase_3["turns"]] == ["looking at Frame 0:", "looking at Frame 1:",
                                                                     "looking at Frame 2:"]
                  and first["steps"][0]["tool"] == "submit_note" and "notes of Frame 0" in first["steps"][0]["args"],
                  json.dumps(phase_3["turns"])[:400])
    harness.check("a session's row shows the same turns", len(session["turns"]) == 3 and session["step"] == "")
    sent = [message.content for message in model.calls[0] if isinstance(message, HumanMessage)]
    harness.check("the person's message reached the first frame's call, and the panel says it was taken",
                  "name every field of the form" in str(sent[0]) and phase_3["messages"][0]["is_sent"]
                  and phase_3["can_send"], str(sent)[:300])
    harness.check("phase 4's eye shows its one call with the document, and takes no message",
                  len(phase_4["turns"]) == 1 and "signs every token" in phase_4["turns"][0]["text"] and not phase_4["can_send"])
    harness.check("both cards offer the worker once it ran",
                  run_status_service._has_phase_worker(root, 3, "frames_processing")
                  and run_status_service._has_phase_worker(root, 4, "finalize"))


def test_a_fast_run_writes_each_frames_notes_into_a_file_of_its_own_defect():
    # The defect: a fast run's Artifacts tab showed no notes for each frame, as a quality run's does: every description
    # went into one file. The fix: each frame's section is also its own file in the frame notes folder.
    ctx = _make_run(2)
    _run_phase_3(ctx, _FakeModel())
    record = (ctx.paths.output_root / output_contract.ITEMS_DIR_NAME / "frame-001.md").read_text(encoding="utf-8")
    harness.check("frame 1's file opens with its heading and holds its speech and its note",
                  record.startswith("# Frame 1 · 00:10") and "the gateway step 1" in record and "notes of Frame 1" in record,
                  record)


def test_a_fast_run_made_before_turns_were_kept_shows_its_notes_from_its_call_logs_defect():
    # The defect: a fast run started before turns were kept showed an empty panel on its phase 3 eye. The fix: its
    # frames' call logs give one turn a frame, its note as the submit_note, with no thinking.
    ctx = _make_run(2)
    _run_phase_3(ctx, _FakeModel())
    for path in (ctx.paths.output_root / "logs" / output_contract.FAST_TURNS_DIR).glob("*.jsonl"):
        path.unlink()
    turns = worker_live_service.describe_phase_worker(ctx.paths.output_root, 3, 0.0)["turns"]
    harness.check("each frame's note is a turn, read from its call log",
                  [turn["frame"] for turn in turns] == [0, 1] and "notes of Frame 1" in turns[1]["steps"][0]["args"],
                  json.dumps(turns)[:300])


def test_phase_4_writes_the_document_in_one_call_over_the_descriptions_and_publishes_it():
    ctx = _make_run(3)
    _run_phase_3(ctx, _FakeModel())
    writer = _FakeModel(text="# Gateway demo\n\n## Summary\n\nThe gateway signs every token.\n")
    previous = fast_document_service.set_model_factory(lambda name: writer)
    try:
        document = fast_document_service.write_document(ctx, "Gateway-demo.mp4")
        again = fast_document_service.write_document(ctx, "Gateway-demo.mp4")
    finally:
        fast_document_service.set_model_factory(previous)
    sent = writer.calls[0][1].content[0]["text"]
    request = fast_document_service.build_request_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("the request is saved whole beside the other requests, its frames in order with their speech",
                  request.startswith("# Request sent to the document writer") and "## Instructions" in request
                  and request.index("## Frame 0 · 00:00") < request.index("## Frame 2 · 00:20")
                  and "Said while it is on screen: Speaker A: the gateway step 2" in request, request[:300])
    harness.check("one call, handed every frame's section", len(writer.calls) == 1 and "## Frame 2 · 00:20" in sent, sent[:300])
    harness.check("the document and the published folder hold the answer",
                  "The gateway signs every token." in ctx.paths.summary.read_text(encoding="utf-8")
                  and "The gateway signs every token." in document.read_text(encoding="utf-8"), str(document))
    harness.check("a resume publishes the kept answer and calls no model again", again == document and len(writer.calls) == 1)


def test_phase_4_of_a_fast_run_takes_the_one_call_and_nothing_of_the_conversation():
    ctx = _make_run(2)
    _run_phase_3(ctx, _FakeModel())
    writer = _FakeModel(text="# Gateway demo\n\nThe gateway signs every token.\n")
    previous = fast_document_service.set_model_factory(lambda name: writer)
    try:
        update = write_document_node.write_document_node({}, {"configurable": {"ctx": ctx}})
    finally:
        fast_document_service.set_model_factory(previous)
    harness.check("phase 4 completes from the one call", update == {"phases": {"finalize": "complete"}}
                  and len(writer.calls) == 1 and ctx.paths.summary.is_file(), str(update))
    harness.check("phase 4's time line counts its one call",
                  any(line.startswith("phase 4 time") and "1 call," in line for line in ctx.lines), "\n".join(ctx.lines))


def test_phase_4_refuses_descriptions_its_window_cannot_hold():
    ctx = _make_run(2)
    _run_phase_3(ctx, _FakeModel())
    ctx.phase_windows = {"finalize": 100}
    try:
        fast_document_service.write_document(ctx, "Gateway-demo.mp4")
        refusal = ""
    except ValueError as exc:
        refusal = str(exc)
    harness.check("the refusal names the sizes and the window", "window of 100" in refusal, refusal)


def test_phase_4_of_a_fast_run_lets_an_error_that_is_not_the_window_rise():
    ctx = _make_run(2)
    _run_phase_3(ctx, _FakeModel())

    def broken_write(ctx_, recording):
        raise ValueError("a bug in the writer, not the window")

    original = fast_document_service.write_document
    fast_document_service.write_document = broken_write
    try:
        write_document_node.write_document_node({}, {"configurable": {"ctx": ctx}})
        outcome = "recorded as a blocked phase"
    except ValueError:
        outcome = "raised"
    finally:
        fast_document_service.write_document = original
    harness.check("an unrelated ValueError is not reported as a refusal of the window", outcome == "raised", outcome)


def test_phase_4_writes_in_the_room_its_window_has_left():
    ctx = _make_run(2)
    _run_phase_3(ctx, _FakeModel())
    writer = _FakeModel(text="# Gateway demo\n")
    prompt = fast_document_service.count_prompt_tokens(fast_document_service.build_messages(ctx.paths, "Gateway-demo.mp4"))
    ctx.phase_windows = {"finalize": prompt + 4_000 + 20_000}
    previous = fast_document_service.set_model_factory(lambda name: writer)
    try:
        fast_document_service.write_document(ctx, "Gateway-demo.mp4")
    finally:
        fast_document_service.set_model_factory(previous)
    harness.check("a window with 20,000 free after the prompt and margin: the answer may write 20,000, under its model's "
                  "own 32,768", writer.bound.get("max_tokens") == 20_000, str(writer.bound))
    harness.check("the room never passes the model's own limit",
                  fast_document_service.compute_answer_room(1_000, 183_040, 32_768) == 32_768)


def test_phases_5_and_6_are_off_for_a_fast_processing_run():
    ctx = _make_run(1)
    config_ = {"configurable": {"ctx": ctx}}
    review = review_document_node.write_reviewed_document_node({}, config_)
    collection = collect_critiques_node.collect_critiques_node({}, config_)
    harness.check("the review and the critique collection are recorded off",
                  review == {"phases": {"review": "off"}} and collection == {"phases": {"critique_collection": "off"}}
                  and ctx.store.get_phase("review") == "off" and ctx.store.get_phase("critique_collection") == "off",
                  f"{review} {collection}")


def test_the_run_line_and_the_header_carry_the_workflow():
    command = job_queue_service.build_run_command(RunJobRequest(video=Path("/in/a.mp4"), output_root=Path("/out/a"),
                                                                workflow="fast-processing"))
    folder = Path(tempfile.mkdtemp())
    video_workflow_service.write_chosen_workflow(folder, "fast-processing", "user")
    harness.check("the queue's run line passes --workflow",
                  command[command.index("--workflow") + 1] == "fast-processing", str(command))
    checks = run_command.RunStartChecksResult(transcription="audio: mlx", diarization="diarization: off")
    lines = run_command.render_model_lines(checks, {"finalize": 183040}, is_audio=False, is_fast=True)
    harness.check("a fast run's phase 3 line names the frame model and its session, phase 4's the one call",
                  lines[2].split()[1] == "qwen3.5-4b:" and "5 frames a session" in lines[2]
                  and "one call, window 183,040" in lines[3], "\n".join(lines))
    harness.check("a fast run's review and critique collection lines say off, naming no model",
                  lines[4].split()[1:3] == ["off", "(a"] and lines[5].split()[0] == "critique_collection"
                  and "off" in lines[5],
                  "\n".join(lines))
    harness.check("the header's first line names the workflow",
                  run_command.describe_run_line("2026-10-01_09-00", 1, 6, "video", folder)
                  == "run 2026-10-01_09-00  phases 1..6 · fast processing workflow"
                  and run_command.describe_run_line("x", 1, 6, "audio", folder).endswith("· audio workflow"))
    # A PDF's header said "audio workflow" and named the audio engine (the Wikipedia PDF's run)
    harness.check("a PDF's header names the PDF workflow",
                  run_command.describe_run_line("x", 1, 6, run_command.describe_medium(Path("/in/a.pdf"), True), folder)
                  .endswith("· PDF workflow"))
    harness.check("the header names the workflow and its models",
                  run_command.describe_workflow(folder).startswith("fast processing (phase 3 qwen3.5-4b, 5 frames a session")
                  and run_command.describe_workflow(Path(tempfile.mkdtemp())) == "quality",
                  run_command.describe_workflow(folder))


def test_a_fast_runs_phase_3_and_4_windows_are_their_fast_models():
    seen = []
    original = run_command.host_window_service.read_loaded_window
    run_command.host_window_service.read_loaded_window = lambda url, model: seen.append(model) or {
        "qwen3.5-4b": 131072}.get(model, 183040)
    try:
        fast = run_command.read_phase_windows(logging.getLogger("test"), is_fast=True)
        quality = run_command.read_phase_windows(logging.getLogger("test"), is_fast=False)
    finally:
        run_command.host_window_service.read_loaded_window = original
    harness.check("a fast run measures phase 3 against the 4B's loaded window, a quality run against its own model's",
                  fast.get("frames_processing") == 131072 and quality.get("frames_processing") == 183040,
                  f"{fast} {quality} {seen}")


def test_the_phase_2_line_names_sessions_for_a_fast_run_and_batches_for_a_quality_one():
    fast = Path(tempfile.mkdtemp())
    video_workflow_service.write_chosen_workflow(fast, video_workflow_vocabulary.FAST_PROCESSING, "user")
    harness.check("a fast run's frames go in sessions of frameFollowUps, a quality run's in batches",
                  video_workflow_service.describe_frame_grouping(fast)
                  == f"sessions of {config.settings.video.fast_processing.frame_follow_ups}"
                  and video_workflow_service.describe_frame_grouping(Path(tempfile.mkdtemp())).startswith("batches of "))

