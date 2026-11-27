"""The direct-draft video workflow (the Fast speed): phase 3 writes each batch of frames straight into a draft document
in chapters, and phase 4 is one conversation over the draft's outline that cuts the pictures the draft places and
submits the title, summary, agenda and heading changes; the program builds the document from them. Phases 5 and 6 are
off.

The models are fakes with the real ones' interface (`bind_tools`, `bind`, `invoke(messages)` -> an `AIMessage`), set
through each service's `set_model_factory`; they record every call. The recording is a real short video, so the frame
tools cut from it as they do on a run.
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
from models.draft_section_spec import DraftSectionSpec
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from schemas import direct_draft_outline
from services import direct_draft_batch_service
from services import direct_draft_outline_service
from services import document_folder_service
from services import phase_file_service
from services import run_paths_service
from services import run_store_service
from services import video_workflow_service
from services import worker_live_service
from services import worker_message_service
from utils import draft_structure_utils

_RUN_ID = "2026-10-01_09-00"
# The window drawn on the recording's 1920 x 1080 frame; the stored frames are kept at half that size
_WINDOW = (400, 300, 1200, 800)
_DRAFT = """# Draft of Gateway-demo.mp4

<!-- batch 1 · frames 0-7 -->

Opening words before any heading.

## Gateway

The gateway signs every token.

{{frame-002: The signing flow}}

### Tokens

A token lasts one hour.

```text
## not a heading
```

<!-- batch 2 · frames 8-9 -->

It is refreshed after 50 minutes.

## Storage

Tokens are kept in a store.

{{frame-009: The token store}}
"""


class _ScriptedModel:
    """Answers each call with the next reply of its script, and keeps every call's messages and every bound argument."""

    def __init__(self, replies: list[AIMessage]):
        self.replies = list(replies)
        self.calls: list[list] = []
        self.tools: list = []
        self.bound: dict = {}

    def bind_tools(self, tools: list) -> "_ScriptedModel":
        self.tools = list(tools)

        return self

    def bind(self, **kwargs) -> "_ScriptedModel":
        self.bound.update(kwargs)

        return self

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(list(messages))

        return self.replies[len(self.calls) - 1]


