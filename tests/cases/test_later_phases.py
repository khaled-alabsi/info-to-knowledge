"""The merge and phase 4, each called directly. Phase 4 is driven by a scripted model, so the whole
path runs offline: the outline call, one section call, the assembled document and its validator.
"""

import logging
import tempfile
from pathlib import Path

from support import fakes
from support import fixtures as fx
from support import harness
from support import section_fixtures
from support import write_mode

import config as app_config
from graph import graph_state
from graph.nodes import write_document_node
from models import output_contract
from models.instruction_errors import UnknownInstructionFileError
from models.pipeline_errors import MissingPhaseInput
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import frame_records_service
from services import document_file_service
from services import integration_service
from services import phase_file_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import document_plan_utils
from utils import instruction_example_utils
from utils import meeting_notes_utils
from validators import transcript_file_validator

TRANSCRIBE, FRAMES_PREPARING, FINALIZE = (
    output_contract.PHASE_NUMBERS[name] for name in ("transcribe", "frames_preparing", "finalize"))
TRANSCRIPT = {"hasAudio": True, "segments": [{"text": " 89 Prozent wollen billigeren Sprit.", "start": 1.0, "end": 4.0,
                                              "words": []}]}
FRAMES = {"schemaVersion": 1, "items": [{"slug": "frame-000", "index": 0, "timestamp": 2.0}]}
RECORDS = {"schemaVersion": 1, "records": [
    {"slug": "frame-000", "index": 0, "timestamp": 2.0, "frame": 0, "screen_kind": "slide",
     "facts": ["89 % of voters want cheaper fuel"], "diagram": 'flowchart LR\n    A["Poll"] --> B["89 %"]',
     "screen": '- slide: white, the whole frame\n  - text "89 %": centre', "speech_visual_alignment": "the graphic carries the figure being read out",
     "builds_on": [], "contradicts_earlier": [], "open_questions": ["who ran the poll"]}]}
# The keys a frame item carries, which the model's record does not
ITEM_KEYS = ("slug", "index", "timestamp")


def _make_run_context(model=None) -> RunContext:
    repository = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=repository, module="clip.mp4", output_root_override=repository / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = repository / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(repository), module="clip.mp4",
                                        output_root=str(paths.output_root)), budgets={"maxAttempts": 2}, models=[])

    return RunContext(repository=repository, module="clip.mp4", paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(),
                      model=model)


def _bind(ctx: RunContext) -> dict:
    return {"configurable": {"ctx": ctx}}


def _strip_item_keys(record: dict) -> dict:
    """A record as the frame model answers it: without the keys its frame item carries."""

    return {key: value for key, value in record.items() if key not in ITEM_KEYS}


def _write_frame_inputs(ctx: RunContext,
                        records: list[dict]) -> None:
    phase_file_service.write_phase_file(ctx.paths, TRANSCRIBE, TRANSCRIPT)
    phase_file_service.write_phase_file(ctx.paths, FRAMES_PREPARING, FRAMES)
    for item, record in zip(FRAMES["items"], records):
        frame_records_service.save_frame_record(ctx.paths, item, _strip_item_keys(record), [])


def _write_inputs(ctx: RunContext) -> None:
    _write_frame_inputs(ctx, RECORDS["records"])
    document_file_service.write_meeting_notes(
        ctx.paths, meeting_notes_utils.render_meeting_notes(
            ctx.module, integration_service.build_integration(ctx.paths)))


def test_layer_2_is_merged_in_memory_from_the_three_files_on_disk():
    ctx = _make_run_context()
    _write_inputs(ctx)
    merged = integration_service.build_integration(ctx.paths)
    harness.check("speech, facts, the diagram, their relation and the open question are entries",
                  {entry["kind"] for entry in merged["entries"]}
                  >= {"speech", "facts", "diagram", "speech_visual_alignment", "open_question"},
                  str(merged["entries"]))
    harness.check("and the screen's outline stays on its frame", merged["frames"][0]["screen"].startswith("- slide:"),
                  str(merged["frames"][0]))
    harness.check("the same files give the same entries, ids included, every time",
                  integration_service.build_integration(ctx.paths)["entries"] == merged["entries"])


