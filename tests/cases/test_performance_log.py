"""Performance of every unit of work, the owner's request of 2026-09-26: "make sure the playbook collect and logs
performance data for each item or batch or video or phase".

One line per finished unit goes to `performance.jsonl` in the run's directory, the moment it finishes, and one line
to the console: a frame asked alone is an `item`, several frames in one call a `batch`, a phase-4 outline, contents
or section a `part`. Each phase that ran writes a `phase` line when it ends, and the run a `video` line. Model calls
keep their own line each in `calls.jsonl`; a unit's line sums the calls it made, retries included.
"""

import ast
import json
import logging
import tempfile
from pathlib import Path

from support import fakes
from support import harness
from support import section_fixtures
from support import write_mode

from langchain_core.messages import AIMessage, HumanMessage

import config as app_config
from chains import chain_runner
from cli import run_command
from graph import graph_state
from graph.nodes import process_frames_node
from graph.nodes import write_document_node
from models import output_contract
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from models.timing_handles import TimingHandles
from services import call_log_service
from services import call_record_service
from services import performance_log_service
from services import document_file_service
from services import integration_service
from services import phase_file_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import document_plan_utils
from utils import json_lines_utils
from utils import meeting_notes_utils

TRANSCRIBE, FRAMES_PREPARING, FRAMES_PROCESSING, FINALIZE = (
    output_contract.PHASE_NUMBERS[name] for name in ("transcribe", "frames_preparing", "frames_processing", "finalize"))
ATMOSPHERE = ("The presenter speaks steadily and takes one question about the figure. Nobody disagrees, and the room is quiet otherwise.")
BATCH_SUMMARY = ("After the survey's method, the presenter sets out to show the result of a poll on fuel prices. They "
                 "bring up the slide with the headline figure. They read out the share of voters who want cheaper fuel. "
                 "They add that the question was asked the same way as in the year before. Nobody asks about it. The "
                 "batch ends with the figure still on screen.")