def _call(name: str,
          args: dict,
          number: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{number}"}])


def _make_run(frame_count: int) -> SimpleNamespace:
    """A direct-draft run folder after phase 2: a recording of 1920 x 1080, `frame_count` frames kept at half size, in
    `02_frames.json` and in the store."""

    root = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=root / "inbox", module="Gateway-demo.mp4", output_root_override=root / "Gateway-demo")
    run_paths_service.ensure_run_paths(paths)
    (root / "inbox").mkdir(exist_ok=True)
    full = numpy.full((1080, 1920, 3), 255, numpy.uint8)
    cv2.rectangle(full, _WINDOW[:2], _WINDOW[2:], (0, 0, 0), 6)
    writer = cv2.VideoWriter(str(root / "inbox" / "Gateway-demo.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 5, (1920, 1080))
    for _ in range(10):
        writer.write(full)
    writer.release()
    (paths.output_root / "frames").mkdir(parents=True, exist_ok=True)
    items = []
    for number in range(frame_count):
        slug = f"frame-{number:03d}"
        cv2.imwrite(str(paths.output_root / "frames" / f"{slug}.jpg"), cv2.resize(full, (960, 540)))
        items.append({"slug": slug, "index": number, "frameNumber": number, "path": f"frames/{slug}.jpg",
                      "timestamp": 0.1 * number, "box": [0, 0, 1920, 1080], "screenBox": [0, 0, 1920, 1080],
                      "transcriptCurrent": f"Speaker A: the gateway step {number}"})
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"],
                                        {"items": items, "video": {}})
    store = run_store_service.RunStore.create(paths.run_state(_RUN_ID), RunIdentity.for_run(_RUN_ID, paths),
                                              budgets={}, models=[])
    store.seed_items(items)
    video_workflow_service.write_chosen_workflow(paths.output_root, video_workflow_vocabulary.DIRECT_DRAFT, "user")
    lines: list[str] = []
    log = logging.getLogger(f"direct-draft-{root.name}")
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    return SimpleNamespace(paths=paths, store=store, run_dir=paths.run_dir(_RUN_ID), log=log, lines=lines,
                           phase_windows={}, control_check=None, selected_slugs=[], module=paths.module,
                           repository=paths.repository, is_dry_run=False)


def _run_phase_3(ctx: SimpleNamespace,
                 model: _ScriptedModel) -> list[dict]:
    """The node driven as the graph drives it, until the queue is empty; each node call's update."""

    previous = direct_draft_batch_service.set_model_factory(lambda name: model)
    state = {"queue": [slug for slug in ctx.store.list_items()], "cursor": 0}
    updates = []
    try:
        while state["cursor"] < len(state["queue"]):
            update = process_frames_node.process_frames_node(state, {"configurable": {"ctx": ctx}})
            updates.append(update)
            state = {**state, "cursor": update["cursor"]}
    finally:
        direct_draft_batch_service.set_model_factory(previous)

    return updates


def _run_phase_4(ctx: SimpleNamespace,
                 model: _ScriptedModel) -> dict:
    previous = direct_draft_outline_service.set_model_factory(lambda name: model)
    try:
        return write_document_node.write_document_node({}, {"configurable": {"ctx": ctx}})
    finally:
        direct_draft_outline_service.set_model_factory(previous)


def _count_images(message: HumanMessage) -> int:
    return sum(1 for part in message.content if part.get("type") == "image_url")


def _sections() -> list[DraftSectionSpec]:
    return draft_structure_utils.parse_sections(_DRAFT)


def test_the_draft_is_read_back_as_sections_with_ids_and_a_fence_keeps_its_lines():
    sections = _sections()
    harness.check("the lead text, then each heading by its place, the batch markers and the draft's title left out",
                  [(section.id, section.level, section.title) for section in sections]
                  == [("H00", 0, ""), ("H01", 2, "Gateway"), ("H02", 3, "Tokens"), ("H03", 2, "Storage")],
                  str([(section.id, section.title) for section in sections]))
    harness.check("a heading inside a code fence is text, not a heading",
                  any("## not a heading" in line for line in sections[2].lines))
    harness.check("the headings are listed with their ids, the lead having none",
                  draft_structure_utils.render_headings(sections) == "H01 ## Gateway\nH02 ### Tokens\nH03 ## Storage")
    outline = draft_structure_utils.render_outline(sections, 20)
    harness.check("the outline shows each section's first line cut to the limit and the pictures it places",
                  "H01 ## Gateway\n    The gateway signs ev…\n    picture frame-002: The signing flow" in outline
                  and "picture frame-009: The token store" in outline, outline)
    last = draft_structure_utils.read_last_section(_DRAFT, 10_000)
    harness.check("the last section is its heading and its text, and a long one keeps its end",
                  last.startswith("## Storage") and "Tokens are kept in a store." in last
                  and draft_structure_utils.read_last_section(_DRAFT, 12).startswith("…")
                  and draft_structure_utils.read_last_section("# Draft of x\n", 100) == "")


def test_heading_changes_rename_merge_and_move_with_the_subsections_and_name_each_refusal():
    sections = _sections()
    refused = draft_structure_utils.apply_changes(sections, [
        {"heading": "H02", "action": "rename", "title": "Token lifetime"},
        {"heading": "H03", "action": "move_after", "after": "H00"},
        {"heading": "H09", "action": "rename", "title": "Nothing"},
        {"heading": "H01", "action": "move_after", "after": "H02"},
        {"heading": "H01", "action": "explode"}])
    titles = [section.title for section in sections]
    harness.check("a rename and a move are done, and a chapter moved after the lead lands before the first chapter",
                  titles == ["", "Storage", "Gateway", "Token lifetime"], str(titles))
    harness.check("each change that cannot be done is named, the others are done",
                  len(refused) == 3 and refused[0].startswith("H09") and "own subsections" in refused[1]
                  and "explode" in refused[2], str(refused))
    merged = _sections()
    draft_structure_utils.apply_changes(merged, [{"heading": "H02", "action": "merge_into_previous"}])
    harness.check("a merged heading's text ends the section before it and its own line is gone",
                  [section.title for section in merged] == ["", "Gateway", "Storage"]
                  and "A token lasts one hour." in "\n".join(merged[1].lines)
                  and "### Tokens" not in draft_structure_utils.render_sections(merged))


def test_a_picture_line_takes_its_cut_in_order_and_one_with_no_cut_is_dropped():
    text = "Intro\n\n{{frame-001: first}}\n\n{{frame-001: second}}\n\n{{frame-004: }}\n\n```\n{{frame-001: in code}}\n```\n"
    done = draft_structure_utils.replace_pictures(text, {"frame-001": ["cuts/cut-1.png"]})
    harness.check("the first picture of a frame takes its first cut, the second has none and is dropped",
                  "![first](cuts/cut-1.png)\n\n*first*" in done and "second" not in done.replace("in code", ""), done)
    harness.check("a picture with no cut of its frame is dropped, and a line in a code fence stays as written",
                  "frame-004" not in done and "{{frame-001: in code}}" in done, done)


def test_phase_3_writes_batches_of_the_frames_per_call_straight_into_the_draft():
    ctx = _make_run(10)
    model = _ScriptedModel([AIMessage(content="## Gateway\n\nThe gateway signs every token.\n\n"
                                              "{{frame-002: The signing flow}}\n\n### Tokens\n\nA token lasts one hour.\n"),
                            AIMessage(content="It is refreshed after 50 minutes.\n\n## Storage\n\nTokens are kept in a "
                                              "store.\n\n{{frame-009: The token store}}\n")])
    messages_path = worker_message_service.build_phase_messages_path(ctx.paths.output_root, "frames_processing")
    worker_message_service.queue_message(messages_path, {"text": "name every field of the form", "role": "user",
                                                         "is_question": False})
    settings = config.settings.frames_processing
    previous = settings.frames_per_call
    settings.frames_per_call = 8
    try:
        updates = _run_phase_3(ctx, model)
    finally:
        settings.frames_per_call = previous
    draft = direct_draft_batch_service.build_draft_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("10 frames at 8 a call: two calls, the cursor at 8 then 10",
                  [update["cursor"] for update in updates] == [8, 10] and len(model.calls) == 2, str(updates))
    harness.check("the draft opens with its title, then each batch's marker and its answer, in order",
                  draft.startswith("# Draft of Gateway-demo.mp4\n") and draft.index("<!-- batch 1 · frames 0-7 -->")
                  < draft.index("## Gateway") < draft.index("<!-- batch 2 · frames 8-9 -->") < draft.index("## Storage"),
                  draft)
    first, second = model.calls
    harness.check("the first call has the instruction, the person's message and the batch, 8 images and each frame's speech",
                  isinstance(first[0], SystemMessage) and "name every field of the form" in str(first[1].content)
                  and _count_images(first[-1]) == 8 and "Speaker A: the gateway step 7" in str(first[-1].content)
                  and "frame-000 · 00:00" in str(first[-1].content), str(first[-1].content)[:300])
    harness.check("the first call is told the draft is empty", "nothing yet" in str(first[-1].content))
    tail = str(second[-1].content)
    harness.check("the second call has 2 images, the headings so far and the last section's text",
                  _count_images(second[-1]) == 2 and "H01 ## Gateway" in tail and "H02 ### Tokens" in tail
                  and "A token lasts one hour." in tail, tail[:500])
    harness.check("the calls carry no tools and no frame is described on its own: no notes file, one call a batch",
                  model.tools == [] and not (ctx.paths.output_root / output_contract.FRAME_DESCRIPTIONS_FILE).exists())
    harness.check("every frame is written, phase 3 is complete and each batch's console line says so",
                  ctx.store.count_by_status()["written"] == 10 and ctx.store.get_phase("frames_processing") == "complete"
                  and sum("written into the draft" in line for line in ctx.lines) == 2, "\n".join(ctx.lines))
    phase_3 = worker_live_service.describe_phase_worker(ctx.paths.output_root, 3, 0.0)
    harness.check("phase 3's panel shows a turn a batch, and the person's message was taken",
                  len(phase_3["turns"]) == 2 and "signs every token" in phase_3["turns"][0]["text"]
                  and phase_3["messages"][0]["is_sent"], json.dumps(phase_3["turns"])[:300])


def test_each_batch_gets_the_next_marker_number_when_batches_are_written_one_after_another():
    ctx = _make_run(2)
    direct_draft_batch_service.start_draft(ctx.paths)
    model = _ScriptedModel([AIMessage(content="## One\n\nFirst.\n"), AIMessage(content="## Two\n\nSecond.\n")])
    previous = direct_draft_batch_service.set_model_factory(lambda name: model)
    try:
        direct_draft_batch_service.write_batch(ctx, [ctx.store.get_item("frame-000")])
        direct_draft_batch_service.write_batch(ctx, [ctx.store.get_item("frame-001")])
    finally:
        direct_draft_batch_service.set_model_factory(previous)
    draft = direct_draft_batch_service.build_draft_path(ctx.paths).read_text(encoding="utf-8")
    harness.check("the second batch's marker is number 2", "<!-- batch 1 · frames 0-0 -->" in draft
                  and "<!-- batch 2 · frames 1-1 -->" in draft and direct_draft_batch_service.count_written_batches(ctx.paths) == 2,
                  draft)


def test_phase_4_cuts_the_pictures_submits_the_outline_and_builds_and_publishes_the_document():
    ctx = _make_run(10)
    direct_draft_batch_service.build_draft_path(ctx.paths).write_text(_DRAFT, encoding="utf-8")
    submit = {"title": "Gateway token signing", "summary": ["The gateway signs every token.", "A token lasts one hour."],
              "agenda": [{"heading": "H01", "text": "How tokens are signed."}, {"heading": "H03", "text": "Where they are kept."},
                         {"heading": "H77", "text": "A chapter the draft does not have."}],
              "changes": [{"heading": "H02", "action": "rename", "title": "Token lifetime"},
                          {"heading": "H55", "action": "merge_into_previous"}]}
    model = _ScriptedModel([_call("look", {"frame": "frame-002"}, 1),
                            _call("cut", {"frame": "frame-002", "rect": [200, 150, 600, 400]}, 2),
                            _call("submit_outline", submit, 3)])
    update = _run_phase_4(ctx, model)
    document = ctx.paths.summary.read_text(encoding="utf-8")
    harness.check("phase 4 completes after three replies, the last one the submit", update == {"phases": {"finalize": "complete"}}
                  and len(model.calls) == 3, str(update))
    harness.check("the model is offered the frame tools and the submit tool",
                  [tool["function"]["name"] for tool in model.tools]
                  == ["look", "cut", "mark", "mask", "undo_cut", "submit_outline"], str(model.tools)[:300])
    request = " ".join(str(part) for part in model.calls[0][1].content)
    harness.check("the first call is told the outline only: ids, headings, first lines, the pictures",
                  "H01 ## Gateway" in request and "picture frame-002: The signing flow" in request
                  and "picture frame-009: The token store" in request and "Speaker A" not in request, request[:400])
    harness.check("the document opens with the title, the summary, the agenda and the contents",
                  document.startswith("# Gateway token signing\n\n## Executive summary\n\nThe gateway signs every token.\n\n"
                                      "- A token lasts one hour.\n"), document[:300])
    harness.check("the agenda links each chapter named and leaves out the one the draft lacks",
                  "1. [Gateway](#gateway): How tokens are signed." in document
                  and "2. [Storage](#storage): Where they are kept." in document and "H77" not in document
                  and "does not have" not in document, document)
    harness.check("the contents list every heading, a section under its chapter, with the renamed title",
                  "- [Gateway](#gateway)\n  - [Token lifetime](#token-lifetime)\n- [Storage](#storage)" in document, document)
    harness.check("the renamed heading is in the text and the old one is gone",
                  "### Token lifetime" in document and "### Tokens" not in document)
    harness.check("the cut frame's picture line is a figure with its caption and the uncut frame's line is dropped",
                  "![The signing flow](" in document and "*The signing flow*" in document
                  and "{{" not in document and "The token store" not in document.split("## Storage", 1)[1], document)
    harness.check("the text before the first heading and the heading in a fence are kept",
                  "Opening words before any heading." in document and "## not a heading" in document)
    published = document_folder_service.build_document_path(ctx.paths)
    resources = list((published.parent / output_contract.RESOURCES_DIR_NAME).glob("*"))
    harness.check("the folder is published with the cut as its one figure and the provenance",
                  published.is_file() and len(resources) == 1 and (published.parent / output_contract.DOCUMENT_PROVENANCE_FILE).is_file()
                  and "Gateway token signing" in published.read_text(encoding="utf-8"), str(resources))
    harness.check("the answer is kept, and a heading change that cannot be done is named on the console",
                  json.loads(direct_draft_outline_service.build_outline_path(ctx.paths).read_text(encoding="utf-8"))["title"]
                  == "Gateway token signing" and any("H55" in line and "skipped" in line for line in ctx.lines),
                  "\n".join(ctx.lines))
    phase_4 = worker_live_service.describe_phase_worker(ctx.paths.output_root, 4, 0.0)
    harness.check("phase 4's panel shows the three turns with their tools",
                  [turn["steps"][0]["tool"] for turn in phase_4["turns"]] == ["look", "cut", "submit_outline"],
                  json.dumps(phase_4["turns"])[:300])
    again = _run_phase_4(ctx, _ScriptedModel([]))
    harness.check("a resume builds the document again from the kept answer and calls no model",
                  again == {"phases": {"finalize": "complete"}} and ctx.paths.summary.read_text(encoding="utf-8") == document)


def test_a_conversation_that_never_submits_publishes_the_draft_under_the_recordings_name():
    ctx = _make_run(10)
    direct_draft_batch_service.build_draft_path(ctx.paths).write_text(_DRAFT, encoding="utf-8")
    settings = config.settings.video.direct_draft
    previous = settings.outline_calls
    settings.outline_calls = 2
    model = _ScriptedModel([AIMessage(content="thinking aloud"), AIMessage(content="still no tool call")])
    try:
        update = _run_phase_4(ctx, model)
    finally:
        settings.outline_calls = previous
    document = ctx.paths.summary.read_text(encoding="utf-8")
    harness.check("two calls were made, then the draft is published as written under the recording's name",
                  update == {"phases": {"finalize": "complete"}} and len(model.calls) == 2
                  and document.startswith("# Gateway-demo.mp4\n") and "## Gateway" in document and "{{" not in document,
                  document[:300])
    harness.check("a reply with no tool call is asked again to submit",
                  "Call `submit_outline`" in str(model.calls[1][-1].content), str(model.calls[1][-1].content)[:200])


def test_a_dry_run_describes_the_outline_call_and_makes_none():
    ctx = _make_run(10)
    direct_draft_batch_service.build_draft_path(ctx.paths).write_text(_DRAFT, encoding="utf-8")
    ctx.is_dry_run = True
    model = _ScriptedModel([])
    previous = direct_draft_outline_service.set_model_factory(lambda name: model)
    try:
        update = write_document_node.write_document_node({"is_dry_run": True}, {"configurable": {"ctx": ctx}})
    finally:
        direct_draft_outline_service.set_model_factory(previous)
    harness.check("the phase is complete and no model was called", update == {"phases": {"finalize": "complete"}}
                  and model.calls == [], str(update))


def test_phases_5_and_6_are_off_and_the_page_lists_only_the_phases_a_direct_draft_run_runs():
    ctx = _make_run(1)
    config_ = {"configurable": {"ctx": ctx}}
    review = review_document_node.write_reviewed_document_node({}, config_)
    collection = collect_critiques_node.collect_critiques_node({}, config_)
    harness.check("the review and the critique collection are recorded off",
                  review == {"phases": {"review": "off"}} and collection == {"phases": {"critique_collection": "off"}},
                  f"{review} {collection}")
    harness.check("the workflow is a no-critic one and not the old fast-processing one",
                  video_workflow_service.has_no_critic(ctx.paths.output_root)
                  and video_workflow_service.is_direct_draft(ctx.paths.output_root)
                  and not video_workflow_service.is_fast_processing(ctx.paths.output_root))
    harness.check("the frames of a direct-draft run go in batches of the frames a call",
                  video_workflow_service.describe_frame_grouping(ctx.paths.output_root).startswith("batches of "),
                  video_workflow_service.describe_frame_grouping(ctx.paths.output_root))


def test_the_run_header_names_the_direct_draft_workflow_and_shows_phases_5_and_6_off():
    ctx = _make_run(1)
    checks = run_command.RunStartChecksResult(transcription="audio: mlx", diarization="diarization: off")
    lines = run_command.render_model_lines(checks, {}, is_audio=False, is_fast=False, has_no_critic=True)
    harness.check("the header's first line and workflow line name the direct draft",
                  run_command.describe_run_line(_RUN_ID, 1, 6, "video", ctx.paths.output_root)
                  == f"run {_RUN_ID}  phases 1..6 · direct draft workflow"
                  and run_command.describe_workflow(ctx.paths.output_root).startswith("direct draft (phase 3 writes "),
                  run_command.describe_workflow(ctx.paths.output_root))
    harness.check("phases 3 and 4 name their models and 5 and 6 say off",
                  "num_ctx" in lines[2] and "num_ctx" in lines[3] and "off" in lines[4] and "off" in lines[5],
                  "\n".join(lines))


def test_the_submit_schema_takes_the_answer_and_refuses_an_unknown_change():
    answer = {"title": "T", "summary": ["One."], "agenda": [{"heading": "H01", "text": "x"}],
              "changes": [{"heading": "H02", "action": "move_after", "after": "H05"}]}
    parsed = direct_draft_outline.SubmitOutline.model_validate(answer)
    harness.check("an answer with a title, a summary, agenda lines and a change is read", parsed.title == "T"
                  and parsed.agenda[0].heading == "H01" and parsed.changes[0].after == "H05", str(parsed))
    try:
        direct_draft_outline.SubmitOutline.model_validate({**answer, "changes": [{"heading": "H02", "action": "explode"}]})
        refused = False
    except ValueError:
        refused = True
    harness.check("a change that is none of the three actions is refused by the schema", refused)


def test_the_fast_speed_preset_points_at_the_direct_draft_workflow_with_eight_frames_a_call():
    fast = config.DEFAULT_SPEED_PRESETS["fast"]
    harness.check("the Fast preset's workflow is direct-draft", fast["video.workflow"] == "direct-draft", str(fast)[:300])
    harness.check("the Fast preset takes 8 frames a call", fast["framesProcessing.framesPerCall"] == 8, str(fast)[:300])