def test_a_line_copied_from_the_instructions_sample_is_no_entry_and_no_other_id_moves():
    """Frames accepted before phase 3 refused a copied sample line kept one: an invented question about a person never
    in the meeting reached the writer as the meeting's own (2026-10-02). The merge drops it after numbering."""

    ids = {}
    for label, first in (("copied", "The unit of the retry delay is not said."), ("own", "who paid for the poll"),
                         ("empty", "(none)")):
        ctx = _make_run_context()
        _write_frame_inputs(ctx, [{**RECORDS["records"][0], "open_questions": [first, "who ran the poll"]}])
        ids[label] = {entry["id"]: entry["text"] for entry in integration_service.build_integration(ctx.paths)["entries"]}
    dropped = set(ids["own"]) - set(ids["copied"])
    harness.check("the copied line is gone", "The unit of the retry delay is not said." not in ids["copied"].values()
                  and len(dropped) == 1, str(ids["copied"]))
    harness.check("every other entry keeps its id", all(ids["copied"][key] == ids["own"][key] for key in ids["copied"]),
                  str(ids))
    harness.check("a note that is only \"(none)\" is no entry either (2026-10-02: one reached the writer as a fact)",
                  "(none)" not in ids["empty"].values() and set(ids["empty"]) == set(ids["copied"]), str(ids["empty"]))


def test_the_merge_refuses_by_name_when_an_input_file_is_missing():
    ctx = _make_run_context()
    phase_file_service.write_phase_file(ctx.paths, TRANSCRIBE, TRANSCRIPT)
    try:
        integration_service.build_integration(ctx.paths)
        refusal = ""
    except MissingPhaseInput as exc:
        refusal = str(exc)
    harness.check("a missing frame list is refused by name, never read as no frames",
                  output_contract.LOOP_ITEMS_FILE in refusal, refusal)


def _script_document_model(entry_ids: list[str],
                            section_text: str,
                            section_attempts: int):
    """The parts, the outline, the title, the section once per attempt it is given, then the summary."""

    parts = fakes.tool_call("DocumentParts", announced_agenda=[],
                            points=[{"title": "Fuel prices", "first_moment": 1, "covers_announced": []}])
    outline = fakes.tool_call("DocumentOutline", topics=[{"title": "Fuel", "first_entry_id": entry_ids[0]}],
                              set_aside_entry_ids=[], not_established=["Whether prices keep rising."])
    contents = fakes.tool_call("DocumentContents", title="Fuel prices decide the vote")
    section = fakes.tool_call("DocumentSection", **section_fixtures.build_section_answer(
        {"paragraphs": [{"text": section_text, "entry_ids": entry_ids}]}))
    summary = fakes.tool_call("DocumentSummary", agenda=[{"part_number": 1, "text": "Discusses fuel prices"}],
                              summary=[{"text": "Fuel prices decide the vote.", "entry_ids": entry_ids[:1]}])
    for message in (parts, outline, contents, section, summary):
        message.response_metadata.update({"done_reason": "stop"})

    return fakes.scripted(parts, outline, contents, *[section] * section_attempts, summary)


def _prepare_document_run(section_text: str,
                           section_attempts: int = 1) -> RunContext:
    seeding = _make_run_context()
    _write_inputs(seeding)
    entry_ids = [entry["id"] for entry in document_plan_utils.select_document_entries(
        integration_service.build_integration(seeding.paths))]
    ctx = _make_run_context(_script_document_model(entry_ids, section_text, section_attempts))
    _write_inputs(ctx)

    return ctx