RECORD = {"screen_kind": "slide", "facts": ["89 % of voters want cheaper fuel"], "diagram": "",
          "screen": '- slide: white, the whole frame\n  - text "89 %": centre', "speech_visual_alignment": "the figure is being read out", "builds_on": [],
          "contradicts_earlier": [], "open_questions": [],
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
          "third_person_description": "The presenter points at the figure on the slide and reads it out.",
          "tags": ["refund policy", "reference"], "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}
EMPTY_RECORD = {**RECORD, "facts": [], "screen": '', "diagram": "", "speech_visual_alignment": ""}
PROMPT_TOKENS, OUTPUT_TOKENS, PROMPT_S, OUTPUT_S = 1000, 500, 2.0, 25.0
TRANSCRIPT = {"hasAudio": True, "segments": [{"text": " 89 Prozent wollen billigeren Sprit.", "start": 1.0, "end": 4.0,
                                              "words": []}]}
FRAMES = {"schemaVersion": 1, "items": [{"slug": "frame-000", "index": 0, "timestamp": 2.0}]}
RECORDS = {"schemaVersion": 1, "records": [{"slug": "frame-000", "index": 0, "timestamp": 2.0, "frame": 0, **RECORD}]}


class _Lines(logging.Handler):
    """Every line the run printed, as the operator reads it."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _load_run_context(model=None,
                      count: int = 0) -> tuple[RunContext, _Lines]:
    tmp = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=tmp, module="clip.mp4", output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = tmp / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(tmp), module="clip.mp4",
                                        output_root=str(paths.output_root)), budgets={"maxAttempts": 2}, models=[])
    logger = logging.getLogger(f"performance-test-{tmp.name}")
    logger.setLevel(logging.INFO)
    lines = _Lines()
    logger.addHandler(lines)
    ctx = RunContext(repository=tmp, module="clip.mp4", paths=paths, store=store, run_dir=run_dir, log=logger,
                     ignore=IgnoreList(), model=model)
    items = []
    for index in range(count):
        frame = paths.output_root / "frames" / f"frame-{index:03d}.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(f"frame {index}".encode())
        items.append({"slug": f"frame-{index:03d}", "index": index, "timestamp": index * 6.0,
                      "path": f"frames/frame-{index:03d}.jpg", "transcriptBefore": "",
                      "transcriptCurrent": f"words of frame {index}"})
    if items:
        store.seed_items(items)
        phase_file_service.write_phase_file(paths, TRANSCRIBE, {
            "segments": [{"text": " ".join(item["transcriptCurrent"] for item in items),
                          "start": 0.0, "end": count * 6.0, "words": []}]})
        phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})

    return ctx, lines


def _answer(schema: str,
            **fields):
    """A reply as Ollama reports it: the tool call, how it ended, its tokens and where the time went."""

    # A section is stated by its parts and answered as the markdown the agent writes.
    fields = section_fixtures.build_section_answer(fields) if schema == "DocumentSection" else fields
    message = fakes.tool_call(schema, **fields)
    message.response_metadata.update({"done_reason": "stop", "prompt_eval_count": PROMPT_TOKENS,
                                      "eval_count": OUTPUT_TOKENS, "prompt_eval_duration": int(PROMPT_S * 1e9),
                                      "eval_duration": int(OUTPUT_S * 1e9)})

    return message


def _frame_answer(**fields):
    """The frame model's markdown answer, with its tokens and time as Ollama reports them."""

    message = fakes.frame_notes(**fields)
    message.response_metadata.update({"done_reason": "stop", "prompt_eval_count": PROMPT_TOKENS,
                                      "eval_count": OUTPUT_TOKENS, "prompt_eval_duration": int(PROMPT_S * 1e9),
                                      "eval_duration": int(OUTPUT_S * 1e9)})

    return message


def _run_frames_processing(ctx: RunContext,
              queue: list[str],
              frames_per_call: int,
              repair_calls: int = 1) -> dict:
    """Drive the frame loop to the end of the queue, as the graph's loop-back edge would."""

    previous = app_config.settings.frames_processing.frames_per_call
    previous_repairs = app_config.settings.frames_processing.repair_calls
    previous_alone = app_config.settings.frames_processing.single_frame_attempts
    app_config.settings.frames_processing.repair_calls = repair_calls
    app_config.settings.frames_processing.frames_per_call = frames_per_call
    # The scenarios ask an unfinished frame alone once, whatever this machine's playbook.local.json sets
    app_config.settings.frames_processing.single_frame_attempts = 1
    state = {**graph_state.new_run_state(FRAMES_PROCESSING, FRAMES_PROCESSING), "queue": queue}
    try:
        while state.get("cursor", 0) < len(queue):
            state.update(process_frames_node.process_frames_node(state, {"configurable": {"ctx": ctx}}))
    finally:
        app_config.settings.frames_processing.frames_per_call = previous
        app_config.settings.frames_processing.repair_calls = previous_repairs
        app_config.settings.frames_processing.single_frame_attempts = previous_alone

    return state


def _read_rows(ctx: RunContext,
               level: str) -> list[dict]:
    path = ctx.run_dir / performance_log_service.PERFORMANCE_FILE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    return [row for row in rows if row.get("level") == level]


def test_a_frame_asked_alone_logs_one_item_line_with_its_time_calls_and_tokens():
    model = fakes.scripted(_frame_answer(**{**RECORD, "frame": 0}), _frame_answer(**{**RECORD, "frame": 1}))
    ctx, lines = _load_run_context(model, count=2)
    _run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=1)
    rows = _read_rows(ctx, "item")
    harness.check("one item line per frame, in order", [row.get("slug") for row in rows] == ["frame-000", "frame-001"],
                  str(rows))
    first = rows[0] if rows else {}
    harness.check("the item line carries the phase, the frame count, the attempts and the outcome",
                  (first.get("phase"), first.get("frames"), first.get("attempts"), first.get("status"))
                  == ("frames_processing", 1, 1, "written"), str(first))
    harness.check("and the call it made, with its tokens and output speed",
                  (first.get("calls"), first.get("promptTokens"), first.get("outputTokens"), first.get("outputTokPerS"))
                  == (1, PROMPT_TOKENS, OUTPUT_TOKENS, OUTPUT_TOKENS / OUTPUT_S), str(first))
    harness.check("and when it started and how long it took",
                  isinstance(first.get("durationS"), (int, float)) and first["durationS"] >= 0
                  and str(first.get("startedAt", "")).count(":") == 2, str(first))
    start = lines.lines.index("reading frame 0 of 2 at 0:00 …") if "reading frame 0 of 2 at 0:00 …" in lines.lines else -1
    end = lines.lines[start + 1] if 0 <= start < len(lines.lines) - 1 else ""
    harness.check("the console names the frame the moment its call starts", start >= 0, "\n".join(lines.lines))
    harness.check("and says how long, the tokens and the outcome under it when it ends",
                  end.startswith("  ") and f"{PROMPT_TOKENS:,} in / {OUTPUT_TOKENS:,} out" in end and end.endswith("accepted"),
                  "\n".join(lines.lines))


