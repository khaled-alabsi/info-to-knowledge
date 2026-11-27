"""The frame list node, the frame node and the small pieces around them, each called directly. No
graph is compiled to exercise one step: a node is a flat function of `(state, config)`.

What is under test is the phase-file mechanism: a phase that is asked for RUNS, a phase that is not
is READ from its file, a file that is not there is refused by name and never read as empty, and a
phase no later one needs is not demanded at all.
"""

import dataclasses
import json
import logging
import tempfile
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import BaseModel

from support import fakes
from support import fixtures as fx
from support import harness

import config as app_config
from agent_client import finish_reason_utils
from agent_client import path_sandbox
from agent_client.agent_errors import SandboxError
from chains import chain_runner
from cli import setup_commands
from graph import graph_state
from graph import routing
from graph.nodes import prepare_frames_node
from graph.nodes import process_frames_node
from graph.nodes.chain_attempt_loop import chain_attempt_loop_graph
from graph.nodes.chain_attempt_loop.nodes import invoke_chain_node
from models import output_contract
from utils import message_content_utils
from utils import chain_response_utils
from models.chain_call_result import ChainCallResult
from models import chain_reply_result
from models.attempt_call_spec import AttemptCallSpec
from models.batch_follow_up_result import BatchFollowUpResult
from models.frame_repair_result import FrameRepairResult
from models.job_retry_context import JobRetryContext, JobRetryHandles
from models.run_context import RunContext
from models.run_identity import RunIdentity
from services import attempt_log_service
from services import call_log_service
from services import chain_dispatch_service
from services import frame_records_service
from services import performance_log_service
from services import phase_file_service
from services import record_file_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from validators import loop_items_validator

FRAMES_PREPARING, FRAMES_PROCESSING, FINALIZE = (output_contract.PHASE_NUMBERS[name] for name in ("frames_preparing", "frames_processing", "finalize"))
RECORD = {"frame": 1, "screen_kind": "slide",
          "take_aways": ['"nearly nine in ten want it cheaper" \u2192 the "89 %" on the slide: the figure is the '
                         "share of voters, not of drivers", "Take aways: the slide's one number is a share of "
                         "voters. What was asked is not shown."],
          "facts": ["89 % of voters want cheaper fuel"], "diagram": "", "as_markdown": "",
          "screen": '- slide: white, the whole frame\n  - text "89 %": centre', "speech_visual_alignment": "the figure is being read out", "builds_on": [],
          "contradicts_earlier": [], "open_questions": [],
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
          "third_person_description": "The presenter points at the figure on the slide and reads it out.",
          "tags": ["refund policy", "reference"], "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}



def _seed_frames(ctx: RunContext,
                 items: list[dict]) -> None:
    """The frames phase 2 would leave: in the store, and in the frames file whose items hold each frame's notes."""

    ctx.store.seed_items(items)
    phase_file_service.write_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["transcribe"], {
        "segments": [{"text": "Guten Abend.", "start": 0.0, "end": 12.0, "words": []}]})
    phase_file_service.write_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})

def _load_run_context(model=None) -> RunContext:
    tmp = Path(tempfile.mkdtemp())
    paths = fx.make_paths(tmp)
    run_dir = tmp / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(paths.repository), module=paths.module,
                                        output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 2}, models=[])

    return RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store,
                      run_dir=run_dir, log=logging.getLogger("test"),
                      ignore=IgnoreList(), model=model)


def _build_items(ctx: RunContext) -> dict:
    """A frame list of two frames, each with a frame on disk under the output folder."""

    items = []
    for index in range(2):
        frame = ctx.paths.output_root / "frames" / f"frame-{index:03d}.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"\xff\xd8 a frame")
        items.append({"slug": f"frame-{index:03d}", "index": index, "timestamp": index * 6.0,
                      "path": f"frames/frame-{index:03d}.jpg", "transcriptBefore": "", "transcriptCurrent": "Guten Abend."})

    return {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION, "items": items}


def _bind(ctx: RunContext) -> dict:
    return {"configurable": {"ctx": ctx}}


def _run_process_frames_node(state: dict,
                   ctx: RunContext) -> dict:
    """The frame node with ONE frame per call, whatever this machine's `playbook.local.json` selects: these cases
    drive the single-frame path, and `test_frames_per_call.py` drives the groups."""

    previous = app_config.settings.frames_processing.frames_per_call
    previous_repairs = app_config.settings.frames_processing.repair_calls
    app_config.settings.frames_processing.repair_calls = 1
    app_config.settings.frames_processing.frames_per_call = 1
    try:
        update = process_frames_node.process_frames_node(state, _bind(ctx))
    finally:
        app_config.settings.frames_processing.frames_per_call = previous
        app_config.settings.frames_processing.repair_calls = previous_repairs

    return update


def _is_refused_by_the_jail(root: Path,
                            relative: str) -> bool:
    try:
        path_sandbox.resolve_within(root, relative)
    except SandboxError:
        return True

    return False


def test_a_new_run_state_carries_the_phase_range_it_was_asked_for():
    state = graph_state.new_run_state(2, 3, is_dry_run=True)
    harness.check("the range and the dry-run flag are in the state",
                  (state["first_phase"], state["last_phase"], state["is_dry_run"]) == (2, 3, True), str(state))
    harness.check("and the loop starts empty", (state["queue"], state["cursor"]) == ([], 0), str(state))


def test_a_phase_that_was_not_asked_for_is_read_from_its_file():
    ctx = _load_run_context()
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, _build_items(ctx))
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("phase 2 completes from disk", update["phases"]["frames_preparing"] == "complete", str(update))
    harness.check("and hands the loop the frames the file holds", update["queue"] == ["frame-000", "frame-001"],
                  str(update))