def test_the_document_file_is_assembled_by_the_program_from_an_outline_and_a_section():
    ctx = _prepare_document_run("89 % of voters want cheaper fuel. The figure is the headline of the evening.")
    update = write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE), _bind(ctx))
    harness.check("phase 4 completes", update["phases"]["finalize"] == "complete", str(update))
    markdown = ctx.paths.summary.read_text(encoding="utf-8")
    harness.check("the document opens with the outline's claim", markdown.startswith("# Fuel prices decide the vote"))
    harness.check("and carries the section the model wrote", "89 % of voters want cheaper fuel." in markdown, markdown)
    harness.check("five model calls were made: the parts, the outline, the title, one section and the summary",
                  ctx.model.call_count == 5, f"calls={ctx.model.call_count}")
    harness.check("the section call was handed the topic's entries, the speech before the first frame among them",
                  "89 Prozent wollen billigeren Sprit." in ctx.model.calls[3][1].content[0]["text"])
    harness.check("the provenance file sits beside the document",
                  (ctx.paths.output_root / output_contract.DRAFT_PROVENANCE_FILE).is_file())


def test_a_section_that_narrates_the_recording_is_rejected_and_the_phase_stops():
    ctx = _prepare_document_run("The video shows a graphic with 89 %. The presenter reads the figure out.",
                                 section_attempts=2)
    update = write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE), _bind(ctx))
    harness.check("the phase is blocked, never complete", update["phases"]["finalize"] == "blocked", str(update))
    harness.check("the rejected text is in no document, since none is written after a block", not ctx.paths.summary.is_file())


def test_the_document_phase_refuses_by_name_without_layer_2():
    ctx = _make_run_context(fakes.scripted(fakes.says("never asked")))
    update = write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE), _bind(ctx))
    harness.check("no integration file fails phase 4", update["phases"]["finalize"] == "failed", str(update))
    harness.check("and no model was called", ctx.model.call_count == 0)


def test_a_line_of_the_instructions_sample_is_known_whatever_its_case_and_spacing():
    """The sample answer of frames_processing.md is read once and compared folded: case, spacing and a leading dash do not hide a
    copied line, and a short line says nothing particular and is never matched."""

    lines = sorted(instruction_example_utils.list_example_lines())
    harness.check("the sample answer gives lines to compare", len(lines) > 3, str(len(lines)))
    sample = lines[0]
    harness.check("a sample line copied with other case, spacing and a dash is known",
                  instruction_example_utils.is_example_line("-  " + sample.upper().replace(" ", "  ")))
    harness.check("a line of the meeting's own is not", not instruction_example_utils.is_example_line(
        "who ran the poll and when it closed"))


def test_an_instruction_file_with_no_sample_answer_is_refused_by_name():
    """With no sample to compare, the copied-line check would pass every note; the file and the missing block are named
    instead."""

    kept = app_config.AGENT_CONTEXT_DIR
    root = Path(tempfile.mkdtemp())
    try:
        app_config.AGENT_CONTEXT_DIR = root
        for text, expected in ((None, "frames-processing.md"), ("# Frames\nno sample here\n", "~~~markdown")):
            if text is not None:
                (root / "instructions" / "_shared").mkdir(parents=True, exist_ok=True)
                (root / "instructions" / "_shared" / "frames-processing.md").write_text(text, encoding="utf-8")
            try:
                instruction_example_utils.list_example_lines()
                message = "no error"
            except (UnknownInstructionFileError, ValueError) as error:
                message = str(error)
            harness.check(f"refused naming {expected}", expected in message, message)
    finally:
        app_config.AGENT_CONTEXT_DIR = kept


def test_a_transcript_that_is_no_utf8_is_unparsable_not_raised():
    paths = fx.make_paths()
    path = phase_file_service.build_phase_file_path(paths, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe not utf-8 \xc3")
    try:
        codes = harness.codes(transcript_file_validator.validate_transcript_file(paths))
    except UnicodeDecodeError as error:
        codes = {f"raised: {error}"}
    harness.check("a file that is no UTF-8 is T_UNPARSABLE", codes == {"T_UNPARSABLE"}, str(codes))


def test_a_segment_time_of_true_is_no_time():
    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, 1, {"hasAudio": True, "segments": [{"text": "hello", "start": True, "end": 2.0}]})
    codes = harness.codes(transcript_file_validator.validate_transcript_file(paths))
    harness.check("a boolean start is T_SEGMENT_TIMES", codes == {"T_SEGMENT_TIMES"}, str(codes))