def test_a_retry_counts_every_call_the_frame_made():
    model = fakes.scripted(_frame_answer(**{**EMPTY_RECORD, "frame": 0}),
                           _frame_answer(**{**RECORD, "frame": 0}))
    ctx, _ = _load_run_context(model, count=1)
    previous = app_config.settings.budgets.max_attempts
    app_config.settings.budgets.max_attempts = 2
    try:
        _run_frames_processing(ctx, ["frame-000"], frames_per_call=1)
    finally:
        app_config.settings.budgets.max_attempts = previous
    rows = _read_rows(ctx, "item")
    harness.check("one item line for the frame, with both attempts and both calls",
                  len(rows) == 1 and (rows[0].get("attempts"), rows[0].get("calls"), rows[0].get("outputTokens"))
                  == (2, 2, 2 * OUTPUT_TOKENS), str(rows))


def test_a_group_logs_one_batch_line_and_its_repair_call_another():
    group = _frame_answer(batch_summary=BATCH_SUMMARY, atmosphere=ATMOSPHERE, frames=[{**RECORD, "frame": 0}, {**EMPTY_RECORD, "frame": 1}])
    repair = _frame_answer(batch_summary=BATCH_SUMMARY, atmosphere=ATMOSPHERE, frames=[{**RECORD, "frame": 1}])
    ctx, lines = _load_run_context(fakes.scripted(group, repair), count=2)
    _run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    batches, items = _read_rows(ctx, "batch"), _read_rows(ctx, "item")
    batch = batches[0] if batches else {}
    # R19's critique and its repair are each a batch unit of their own, over the frames whose records were accepted.
    harness.check("one batch line for the call of two frames, one for its repair, one each for the critique and the "
                  "repair, one for the open questions",
                  [(row.get("slug"), row.get("frames"), row.get("calls")) for row in batches]
                  == [("frames-000-001", 2, 1), ("frames-000-001-repair", 1, 1),
                      ("frames-000-001-critique", 2, 1), ("frames-000-001-second-pass", 2, 1),
                      ("frames-000-001-open-questions", 2, 1)], str(batches))
    harness.check("its seconds per frame are its seconds over its frames",
                  batch.get("secondsPerFrame") == round(batch.get("durationS", -1) / 2, 1), str(batch))
    harness.check("no frame is asked alone", items == [], str(items))
    harness.check("the console names the batch by its frames when its call starts, and the repair call by its frame",
                  "reading frames 0 → 1 of 2 at 0:00 …" in lines.lines and "reading frame 1 of 2 at 0:06 …" in lines.lines,
                  "\n".join(lines.lines))
    harness.check("the batch ends with how long one frame took and what follows, naming the retried frame",
                  any(line.startswith("  ") and "one frame takes" in line and "on average)" in line
                      # One frame is named in the singular: "frames 1" read as a count (the owner, 2026-09-28).
                      and line.endswith("1 of 2 accepted → retried together (frame 1)")
                      for line in lines.lines), "\n".join(lines.lines))