def test_a_frame_list_the_loop_needs_and_cannot_find_is_refused():
    ctx = _load_run_context()
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("`--from 3` with no frame list fails phase 2", update["phases"]["frames_preparing"] == "failed", str(update))
    harness.check("and the run stops there", routing.route_after_frames_preparing({**update, "first_phase": FRAMES_PROCESSING,
                                                                         "last_phase": FINALIZE}) == "failed")


def test_a_run_that_never_enters_the_loop_does_not_demand_the_frame_list():
    ctx = _load_run_context()
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FINALIZE, FINALIZE), _bind(ctx))
    harness.check("`--phase 4` is not refused for a file it would never read",
                  update == {"phases": {"frames_preparing": "complete"}, "queue": []}, str(update))


def test_a_resume_does_not_reset_the_frames_it_already_finished():
    ctx = _load_run_context()
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, _build_items(ctx))
    prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    ctx.store.set_item_status("frame-000", "written")
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("a second pass keeps the finished frame finished",
                  ctx.store.get_item("frame-000")["status"] == "written", str(ctx.store.list_items()))
    harness.check("and queues only what is left", update["queue"] == ["frame-001"], str(update))


def test_a_frame_named_by_worker_is_asked_again_though_it_was_accepted():
    """`--worker N` re-runs frame N and rewrites its entry; an accepted frame was dropped from the queue as finished,
    so a run asking accepted frames again with wrong notes asked nothing."""

    ctx = _load_run_context()
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, _build_items(ctx))
    prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    item = ctx.store.get_item("frame-000")
    frame_records_service.save_frame_record(ctx.paths, item, {**RECORD, "frame": 0}, [])
    ctx.store.set_item_status("frame-000", "written")
    ctx.store.set_item_status("frame-001", "written")
    ctx.selected_slugs = ["frame-000"]
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("the named frame is queued again", update["queue"] == ["frame-000"], str(update))
    harness.check("and the frame not named stays finished", ctx.store.get_item("frame-001")["status"] == "written",
                  str(ctx.store.list_items()))


def test_a_resume_asks_a_blocked_frame_though_phase_3_was_once_complete():
    """Frames named by `--worker` were asked again and blocked; the run still recorded phase 3 complete from its earlier
    pass, so a plain resume queued nothing and phase 4 began without those frames' notes (2026-10-06)."""

    ctx = _load_run_context()
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, _build_items(ctx))
    prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    ctx.store.set_item_status("frame-000", "blocked")
    ctx.store.set_item_status("frame-001", "written")
    ctx.store.set_phase("frames_processing", "complete")
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("the blocked frame is queued again", update["queue"] == ["frame-000"], str(update))


def test_a_worker_run_of_named_frames_never_records_phase_3_complete_while_others_are_unread():
    """A `--worker` run of five named frames recorded phase 3 complete while seven frames had never been read; a plain
    resume took the record, queued nothing and began phase 4 with 60 of 67 frames."""

    answer = fakes.frame_notes(**{**RECORD, "frame": 0})
    answer.response_metadata.update({"done_reason": "stop"})
    ctx = _load_run_context(fakes.scripted(answer))
    _seed_frames(ctx, _build_items(ctx)["items"])
    ctx.selected_slugs = ["frame-000"]
    state = {**graph_state.new_run_state(FRAMES_PROCESSING, FRAMES_PROCESSING), "queue": ["frame-000"]}
    state.update(_run_process_frames_node(state, ctx))
    harness.check("the named frame is written", state.get("written") == ["frame-000"], str(state))
    harness.check("phase 3 is not recorded complete while frame 1 was never read",
                  ctx.store.get_phase("frames_processing") != "complete", str(ctx.store.get_phase("frames_processing")))
    phase_rows = [row for row in performance_log_service.read_rows(ctx.run_dir) if row.get("level") == "phase"]
    harness.check("and its performance line says incomplete, never complete",
                  [row["status"] for row in phase_rows] == ["incomplete"], str(phase_rows))


def test_a_phase_3_recorded_blocked_whose_frames_are_all_written_is_complete():
    """The frames a block stopped were asked again and written; the run still recorded phase 3 blocked, and phase 5 was
    held for it after phase 4 published (2026-10-06)."""

    ctx = _load_run_context()
    # The phase closes as the frame loop closes it, its meeting notes written from the transcript
    phase_file_service.write_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["transcribe"],
                                       {"schemaVersion": 1, "hasAudio": True, "segments": [], "words": []})
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, _build_items(ctx))
    prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    for slug in ctx.store.list_items():
        ctx.store.set_item_status(slug, "written")
    ctx.store.set_phase("frames_processing", "blocked")
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("phase 3 is recorded complete, and nothing is queued",
                  ctx.store.get_phase("frames_processing") == "complete" and update["queue"] == [], str(update))
    ctx.store.set_phase("frames_processing", "blocked")
    ctx.store.set_item_status(next(iter(ctx.store.list_items())), "blocked")
    prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("a frame still blocked keeps it blocked", ctx.store.get_phase("frames_processing") == "blocked", "")


def test_a_resume_adopts_the_frames_whose_notes_are_on_disk_though_its_store_says_pending():
    ctx = _load_run_context()
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, _build_items(ctx))
    prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    item = ctx.store.get_item("frame-000")
    frame_records_service.save_frame_record(ctx.paths, item, {**RECORD, "frame": 0}, [])
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), _bind(ctx))
    harness.check("the frame whose notes are in the frames file is written, never asked again",
                  ctx.store.get_item("frame-000")["status"] == "written", str(ctx.store.list_items()))
    harness.check("and only the frame without notes is queued", update["queue"] == ["frame-001"], str(update))


def test_a_plain_resume_of_a_run_begun_at_phase_4_never_rebuilds_the_frames_or_asks_a_frame():
    import argparse
    import logging
    from cli import run_command
    ctx = _load_run_context()
    frames = _build_items(ctx)
    phase_file_service.write_phase_file(ctx.paths, output_contract.PHASE_NUMBERS["transcribe"],
                                       {"schemaVersion": 1, "hasAudio": True, "segments": [], "words": []})
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, frames)
    frame_records_service.save_frame_record(ctx.paths, {**frames["items"][0]}, {**RECORD, "frame": 0}, [])
    before = (ctx.paths.output_root / output_contract.LOOP_ITEMS_FILE).read_bytes()
    started = run_command._open_run(ctx.paths, False, False, logging.getLogger("t"))
    started.data["phases"] = {"frames_processing": "complete", "finalize": "blocked"}     # `--from 4`, over frame-001 left without notes
    started.save()
    store = run_command._open_run(ctx.paths, True, False, logging.getLogger("t"))
    ctx = dataclasses.replace(ctx, store=store)
    update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(1, FINALIZE), _bind(ctx))
    harness.check("phases 1 and 2 are taken from their files", store.get_phase("transcribe") == "complete"
                  and update["phases"]["frames_preparing"] == "complete", str(store.data["phases"]))
    harness.check("the frames file, notes and all, is left byte for byte",
                  (ctx.paths.output_root / output_contract.LOOP_ITEMS_FILE).read_bytes() == before)
    harness.check("no frame is queued: phase 3 stays as the run recorded it", update["queue"] == [], str(update))
    harness.check("the phase-4 block stands, so the resume goes on there", store.get_phase("finalize") == "blocked")


def test_the_frame_list_is_checked_before_anything_is_built_on_it():
    ctx = _load_run_context()
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, {**_build_items(ctx), "items": []})
    harness.check("an empty frame list is C_EMPTY",
                  "C_EMPTY" in harness.codes(loop_items_validator.validate_loop_items(ctx.paths)))
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, _build_items(ctx))
    harness.check("a real frame list passes", loop_items_validator.validate_loop_items(ctx.paths).ok)


def test_a_dry_run_of_one_frame_renders_its_prompt_and_calls_no_model():
    model = fakes.scripted(fakes.says("never asked"))
    ctx = _load_run_context(model)
    _seed_frames(ctx, _build_items(ctx)["items"])
    state = {**graph_state.new_run_state(FRAMES_PROCESSING, FRAMES_PROCESSING, is_dry_run=True), "queue": ["frame-000"]}
    update = _run_process_frames_node(state, ctx)
    harness.check("the cursor advances", update == {"cursor": 1}, str(update))
    harness.check("the model was not called", model.call_count == 0, f"calls={model.call_count}")
    rendered = (ctx.run_dir / "dry-run" / "frames_processing" / "frame-000" / "human-message.md").read_text(encoding="utf-8")
    harness.check("the rendered message names the frame and its speech",
                  "frame 0" in rendered and "Guten Abend." in rendered, rendered)
    harness.check("and names the frame by its size, never its bytes", "[image:" in rendered, rendered)


def test_an_accepted_frame_joins_the_records_file_and_the_next_frame_is_shown_its_notes():
    first = fakes.frame_notes(**{**RECORD, "frame": 0})
    second = fakes.frame_notes(**RECORD)
    for message in (first, second):
        message.response_metadata.update({"done_reason": "stop"})
    ctx = _load_run_context(fakes.scripted(first, second))
    _seed_frames(ctx, _build_items(ctx)["items"])
    state = {**graph_state.new_run_state(FRAMES_PROCESSING, FRAMES_PROCESSING), "queue": ["frame-000", "frame-001"]}
    state.update(_run_process_frames_node(state, ctx))
    harness.check("frame 0 is written", state.get("written") == ["frame-000"], str(state))
    state.update(_run_process_frames_node(state, ctx))
    records = frame_records_service.read_frame_records(ctx.paths)
    harness.check("both frames have their notes in 02_frames.json, in order",
                  [entry["slug"] for entry in records] == ["frame-000", "frame-001"], str(records))
    harness.check("frame 1 records that it was built from frame 0", records[1]["builtFrom"] == ["frame-000"], str(records[1]))
    harness.check("and frame 1's message carried frame 0's notes",
                  "Frame 0" in ctx.model.calls[1][1].content[0]["text"], ctx.model.calls[1][1].content[0]["text"])
    notes_path = ctx.paths.output_root / output_contract.NOTES_FILE
    harness.check("phase 3 writes the merged meeting notes after its last frame",
                  notes_path.is_file() and "89 % of voters want cheaper fuel" in notes_path.read_text(encoding="utf-8"),
                  str(notes_path))