def test_the_console_line_gives_the_context_used_against_the_window_the_host_loaded():
    ctx, lines = _load_run_context(fakes.scripted(_frame_answer(**{**RECORD, "frame": 0})), count=1)
    ctx.phase_windows["frames_processing"] = 10_000
    _run_frames_processing(ctx, ["frame-000"], frames_per_call=1)
    percent = (PROMPT_TOKENS + OUTPUT_TOKENS) * 100 // 10_000
    harness.check("the call's tokens as a share of the loaded window, naming the window", any(f"context {percent}% of 10,000" in line
                                                                           for line in lines.lines),
                  "\n".join(lines.lines))


def test_the_end_of_the_frame_queue_logs_the_phase_line():
    group = _frame_answer(batch_summary=BATCH_SUMMARY, atmosphere=ATMOSPHERE, frames=[{**RECORD, "frame": 0}, {**EMPTY_RECORD, "frame": 1}])
    repair = _frame_answer(batch_summary=BATCH_SUMMARY, atmosphere=ATMOSPHERE, frames=[{**RECORD, "frame": 1}])
    ctx, lines = _load_run_context(fakes.scripted(group, repair), count=2)
    _run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    phases = _read_rows(ctx, "phase")
    phase = phases[0] if phases else {}
    harness.check("one phase line for phase 3, written when its queue ran out", len(phases) == 1
                  and phase.get("phase") == "frames_processing", str(phases))
    # Five calls: the batch, its repair, R19's critique, its repair and the open questions. Each frame is still
    # counted once. The tokens are three calls' worth, because the fake answers the critique and the repair itself and
    # reports no usage for either.
    harness.check("it counts each frame once, however often it was asked, and every call",
                  (phase.get("frames"), phase.get("calls"), phase.get("outputTokens")) == (2, 5, 3 * OUTPUT_TOKENS),
                  str(phase))
    harness.check("with its seconds per frame", phase.get("secondsPerFrame") == round(phase.get("durationS", -1) / 2, 1),
                  str(phase))
    harness.check("the console says the phase's time", any("phase 3" in line and "one frame takes" in line
                                                            for line in lines.lines), "\n".join(lines.lines))


def test_a_phase_that_fails_before_any_call_logs_its_line():
    failing, _ = _load_run_context()
    write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE), {"configurable": {"ctx": failing}})
    phases = _read_rows(failing, "phase")
    harness.check("phase 4 refused for a missing input writes one phase line, failed and with no call",
                  [(row.get("phase"), row.get("status"), row.get("calls")) for row in phases]
                  == [("finalize", "failed", 0)] and isinstance(phases[0].get("durationS"), (int, float)), str(phases))


def test_each_document_part_logs_a_part_line_and_phase_5_its_phase_line():
    seeding, _ = _load_run_context()
    for number, content in ((TRANSCRIBE, TRANSCRIPT), (FRAMES_PREPARING, FRAMES), (FRAMES_PROCESSING, RECORDS)):
        phase_file_service.write_phase_file(seeding.paths, number, content)
    document_file_service.write_meeting_notes(
        seeding.paths, meeting_notes_utils.render_meeting_notes(
            seeding.module, integration_service.build_integration(seeding.paths)))
    entry_ids = [entry["id"] for entry in document_plan_utils.select_document_entries(
        integration_service.build_integration(seeding.paths))]
    # The only speech comes before the first frame, so no frame read it and there is no enrichment call.
    model = fakes.scripted(
        _answer("DocumentParts", announced_agenda=[], points=[{"title": "Fuel", "first_moment": 1, "covers_announced": []}]),
        _answer("DocumentOutline", topics=[{"title": "Fuel", "first_entry_id": entry_ids[0]}], set_aside_entry_ids=[],
                not_established=["Whether prices keep rising."]),
        _answer("DocumentContents", title="Fuel prices decide the vote"),
        _answer("DocumentSection", heading="Fuel prices", not_established=[],
                paragraphs=[{"text": "89 % of voters want cheaper fuel. The figure is the headline of the evening.",
                             "entry_ids": entry_ids}]),
        _answer("DocumentSummary", agenda=[{"part_number": 1, "text": "Discusses fuel prices"}],
                summary=[{"text": "Fuel prices decide the vote.", "entry_ids": entry_ids[:1]}]))
    ctx, lines = _load_run_context(model)
    for number, content in ((TRANSCRIBE, TRANSCRIPT), (FRAMES_PREPARING, FRAMES), (FRAMES_PROCESSING, RECORDS)):
        phase_file_service.write_phase_file(ctx.paths, number, content)
    document_file_service.write_meeting_notes(
        ctx.paths, meeting_notes_utils.render_meeting_notes(
            ctx.module, integration_service.build_integration(ctx.paths)))
    write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE), {"configurable": {"ctx": ctx}})
    parts = _read_rows(ctx, "part")
    named = {row.get("slug"): [line for line in lines.lines if line.strip().split(" ")[0] == row.get("slug")]
             for row in parts}
    harness.check("the console names each part once, when its call starts, and never again under it",
                  bool(named) and all(found == [f"{slug} …"] for slug, found in named.items()), "\n".join(lines.lines))
    harness.check("one part line per call of phase 4, each with its call",
                  [(row.get("slug"), row.get("calls")) for row in parts]
                  == [(write_document_node.PARTS_SLUG, 1), ("outline-01", 1), (write_document_node.CONTENTS_SLUG, 1),
                      ("heading-001-1", 1), (write_document_node.SUMMARY_SLUG, 1)], str(parts))
    finalize = [row for row in _read_rows(ctx, "phase") if row.get("phase") == "finalize"]
    harness.check("phase 4 writes its phase line with all five calls",
                  len(finalize) == 1 and (finalize[0].get("calls"), finalize[0].get("outputTokens"))
                  == (5, 5 * OUTPUT_TOKENS), str(finalize))


def test_the_run_ends_with_a_video_line_summing_its_phases_and_a_table():
    ctx, lines = _load_run_context()
    handles = performance_log_service.open_session(ctx, TRANSCRIBE, FINALIZE)
    harness.check("a session is timed from handles that name no unit", isinstance(handles, TimingHandles)
                  and (handles.phase, handles.slug) == ("run", None), str(handles))
    write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE), {"configurable": {"ctx": ctx}})
    performance_log_service.record_video(ctx, handles, "complete", video_s=120.0)
    videos = _read_rows(ctx, "video")
    video = videos[0] if videos else {}
    harness.check("one video line, with the recording's length and how long the run took against it",
                  len(videos) == 1 and video.get("videoS") == 120.0
                  and video.get("realTimeFactor") == round(video.get("durationS", -1) / 120.0, 3), str(videos))
    harness.check("it names the phases this session ran", video.get("phases") == ["finalize"], str(video))
    harness.check("the console ends with a table of the phases and the video",
                  any("finalize" in line and "video" in line for line in lines.lines), "\n".join(lines.lines))


def test_an_audio_run_s_table_names_its_phases_by_what_they_do_and_counts_no_frame():
    phases = [{"phase": "transcribe", "number": 1, "status": "complete", "durationS": 150.0},
              {"phase": "frames_preparing", "number": 2, "status": "complete", "durationS": 300.0, "calls": 1,
               "promptTokens": 8000, "outputTokens": 9000, "outputTokPerS": 30.0}]
    audio = {"status": "complete", "durationS": 450.0, "videoS": 900.0, "realTimeFactor": 0.5, "calls": 1}
    table = performance_log_service.render_performance_table(phases, audio, is_audio=True).splitlines()
    harness.check("no frame column", "frames" not in table[0] and "s per frame" not in table[0], table[0])
    harness.check("phase 2 is speech preparing, the last row the audio",
                  [line.split()[0] for line in table[1:4]] == ["transcribe", "speech_preparing", "audio"], str(table))
    harness.check("the length line says audio", table[-1].startswith("15.0 min of audio in 7.5 min")
                  and table[-1].endswith("per second of audio"), table[-1])
    video = performance_log_service.render_performance_table(phases, audio).splitlines()
    harness.check("a video's table is unchanged", "s per frame" in video[0]
                  and [line.split()[0] for line in video[1:4]] == ["transcribe", "frames_preparing", "video"], str(video))