def test_the_call_node_logs_the_call_and_writes_the_record():
    answer = fakes.frame_notes(**{**RECORD, "frame": 0})
    answer.response_metadata.update({"done_reason": "stop"})
    ctx = _load_run_context(fakes.scripted(answer))
    item = _build_items(ctx)["items"][0]
    job = JobRetryContext(job_kind="frames_processing", phase="frames_processing", snapshot_targets=[],
                          inputs={"slug": "frame-000", "item": item, "previousRecords": [], "builtFrom": []},
                          handles=JobRetryHandles(log=attempt_log_service.claim_attempt_dir(ctx.run_dir, "frames_processing",
                                                                                          "frame-000", 1)))
    update = invoke_chain_node.invoke_chain_node({"slug": "frame-000", "attempt": 1, "findings": []},
                                                 {"configurable": {"ctx": ctx, "job": job}})
    harness.check("the node made one model call and carries nothing over", ctx.model.call_count == 1
                  and update == {"carry_over": ""}, str(update))
    logged = call_log_service.read_call_log(call_log_service.build_call_log_path(ctx.paths, "frame-000"))
    harness.check("its call log holds the messages it was sent, rendered as the attempt's own files are",
                  (logged["request"]["system"], logged["request"]["human"])
                  == chain_response_utils.render_prompt_as_text(ctx.model.calls[0]), str(logged["request"])[:300])
    harness.check("the record is on disk", record_file_service.read_frame_record(ctx.paths, "frame-000")["screen"]
                  == RECORD["screen"])
    harness.check("as the markdown document the model writes",
                  record_file_service.build_record_path(ctx.paths, "frame-000").read_text(encoding="utf-8")
                  .startswith("# Frame 0 · slide\n"))
    harness.check("the call log names how the reply ended",
                  call_log_service.read_call_log(call_log_service.build_call_log_path(ctx.paths, "frame-000"))
                  ["response"]["finishReason"] == "stop")
    harness.check("the job's handles carry the result the record node reads",
                  isinstance(job.handles.result, ChainCallResult) and job.handles.result.as_dict()["modelCalls"] == 1)


def test_the_chain_subgraph_is_wired_as_designed():
    ctx = _load_run_context()
    job = JobRetryContext(job_kind="frames_processing", phase="frames_processing", snapshot_targets=[])
    rendered = chain_attempt_loop_graph.build_chain_attempt_graph(ctx, job).get_graph().draw_mermaid()
    for node in ("snapshot", "invoke_chain", "capture", "validate", "record"):
        harness.check(f"the chain subgraph has a `{node}` node", node in rendered, rendered)


class Note(BaseModel):
    """A structure the runner is asked for: every phase but phase 3 still answers through one."""

    frame: int
    screen_kind: str


NOTE = {"frame": 1, "screen_kind": "slide"}


def test_a_reply_that_parsed_to_nothing_is_named_not_passed_on():
    reply = chain_runner.invoke_structured_chain(fakes.scripted(fakes.says("prose")), Note,
                                                 "function_calling", [HumanMessage(content="x")])
    harness.check("nothing parsed", reply.parsed is None, str(reply))
    harness.check("and the reason is stated in words", bool(reply.parsing_error), str(reply))


def test_a_slug_that_climbs_out_of_the_output_root_is_refused():
    paths = fx.make_paths()
    for build in (record_file_service.build_record_path, call_log_service.build_call_log_path):
        try:
            build(paths, "../../outside")
        except SandboxError:
            harness.check(f"{build.__name__} refuses `../`", True)
        else:
            harness.check(f"{build.__name__} refuses `../`", False)
    harness.check("the jail itself refuses an absolute path outside its root",
                  _is_refused_by_the_jail(paths.output_root, "/etc/passwd"))
    harness.check("an ordinary slug stays under the output root",
                  record_file_service.build_record_path(paths, "frame-001").is_relative_to(paths.output_root.resolve()))


def test_the_setup_commands_are_the_engines_own():
    harness.check("`models` and `probe` are the engine's handlers",
                  callable(setup_commands.cmd_models) and callable(setup_commands.cmd_probe))


def _ask_for_frame_record(reply_text: str) -> dict:
    return chain_runner.invoke_structured_chain(fakes.scripted(fakes.says(reply_text)), Note,
                                                "function_calling", [HumanMessage(content="x")])


def test_a_function_call_the_host_returned_as_text_is_still_the_answer():
    """Measured on this machine, 2026-09-26: `qwen3-vl:8b` behind Ollama wrote a complete, correct FrameRecord call
    into the reply's TEXT, nothing read it, and the attempt was rejected as MODEL_NO_STRUCTURED_OUTPUT. The profile
    had the fix; this playbook had not been given it."""

    reply = _ask_for_frame_record(json.dumps({"name": "Note", "arguments": NOTE}))
    harness.check("the call is read out of the text", reply.parsed == Note(**NOTE), str(reply))
    harness.check("and the reply says which way the answer came", reply.reply_channel == chain_reply_result.TEXT_CHANNEL, str(reply))
    harness.check("with no parsing error left standing", reply.parsing_error == "", str(reply))


def test_text_that_only_resembles_a_function_call_is_not_an_answer():
    for label, text in (("another function's name", json.dumps({"name": "SomethingElse", "arguments": NOTE})),
                        ("arguments the schema refuses",
                         json.dumps({"name": "Note", "arguments": {**NOTE, "frame": "not a number"}})),
                        ("a bare record with no call around it", json.dumps(NOTE)),
                        ("prose that mentions the function", 'I would call Note with {"frame": 1}')):
        reply = _ask_for_frame_record(text)
        harness.check(f"{label} parses to nothing", reply.parsed is None, str(reply))
        harness.check(f"{label} keeps a parsing error in words", bool(reply.parsing_error), str(reply))


def test_a_real_function_call_says_it_came_as_one():
    answer = fakes.tool_call("Note", **NOTE)
    reply = chain_runner.invoke_structured_chain(fakes.scripted(answer), Note,
                                                 "function_calling", [HumanMessage(content="x")])
    harness.check("a structured call is reported as one", reply.reply_channel == chain_reply_result.TOOL_CALL_CHANNEL, str(reply))

    harness.check("and the reply is a ChainReplyResult", isinstance(reply, chain_reply_result.ChainReplyResult), type(reply).__name__)