def test_the_run_command_opens_a_session_and_records_the_video_on_every_way_out():
    tree = ast.parse(Path(run_command.__file__).read_text(encoding="utf-8"))
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
             and node.func.value.id == "performance_log_service"}
    harness.check("cmd_run opens a performance session and records the video", {"open_session", "record_video"} <= calls,
                  str(calls))
    records = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "record_video"]
    harness.check("the video is recorded when the run ends AND when an operator stops it", len(records) >= 2,
                  str(len(records)))


def test_a_row_appended_after_a_torn_line_starts_a_line_of_its_own():
    path = Path(tempfile.mkdtemp()) / "rows.jsonl"
    path.write_text('{"kept": 1}\n{"torn": ', encoding="utf-8")
    json_lines_utils.append_json_line(path, {"next": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    harness.check("the torn line stays alone and the new row reads", lines == ['{"kept": 1}', '{"torn": ', '{"next": 2}'],
                  str(lines))


def test_a_line_a_kill_cut_in_half_never_swallows_the_next_session():
    ctx, _ = _load_run_context()
    performance_log_service.open_session(ctx, TRANSCRIBE, FINALIZE)
    with (ctx.run_dir / performance_log_service.PERFORMANCE_FILE).open("a", encoding="utf-8") as handle:
        handle.write('{"level": "item", "session": "the killed li')
    performance_log_service.open_session(ctx, FRAMES_PROCESSING, FINALIZE)
    sessions = [row["phases"] for row in performance_log_service.read_rows(ctx.run_dir) if row.get("level") == "session"]
    harness.check("the resumed session's line is read", sessions == [f"{TRANSCRIBE}..{FINALIZE}", f"{FRAMES_PROCESSING}..{FINALIZE}"],
                  str(sessions))


def test_a_stopped_run_closes_its_unfinished_phase_before_the_video_line():
    model = fakes.scripted(_frame_answer(**{**RECORD, "frame": 0}))
    ctx, _ = _load_run_context(model, count=2)
    handles = performance_log_service.open_session(ctx, FRAMES_PROCESSING, FINALIZE)
    previous = app_config.settings.frames_processing.frames_per_call
    previous_repairs = app_config.settings.frames_processing.repair_calls
    app_config.settings.frames_processing.repair_calls = 1
    app_config.settings.frames_processing.frames_per_call = 1
    try:
        state = {**graph_state.new_run_state(FRAMES_PROCESSING, FINALIZE), "queue": ["frame-000", "frame-001"]}
        process_frames_node.process_frames_node(state, {"configurable": {"ctx": ctx}})
    finally:
        app_config.settings.frames_processing.frames_per_call = previous
        app_config.settings.frames_processing.repair_calls = previous_repairs
    performance_log_service.record_video(ctx, handles, "interrupted", video_s=None)
    phases = _read_rows(ctx, "phase")
    video = (_read_rows(ctx, "video") or [{}])[0]
    harness.check("the phase the stop cut short gets its line, marked interrupted",
                  [(row.get("phase"), row.get("status"), row.get("frames")) for row in phases]
                  == [("frames_processing", "interrupted", 1)], str(phases))
    harness.check("and the video line counts its frame and its call instead of reporting nothing",
                  (video.get("frames"), video.get("calls")) == (1, 1), str(video))


def test_a_batch_with_copies_passed_over_is_still_named_by_its_first_and_last_frame():
    ctx, lines = _load_run_context(count=4)
    performance_log_service.start_unit(ctx, "frames_processing", "frames-000-003", ["frame-000", "frame-002", "frame-003"])
    harness.check("the approved arrow form, even when copies in between were passed over",
                  lines.lines == ["reading frames 0 → 3 of 4 at 0:00 …"], str(lines.lines))


def test_a_unit_paused_between_its_start_and_its_end_counts_only_its_work_defect():
    # A fix call the owner paused for 54 minutes was logged at 58:16, "one frame takes 1165 s": the pause was counted
    # as the call's time. A unit's time now leaves out the pauses waited out while it ran
    from services import control_signal_service

    ctx, lines = _load_run_context(count=2)
    clock = {"now": 1000.0}

    class FakeTime:
        @staticmethod
        def monotonic():
            return clock["now"]

        # the pause's poll: no wait, the clock moved on and the owner's pause cleared
        sleep = staticmethod(lambda seconds: (clock.update(now=clock["now"] + 3000.0),
                                              control_signal_service.clear(ctx.run_dir)))

        time = staticmethod(__import__("time").time)
        strftime = staticmethod(__import__("time").strftime)

    saved = (performance_log_service.time, control_signal_service.time)
    performance_log_service.time = control_signal_service.time = FakeTime
    try:
        handles = performance_log_service.start_unit(ctx, "finalize", "outline-03")
        clock["now"] += 100.0
        control_signal_service.request(ctx.run_dir, control_signal_service.PAUSE)
        control_signal_service.check(ctx.run_dir)
        clock["now"] += 140.0
        performance_log_service.record_unit(ctx, handles, "part", "accepted", [])
    finally:
        performance_log_service.time, control_signal_service.time = saved
    units = [row for row in _read_rows(ctx, "part") if row.get("slug") == "outline-03"]
    harness.check("the unit's time is its 240 s of work, not the 3,000 s it waited paused",
                  [row.get("durationS") for row in units] == [240.0], str(units))


def test_a_label_decides_what_a_unit_s_start_line_says():
    """The owner's words, 2026-09-27: "this is not showing the total number of follow up left". A phase-4 follow-up is
    started with its place as its label, `outline 3 of 11`; no label keeps the unit's own name."""

    ctx, lines = _load_run_context(count=2)
    unnamed = performance_log_service.start_unit(ctx, "finalize", "outline-03")
    labelled = performance_log_service.start_unit(ctx, "finalize", "outline-03", label="outline 3 of 11")
    harness.check("no label prints the unit's own name, a label prints the label",
                  lines.lines == ["outline-03 …", "outline 3 of 11 …"], str(lines.lines))
    performance_log_service.record_unit(ctx, labelled, "part", "accepted", [])
    harness.check("a labelled unit's outcome goes under its start line and never names it a second time",
                  len(lines.lines) == 3 and lines.lines[-1].startswith("  ") and "outline" not in lines.lines[-1],
                  str(lines.lines))


def test_a_frame_kept_with_a_warning_says_so_in_plain_words():
    facts = ["The slide shows the monthly limit.", "The screen shows the save button."]
    group = _frame_answer(batch_summary=BATCH_SUMMARY, atmosphere=ATMOSPHERE,
                          frames=[{**RECORD, "frame": 0, "facts": facts}, {**RECORD, "frame": 1}])
    ctx, lines = _load_run_context(fakes.scripted(group), count=2)
    _run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    warned = [line for line in lines.lines if "with a warning" in line]
    harness.check("the console says the frame was accepted with a warning, and what the warning is",
                  len(warned) == 1 and warned[0].startswith("  frame 0 accepted with a warning: ")
                  and "describe the screen" in warned[0], "\n".join(lines.lines))
    harness.check("and never prints the check's code name", not any("FRAME_FACTS_DESCRIBE_SCREEN" in line for line in lines.lines),
                  "\n".join(lines.lines))


def test_with_no_repair_call_the_batch_line_says_each_failed_frame_is_retried_alone():
    group = _frame_answer(batch_summary=BATCH_SUMMARY, atmosphere=ATMOSPHERE, frames=[{**RECORD, "frame": 0}, {**EMPTY_RECORD, "frame": 1}])
    alone = _frame_answer(**{**RECORD, "frame": 1})
    ctx, lines = _load_run_context(fakes.scripted(group, alone), count=2)
    _run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2, repair_calls=0)
    harness.check("the batch line says the failed frame goes alone, never 'together'",
                  any(line.endswith("1 of 2 accepted → each retried alone (frame 1)") for line in lines.lines)
                  and not any("retried together" in line for line in lines.lines), "\n".join(lines.lines))


def test_a_step_stopped_for_looping_says_so_and_never_cut_at_the_output_limit():
    """A follow-up whose first attempt was stopped for repeating its thinking was printed "cut at output limit"
    (lecture-1 frames-087-088-answers, 2026-10-13): the loop is a cut to the program, and named for what it was."""

    looped = performance_log_service._render_status({"status": "1 answered, 1 stay open", "cutAtLimit": 0, "loopStopped": 1})
    cut = performance_log_service._render_status({"status": "accepted", "cutAtLimit": 1, "loopStopped": 0})
    harness.check("a loop stop is named, and no cut", looped == "stopped looping · 1 answered, 1 stay open", looped)
    harness.check("a cut is still named a cut", cut == "cut at output limit · accepted", cut)


def test_a_batch_past_the_count_names_the_last_frame_s_number():
    """Frames dropped before phase 3 leave numbers that run past the count: "frames 173 → 174 of 173" read as past the
    end with 16 frames still to go (lecture-1, 2026-10-14). The line names the last frame's number then."""

    ctx, lines = _load_run_context(count=4)
    ctx.store.list_items()["frame-003"]["index"] = 9
    performance_log_service.start_unit(ctx, "frames_processing", "frames-000-000", ["frame-000"])
    harness.check("the count of 4 frames gives way to the last frame's number, 9",
                  any(line.endswith("…") and " of 9 at " in line for line in lines.lines), str(lines.lines))


def test_a_phase_with_no_metrics_code_of_its_own_counts_its_calls_on_its_closing_line_defect():
    # Defect: a knowledge pass's phase 7 closing table read "0 calls" and "no model call" after 17 hours of calls: only
    # the callers that wrote their own row into the call record were counted, and phase 7 wrote none.
    # Fix: every call through chain_runner is one row of the phase and step started last, written in one place
    ctx, lines = _load_run_context()
    phase = output_contract.DOCUMENT_RESHAPING_PHASE
    handles = performance_log_service.start_unit(ctx, phase, None)
    usage = {"input_tokens": PROMPT_TOKENS, "output_tokens": OUTPUT_TOKENS, "total_tokens": PROMPT_TOKENS + OUTPUT_TOKENS}
    model = fakes.scripted(AIMessage(content="part closed", usage_metadata=usage))
    chain_runner.read_streamed_reply(model=model,
                                     messages=[HumanMessage(content="Read part 1.")])
    row = performance_log_service.record_phase(ctx, phase, "complete", handles)
    call_record_service.clear_place()
    harness.check("the phase's line counts its one call and its tokens",
                  row.get("calls") == 1 and row.get("promptTokens") == PROMPT_TOKENS, str(row))
    harness.check("and the console says no 'no model call'", not any("no model call" in line for line in lines.lines),
                  str(lines.lines))


def test_each_call_row_names_the_model_that_answered_and_its_machine_s_address_defect():
    # Defect: a speed comparison per machine could only guess each run's machine from the set it uses now, since no call
    # row said which model answered or where; a run moved between machines was counted under its last one.
    # Fix: every call row names the model the host reports and the address of the machine it was sent to
    ctx, _lines = _load_run_context()
    phase = output_contract.DOCUMENT_RESHAPING_PHASE
    performance_log_service.start_unit(ctx, phase, None)
    model = fakes.scripted(AIMessage(content="ok", response_metadata={"model_name": "qwen3.6-35b-a3b-pc"}))
    object.__setattr__(model, "base_url", "http://gpu-box:8090/v1")
    chain_runner.invoke_reply(model=model,
                              messages=[HumanMessage(content="Say ok.")])
    call_record_service.clear_place()
    row = call_log_service.read_call_metrics(ctx.run_dir)[-1]
    harness.check("the row names the model the host says answered",
                  row.get("model") == "qwen3.6-35b-a3b-pc", str(row))
    harness.check("and the machine's address the call went to",
                  row.get("host") == "http://gpu-box:8090/v1", str(row))


def test_a_call_made_with_no_phase_started_writes_no_row():
    ctx, _lines = _load_run_context()
    call_record_service.clear_place()
    chain_runner.invoke_reply(model=fakes.scripted(AIMessage(content="ok")),
                              messages=[HumanMessage(content="Say ok.")])
    harness.check("a call outside any phase (a probe, the document chat) adds nothing to a run's call record",
                  call_log_service.read_call_metrics(ctx.run_dir) == [], str(call_log_service.read_call_metrics(ctx.run_dir)))