def test_a_reply_the_output_limit_ended_is_read_as_cut_under_either_host_spelling():
    for key in finish_reason_utils.FINISH_KEYS:
        message = fakes.says("half a thought")
        message.response_metadata.update({key: finish_reason_utils.CUT_AT_OUTPUT_LIMIT})
        harness.check(f"`{key}: length` is cut at the output limit", finish_reason_utils.is_reply_cut_at_output_limit(message))
    finished = fakes.says("done")
    finished.response_metadata.update({"finish_reason": "stop"})
    harness.check("`stop` is not", not finish_reason_utils.is_reply_cut_at_output_limit(finished))

def test_a_frame_answered_in_markdown_is_read_by_the_program():
    """Phase 3 asks for no structure: the reply's text is the notes."""

    read = chain_dispatch_service.MARKDOWN_READERS["frames_processing"]
    reply = chain_runner.invoke_markdown_chain(fakes.scripted(fakes.frame_notes(**{**RECORD, "frame": 0})),
                                               read, [HumanMessage(content="x")])
    harness.check("the notes are read out of the text", reply.parsed == {**RECORD, "frame": 0}, str(reply))
    harness.check("and the reply says it came as markdown", reply.reply_channel == chain_reply_result.MARKDOWN_CHANNEL, str(reply))
    prose = chain_runner.invoke_markdown_chain(fakes.scripted(fakes.says("I looked at the slide.")), read,
                                               [HumanMessage(content="x")])
    harness.check("prose with no `# Frame N` document is no answer, and says why",
                  prose.parsed is None and "# Frame N" in prose.parsing_error, str(prose))
    two = chain_runner.invoke_markdown_chain(
        fakes.scripted(fakes.frame_notes(frames=[{**RECORD, "frame": 0}, {**RECORD, "frame": 1}])), read,
        [HumanMessage(content="x")])
    harness.check("two frames where one was asked is no answer either", two.parsed is None
                  and "write one" in two.parsing_error, str(two))


class _StreamingModel(BaseChatModel):
    """Streams a reply in pieces as LM Studio does: thinking first, then the text, the end and the counts last."""

    pieces: list = []

    @property
    def _llm_type(self) -> str:
        return "streaming-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("a markdown call is streamed, never asked whole")

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for piece in self.pieces:
            yield ChatGenerationChunk(message=piece)


def test_an_answer_written_as_a_fenced_block_ending_the_thinking_is_read_defect():
    # A batch's reply ended of itself with an empty answer, its whole document written as a fenced block that ended
    # the thinking, and ten frames were blocked as "thinking only". That block is read as the answer now
    notes = fakes.frame_notes(**{**RECORD, "frame": 0}).content
    thinking = f"Frame 0 shows the slide.\nDraft:\n```markdown\n{notes}\n```"
    read = chain_dispatch_service.MARKDOWN_READERS["frames_processing"]
    ended = chain_runner.invoke_markdown_chain(_StreamingModel(pieces=[
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": thinking}),
        AIMessageChunk(content="", response_metadata={"finish_reason": "stop"})]), read, [HumanMessage(content="x")])
    harness.check("the block ending the thinking is the answer", ended.parsed == {**RECORD, "frame": 0}, str(ended.parsed))
    harness.check("and the reply says where it came from", ended.reply_channel == chain_reply_result.THINKING_CHANNEL,
                  ended.reply_channel)
    cut = chain_runner.invoke_markdown_chain(_StreamingModel(pieces=[
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": thinking}),
        AIMessageChunk(content="", response_metadata={"finish_reason": "length"})]), read, [HumanMessage(content="x")])
    harness.check("a reply cut at its output limit is no answer, whatever its thinking holds", cut.parsed is None, str(cut.parsed))
    drafting = chain_runner.invoke_markdown_chain(_StreamingModel(pieces=[
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": thinking + "\nNow check the arrow again."}),
        AIMessageChunk(content="", response_metadata={"finish_reason": "stop"})]), read, [HumanMessage(content="x")])
    harness.check("a draft the thinking goes on past is no answer", drafting.parsed is None, str(drafting.parsed))


def test_a_markdown_call_is_streamed_and_its_pieces_join_into_one_reply():
    """Unstreamed, a follow-up thinking past the client's timeout was closed and asked again from nothing, its
    thinking lost (2026-10-12); streamed, it ends cut at its output limit with every piece kept."""

    model = _StreamingModel(pieces=[
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "Check the arrow. "}),
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "It points down."}),
        AIMessageChunk(content="I looked", response_metadata={"finish_reason": "length"},
                       usage_metadata={"input_tokens": 900, "output_tokens": 40, "total_tokens": 940})])
    reply = chain_runner.invoke_markdown_chain(model, chain_dispatch_service.MARKDOWN_READERS["frames_processing"],
                                               [HumanMessage(content="x")])
    harness.check("the thinking of every piece is kept, in order",
                  message_content_utils.read_reasoning(reply.raw) == "Check the arrow. It points down.", str(reply.raw))
    harness.check("the reply says it was cut at the output limit",
                  finish_reason_utils.is_reply_cut_at_output_limit(reply.raw), str(reply.raw.response_metadata))
    harness.check("and keeps its token counts",
                  chain_response_utils.read_token_usage(reply.raw)["output_tokens"] == 40, str(reply.raw))


def test_a_streamed_thinking_is_written_as_it_comes_and_a_loop_is_ended_as_cut():
    """A critique streamed for over two hours unseen (2026-10-13): its thinking now lands in the attempt folder while it
    streams, and a thinking that repeats one passage is ended and reported cut, which the caller compacts."""

    passage = "Check the arrow on the slide again, it points from the gateway to the provider, so the note is right. " * 5
    pieces = [AIMessageChunk(content="", additional_kwargs={"reasoning_content": passage}) for _ in range(40)]
    pieces.append(AIMessageChunk(content="never reached", response_metadata={"finish_reason": "stop"}))
    live = Path(tempfile.mkdtemp()) / "thinking-live.md"
    reply = chain_runner.invoke_markdown_chain(_StreamingModel(pieces=pieces),
                                               chain_dispatch_service.MARKDOWN_READERS["frames_processing"],
                                               [HumanMessage(content="x")], live)
    harness.check("the loop is ended before the stream's end", "never reached" not in str(reply.raw.content), str(reply.raw.content))
    harness.check("and reported cut at the output limit, so it is compacted",
                  finish_reason_utils.is_reply_cut_at_output_limit(reply.raw), str(reply.raw.response_metadata))
    harness.check("the thinking so far is on disk, with why it stopped",
                  live.read_text(encoding="utf-8").endswith(chain_runner.LOOP_NOTE), live.read_text(encoding="utf-8")[-200:])
    varied = "".join(f"Frame {n} shows the {n}th retry setting with its own value. " for n in range(2000))
    harness.check("a long thinking that does not repeat is no loop", not chain_runner.is_thinking_looping(varied))
    outline = "  - bullet \"Start with a guess, g\"\n    - sub-bullet \"If g*g is close enough to x, stop\"\n" * 6
    drafts = "".join(f"Frame {n} shows the same slide again.\n{outline}" for n in range(4))
    harness.check("one slide's outline drafted once per frame of a batch and once more is no loop (2026-10-13)",
                  not chain_runner.is_thinking_looping(drafts))
    chrome = "- side panel: left, light grey\n  - heading \"Page tree\"\n  - entry \"Partner onboarding\"\n" * 4
    pages = "".join(f"**Frame {n}: section {n}**\n- Facts: the {n}th section holds {n * 7} settings.\n{chrome}" for n in range(8))
    harness.check("a wiki's chrome outlined on each of 8 pages, the pages' own text between, is no loop (pdf-04)",
                  not chain_runner.is_thinking_looping(pages))


def test_an_answer_whose_own_text_repeats_is_ended_as_a_loop():
    """A chapter wrote its draft about 70 times over in its answer, with little thinking, to its output limit
    (pdf-figures-05-manual): the loop check read the thinking alone."""

    draft = ("### Installing the gateway CLI (T01.1)\n\nThe CLI talks to the gateway's admin API and needs Python 3.11 or "
             "later. [S0002]\n\n```text\npip install acmesync-gateway-cli==2.15.0\n```\n[S0003]\n\n")
    pieces = [AIMessageChunk(content=draft) for _ in range(60)]
    pieces.append(AIMessageChunk(content="never reached", response_metadata={"finish_reason": "stop"}))
    reply = chain_runner.read_streamed_reply(_StreamingModel(pieces=pieces), [HumanMessage(content="x")])
    harness.check("the repeating answer is ended before the stream's end and reported cut",
                  "never reached" not in str(reply.content) and finish_reason_utils.is_reply_cut_at_output_limit(reply),
                  str(reply.response_metadata))
    honest = "".join(f"### Step {n} (T01.{n})\n\nStep {n} sets the {n}th limit of the gateway to {n * 3} calls. [S{n:04d}]\n\n"
                     for n in range(1, 400))
    harness.check("a long answer that does not repeat runs to its end", "never reached" in str(chain_runner.read_streamed_reply(
        _StreamingModel(pieces=[AIMessageChunk(content=honest), AIMessageChunk(content="never reached")]),
        [HumanMessage(content="x")]).content))


def test_an_answer_written_into_a_tool_call_that_repeats_is_ended_as_a_loop():
    """A chapter's answer goes into its submit tool's arguments, which stream apart from the text: the wiki's chapter
    wrote itself over and over there, unseen by a check that read the thinking and the text alone."""

    draft = ("### Discovery (T08.1)\n\nVan Helmont weighed the soil before and after the tree grew. [S0086]\n\n"
             "Priestley kept a mouse alive beside a plant. [S0087]\n\n")
    pieces = [AIMessageChunk(content="", tool_call_chunks=[{"name": "submit_chapter", "args": '{"markdown": "', "id": "c1", "index": 0}])]
    pieces += [AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": draft, "id": None, "index": 0}]) for _ in range(60)]
    pieces.append(AIMessageChunk(content="never reached", response_metadata={"finish_reason": "stop"}))
    reply = chain_runner.read_streamed_reply(_StreamingModel(pieces=pieces), [HumanMessage(content="x")])
    harness.check("the repeating arguments are ended before the stream's end and reported cut",
                  "never reached" not in str(reply.content) and finish_reason_utils.is_reply_cut_at_output_limit(reply),
                  str(reply.response_metadata))


def test_a_return_to_an_earlier_screen_is_asked_with_the_notes_and_words_of_its_first_showing():
    first = fakes.frame_notes(**{**RECORD, "frame": 0})
    first.response_metadata.update({"done_reason": "stop"})
    second = fakes.frame_notes(**{**RECORD, "frame": 1})
    second.response_metadata.update({"done_reason": "stop"})
    ctx = _load_run_context(fakes.scripted(first, second))
    items = _build_items(ctx)["items"]
    items[1]["repeatOf"] = "frame-000"
    _seed_frames(ctx, items)
    state = {**graph_state.new_run_state(FRAMES_PROCESSING, FRAMES_PROCESSING), "queue": ["frame-000", "frame-001"]}
    state.update(_run_process_frames_node(state, ctx))
    state.update(_run_process_frames_node(state, ctx))
    asked = " ".join(str(part.get("text", "")) for part in ctx.model.calls[-1][1].content if isinstance(part, dict))
    harness.check("the return is asked: one call per frame", ctx.model.call_count == 2, f"calls={ctx.model.call_count}")
    harness.check("its message names the earlier showing", "This screen was shown before, as frame 0" in asked, asked[-1500:])
    harness.check("and carries that showing's notes", RECORD["facts"][0] in asked, asked[-1500:])
    harness.check("the return's notes are its own answer, not a copy",
                  ctx.store.get_item("frame-001").get("status") == "written"
                  and not ctx.store.get_item("frame-001").get("reusedFrom"), str(ctx.store.get_item("frame-001")))


def test_a_repeat_whose_original_has_no_record_is_asked_like_any_frame():
    answer = fakes.frame_notes(**RECORD)
    answer.response_metadata.update({"done_reason": "stop"})
    ctx = _load_run_context(fakes.scripted(answer))
    items = _build_items(ctx)["items"]
    items[1]["repeatOf"] = "frame-000"
    _seed_frames(ctx, items)
    state = {**graph_state.new_run_state(FRAMES_PROCESSING, FRAMES_PROCESSING), "queue": ["frame-001"]}
    state.update(_run_process_frames_node(state, ctx))
    harness.check("with no accepted original, the model is asked", ctx.model.call_count == 1,
                  f"calls={ctx.model.call_count}")
    harness.check("and its own answer is written", state.get("written") == ["frame-001"], str(state))


def test_a_batch_with_nothing_to_ask_alone_repairs_nothing():
    ctx = _load_run_context()
    result = process_frames_node._process_single_frames(ctx, [])
    harness.check("an empty repair result", result == FrameRepairResult(), str(result))


def test_a_batch_with_its_critique_off_keeps_its_account_and_blocks_nothing():
    ctx = _load_run_context(fakes.scripted())
    with fx.critique_switches(is_critique_on=False, is_critique_repair_on=True):
        reviewed = process_frames_node._run_critique_and_second_pass(ctx, "frames-000-001", [], "The account.", "Calm.")
    harness.check("the account and mood stand, and no follow-up blocked",
                  reviewed == BatchFollowUpResult(summary="The account.", atmosphere="Calm."), str(reviewed))


def test_a_two_frame_batch_keeps_a_two_sentence_account_its_second_pass_rewrote():
    """The second pass's account was judged without the batch's frames, so the sentence minimum rose from one a frame
    to three, and a 2-frame batch's rewrite in 2 sentences was dropped."""

    ctx = _load_run_context(fakes.scripted())
    covered = [{"slug": "frame-000", "index": 0}, {"slug": "frame-001", "index": 1}]
    rewrite = {"batch_title": "Refund limits for team leads",
               "batch_summary": "The presenter shows the refund limit. A team lead may raise it.", "atmosphere": "Calm."}
    summary, _atmosphere = process_frames_node._keep_better_account(
        ctx, "frames-000-001", covered, rewrite, "The presenter shows a slide. Then another.", "Calm.")
    harness.check("the rewrite in one sentence a frame is kept", summary == rewrite["batch_summary"], summary)


def test_a_phase_2_that_fails_marks_its_running_stage_failed():
    """Phase 2 closed its stages only on finishing them: a classifier command missing raised inside the classifier
    stage, and the page showed it running, its clock growing, under a failed phase. Phase 1 already marked its own."""

    from services import frame_preparation_service
    from services import frames_preparing_progress_service as progress_service

    ctx = _load_run_context()

    def _fail_in_the_classifier(run_ctx):
        progress_service.open_progress(run_ctx.run_dir, [progress_service.RULES, progress_service.CLASSIFIER])
        progress_service.start_stage(run_ctx.run_dir, progress_service.RULES, None)
        progress_service.finish_stage(run_ctx.run_dir, progress_service.RULES, 3, run_ctx.log)
        progress_service.start_stage(run_ctx.run_dir, progress_service.CLASSIFIER, 3)
        raise FileNotFoundError("frames.knowledgeClassifierCommand points at a missing file")

    original = frame_preparation_service.prepare_frames
    frame_preparation_service.prepare_frames = _fail_in_the_classifier
    try:
        update = prepare_frames_node.prepare_frames_node(graph_state.new_run_state(FRAMES_PREPARING, FINALIZE), _bind(ctx))
    finally:
        frame_preparation_service.prepare_frames = original
    stages = {stage["name"]: stage["status"] for stage in progress_service.read_progress(ctx.run_dir)}
    harness.check("the phase fails, its finished stage done and its running one failed",
                  update["phases"]["frames_preparing"] == "failed"
                  and stages == {progress_service.RULES: "done", progress_service.CLASSIFIER: "failed"}, str(stages))


def test_a_thinking_that_counts_its_passes_is_a_loop_defect():
    # Defect: a critique wrote "859. Let's look at frame 79 again" through "860." and on for 332,000 characters and was
    # never ended: each repeat differed from the last in its number. Fix: numbers are compared as one.
    passage = ("{n}. **Let's look at frame 79's take-aways again.**\n     - Quote: the speaker closes with a drone flight "
               "over the whole facility\n     - Mistake: the screen does not show the video.\n\n")
    looping = "Reading the batch first.\n" + "".join(passage.format(n=n) for n in range(1, 200))
    harness.check("the counted repeats are a loop", chain_runner.is_thinking_looping(looping))
    honest = "".join(f"Frame {n}: the slide titled 'Step {n}' lists {n * 3} new points of the gateway's refund rules, "
                     f"and its speech adds the budget for client {n}.\n" for n in range(1, 40))
    harness.check("a thinking that drafts each frame once is none", not chain_runner.is_thinking_looping(honest))


def test_a_streamed_answer_is_written_to_the_live_file_as_it_comes_defect():
    # A chapter's answer streamed 25 minutes after its thinking ended, and the live file still held the thinking alone,
    # so nobody could see what it wrote (DEFECTS 365). The answer so far now stands under the thinking
    thinking = [AIMessageChunk(content="", additional_kwargs={"reasoning_content": "Plan the Gateway section first. "})]
    answer = "".join(f"The Gateway sends batch {index * 13} to AcmeSync node {index % 7}. [F{index}.1]\n" for index in range(80))
    pieces = thinking + [AIMessageChunk(content="", tool_call_chunks=[{"name": "submit_chapter", "args": answer[start:start + 500],
                                                                       "id": "c1" if start == 0 else None, "index": 0}])
                         for start in range(0, len(answer), 500)]
    live = Path(tempfile.mkdtemp()) / "thinking-live.md"
    chain_runner.read_streamed_reply(_StreamingModel(pieces=pieces), [HumanMessage(content="x")], live)
    written = live.read_text(encoding="utf-8") if live.is_file() else ""
    harness.check("the live file holds the thinking and the answer so far",
                  "Plan the Gateway section first." in written and "AcmeSync node" in written, written[-300:])


def test_a_draft_written_again_and_again_with_two_closings_is_a_loop_defect():
    # A chapter's answer drafted itself 11 times in its tool call, each copy closing with one of two sentences, and ran
    # 30 minutes: the passage test needs one text between copies (DEFECTS 361); recent text written before now is a loop
    from chains import chain_runner

    draft = "".join(f"The Gateway sends batch {index * 13} to AcmeSync node {index % 7} once Jonas signs it. [F{index}.1]\n"
                    for index in range(70))
    closings = ("I'll proceed with these cuts and submit the chapter.\n\n", "I will now submit this using the tool.\n\n")
    # Each copy about as long as the real one's 5,500 characters, two copies wider than a sixth of the passage test's
    # span, and streamed in small pieces as a host streams them
    answer = "".join(draft + closings[copy % 2] for copy in range(14))
    pieces = [AIMessageChunk(content="", tool_call_chunks=[{"name": "submit_chapter", "args": '{"markdown": "', "id": "c1", "index": 0}])]
    pieces += [AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": answer[start:start + 300], "id": None, "index": 0}])
               for start in range(0, len(answer), 300)]
    pieces.append(AIMessageChunk(content="never reached", response_metadata={"finish_reason": "stop"}))
    reply = chain_runner.read_streamed_reply(_StreamingModel(pieces=pieces), [HumanMessage(content="x")])
    harness.check("the drafts with two closings are ended before the stream's end and reported cut",
                  "never reached" not in str(reply.content) and finish_reason_utils.is_reply_cut_at_output_limit(reply),
                  str(reply.response_metadata))
    honest = "".join(f"Frame {index}: the Gateway's box holds row {index * 7} of AcmeSync, checked once by Jonas.\n"
                     for index in range(900))
    harness.check("a long text that says something new on every line is no loop",
                  not chain_runner.is_thinking_looping(honest), str(len(honest)))


def test_a_chapter_draft_repeating_half_of_its_text_is_ended_defect():
    # Defect: a chapter answer wrote its draft four times with the request's lines in it, 72% of its recent text old,
    # under the 97% the loop check stops at, and ran 25 minutes (DEFECTS 599). A tool call's arguments stop at 50%
    from chains import chain_runner

    draft = "".join(f"The Gateway sends batch {index * 13} to AcmeSync node {index % 7} once Jonas signs it. [F{index}.1]\n\n"
                    for index in range(150))
    # Past the check's 12,000-character window: the draft, then most of it again, then a few new lines
    answer = draft + draft[: len(draft) * 4 // 5] + "".join(f"Row {index}: a new fact about the AcmeSync relay. [F{index}.2]\n\n"
                                                         for index in range(20))
    pieces = [AIMessageChunk(content="", tool_call_chunks=[{"name": "submit_chapter", "args": '{"markdown": "', "id": "c1", "index": 0}])]
    pieces += [AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": answer[start:start + 300], "id": None, "index": 0}])
               for start in range(0, len(answer), 300)]
    pieces.append(AIMessageChunk(content="never reached", response_metadata={"finish_reason": "stop"}))
    reply = chain_runner.read_streamed_reply(_StreamingModel(pieces=pieces), [HumanMessage(content="x")])
    harness.check("a draft written over again in its arguments is ended and reported cut",
                  "never reached" not in str(reply.content) and finish_reason_utils.is_reply_cut_at_output_limit(reply),
                  str(reply.response_metadata))
    text = [AIMessageChunk(content=answer[start:start + 300]) for start in range(0, len(answer), 300)]
    text.append(AIMessageChunk(content="", response_metadata={"finish_reason": "stop"}))
    reply = chain_runner.read_streamed_reply(_StreamingModel(pieces=text), [HumanMessage(content="x")])
    harness.check("the same text in a reply's own text keeps the thinking's bar", not finish_reason_utils.is_reply_cut_at_output_limit(reply),
                  str(reply.response_metadata))


def test_a_thinking_going_round_one_checklist_is_a_loop_once_a_line_stands_thirty_times_defect():
    # A critique went through its list of common mistakes 42 times, each round's text between the repeated lines its
    # own, and the passage test ended it only at 84,000 characters (DEFECTS 340); one line standing 30 times now does
    from chains import chain_runner

    def rounds(count: int) -> str:
        return "".join(f"Let's look for \"A label on the screen nobody quoted\".\n    Frame {index}: the Gateway's "
                       f"box, checked {index} times, AcmeSync row {index * 7}.\n" for index in range(count))

    harness.check("25 rounds are no loop", not chain_runner.is_thinking_looping(rounds(25)))
    harness.check("30 rounds are a loop", chain_runner.is_thinking_looping(rounds(30)))

