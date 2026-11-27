"""One follow-up of phase 4's conversation, driven by a scripted model. REQUIREMENTS.md R12.

The follow-up offers the reading tools and exactly one submit tool. An answer is judged on submit by the same
validator the calls path uses; a rejected one comes back as the tool's result with the findings, in the same
conversation, until the attempts or the calls run out. When the conversation passes its size_limit it is cut back to
the starting_messages, keeping the follow-up, the last findings and the attempt number.
"""

import dataclasses
import functools
import json
import logging
import tempfile
import types
from pathlib import Path
from typing import Any, Sequence

from support import fakes
from support import harness

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from models.starting_messages_spec import StartingMessagesSpec
from models.conversation_limits_spec import ConversationLimitsSpec
from models.follow_up_spec import FollowUpSpec
from models.part_writing_context import PartWritingContext
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from models.write_conversation_state import WriteConversationState
from models.write_reading_context import WriteReadingContext
from models.write_reading_state import WriteReadingState
from services import conversation_log_service
from services import document_conversation_service
from services import document_file_service
from services import run_paths_service
from services import write_conversation_service
from services import write_reading_service
from services import worker_feedback_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore

GOOD = {"working_title": "Refunds are split into at most three parts",
        "sentences": ["A refund can be split into at most three parts.", "Each part is booked as its own payment.",
                      "Who approves a larger split is still open."]}
ENTRIES = [{"id": "F0.1", "kind": "facts", "text": "A refund can be split into at most three parts.",
            "timestamps": [10.0], "frames": [0], "tags": ["refund split limit", "reference"]}]
LIMITS = ConversationLimitsSpec(size_limit_tokens=120_000, window_tokens=163_840, max_output_tokens=32_768,
                            chars_per_token=3.5, frame_tokens=1_000, max_calls=6, max_attempts=3)


class ToolRecordingModel(fakes.ScriptedChatModel):
    """The scripted model, also keeping the names of the tools each bind offered."""

    bound: list[list[str]] = []
    output_limits: list[int | None] = []

    def bind_tools(self,
                   tools: Sequence[Any],
                   **kwargs: Any) -> "ToolRecordingModel":
        self.bound.append([tool["function"]["name"] for tool in tools])
        self.output_limits.append(kwargs.get("max_tokens"))

        return self


def _build_session(*script,
                   limits: ConversationLimitsSpec = LIMITS) -> WriteConversationState:
    tmp = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=tmp, module="meeting.mp4", output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = tmp / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json", RunIdentity(run_id="t", repository=str(tmp), module="meeting.mp4",
                                                              output_root=str(paths.output_root)), budgets={}, models=[])
    model = ToolRecordingModel(script=list(script), calls=[], bound=[], output_limits=[])
    ctx = RunContext(repository=tmp, module="meeting.mp4", paths=paths, store=store, run_dir=run_dir,
                     log=logging.getLogger("test"), ignore=IgnoreList(), model=model)
    reading = WriteReadingContext(paths=paths, moments=write_reading_service.build_reading_moments(ENTRIES),
                                  point_starts=[], frame_items={}, frame_records={}, read_chars=4000, notes_chars=400,
                                  read_budget=10)
    starting_messages = StartingMessagesSpec(system_text="You write the document of one meeting.", plan_text="The meeting.",
                                  notes=[], done_lines=[], topic_parts_text="", follow_up_text="", findings_text="")

    return WriteConversationState(ctx=ctx, model=model, reading=reading, reading_state=WriteReadingState(), starting_messages=starting_messages,
                                    limits=limits)


DRAFT = FollowUpSpec(submit_tool="submit_executive_summary_draft", slug="executive-summary-draft", inputs={"maxWords": 500},
                     text="Write the executive summary draft.", position="the executive summary draft")


def _texts(messages) -> str:
    return "\n".join(str(message.content) for message in messages)


def test_a_good_answer_is_accepted_and_only_the_expected_submit_tool_is_offered():
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("accepted on the first attempt", outcome.is_accepted and outcome.attempts == 1, str(outcome))
    offered = session.model.bound[0]
    harness.check("the one submit tool this follow-up expects", [name for name in offered if name.startswith("submit_")]
                  == ["submit_executive_summary_draft"], str(offered))
    harness.check("beside the reading tools", {"list_tags", "find", "read_frame", "note"} <= set(offered))
    harness.check("the answer is on disk", document_file_service.read_executive_summary_draft(session.ctx.paths) == GOOD)
    first = session.model.calls[0]
    harness.check("the first prompt is the starting_messages: system, standing, follow-up",
                  isinstance(first[0], SystemMessage) and first[-1].content.startswith("Write the executive summary draft"), _texts(first))


def test_the_next_follow_up_is_asked_in_the_same_conversation():
    """R12: one conversation. The second follow-up continues the first, whose accepted answer got its result."""

    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD))
    write_conversation_service.run_follow_up(session, DRAFT)
    again = FollowUpSpec(submit_tool="submit_executive_summary_draft", slug="executive-summary-draft", inputs={"maxWords": 500},
                         text="Write the executive summary draft again.", position="the executive summary draft")
    outcome = write_conversation_service.run_follow_up(session, again)
    harness.check("the second follow-up is accepted", outcome.is_accepted, str(outcome))
    second = session.model.calls[1]
    harness.check("its prompt holds the first follow-up, not a new starting_messages",
                  sum(isinstance(message, SystemMessage) for message in second) == 1
                  and any(message.content == "Write the executive summary draft." for message in second), _texts(second))
    results = [message for message in second if isinstance(message, ToolMessage)]
    harness.check("the first answer's call got its result before the next question",
                  [message.content for message in results] == ["Accepted."], str([message.content for message in results]))
    harness.check("the new question comes last", second[-1].content == "Write the executive summary draft again.",
                  str(second[-1].content))


def test_a_rejected_answer_comes_back_with_its_findings_in_the_same_conversation():
    short = {**GOOD, "sentences": GOOD["sentences"][:1]}
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD))
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("accepted on the second attempt", outcome.is_accepted and outcome.attempts == 2, str(outcome))
    retry_prompt = session.model.calls[1]
    rejection = next(message for message in retry_prompt if isinstance(message, ToolMessage))
    harness.check("the tool's result names the finding and its fix", "EXECUTIVE_SUMMARY_DRAFT_LENGTH" in rejection.content
                  and "write 3 to 8 sentences" in rejection.content, rejection.content)
    harness.check("the first attempt is still in the conversation", len(retry_prompt) > len(session.model.calls[0]))


def test_a_reply_without_a_tool_call_spends_a_call_and_no_attempt_and_names_the_tool():
    session = _build_session(fakes.says("Here is my summary: refunds."), fakes.says("Still thinking."),
                             fakes.says("Now the draft."), fakes.tool_call("submit_executive_summary_draft", **GOOD))
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("three replies with no call judge nothing: accepted at the first answer, as many replies as the call "
                  "budget allows", outcome.is_accepted and outcome.attempts == 1 and outcome.calls == 4, str(outcome))
    nudge = session.model.calls[1][-1]
    harness.check("told to answer through the submit tool", isinstance(nudge, HumanMessage)
                  and "submit_executive_summary_draft" in str(nudge.content), str(nudge.content))


def test_a_call_written_as_text_is_read_as_a_call():
    session = _build_session(fakes.says(json.dumps({"name": "submit_executive_summary_draft", "arguments": GOOD})))
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("the text call is carried out and accepted", outcome.is_accepted, str(outcome))


def test_reads_carry_ids_and_stay_whole_in_the_conversation():
    session = _build_session(fakes.tool_call("find", words="refund"), fakes.tool_call("submit_executive_summary_draft", **GOOD))
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("accepted", outcome.is_accepted, str(outcome))
    after_read = next(message for message in session.model.calls[1] if isinstance(message, ToolMessage))
    harness.check("the read result starts with its id and holds the entry", after_read.content.startswith("[r1]")
                  and "F0.1" in after_read.content, after_read.content)


def test_a_follow_up_that_spends_its_calls_is_blocked():
    session = _build_session(fakes.tool_call("list_tags"),
                             limits=ConversationLimitsSpec(**{**LIMITS.__dict__, "max_calls": 3}))
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("blocked, and says why", not outcome.is_accepted and "calls" in outcome.reason, str(outcome))
    harness.check("after exactly its calls", session.model.call_count == 3, str(session.model.call_count))


def test_a_follow_up_with_its_own_call_limit_uses_it():
    """The table of contents reads the whole notes file first, so it has more calls than any other follow-up."""

    session = _build_session(fakes.tool_call("list_tags"),
                             limits=ConversationLimitsSpec(**{**LIMITS.__dict__, "max_calls": 3}))
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_calls=5))
    harness.check("blocked after its own five calls, not the conversation's three",
                  not outcome.is_accepted and session.model.call_count == 5 and "5 calls" in outcome.reason, str(outcome))


COMPACTING = ConversationLimitsSpec(**{**LIMITS.__dict__, "size_limit_tokens": 12_000})
# `budgets.chapterDryRuns` on: `check_chapter` offered, and an answer sent with no check before it checked first
DRY_RUNS = ConversationLimitsSpec(**{**LIMITS.__dict__, "can_dry_run_chapters": True})
# One long sentence: too few sentences, and long enough to grow the conversation past the size limit above
LONG_SHORT = {**GOOD, "sentences": ["The gateway keeps a long list. " + "word " * 12_000]}


def test_past_the_size_limit_the_conversation_is_compacted_with_the_workers_own_prompt():
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **LONG_SHORT),
                             fakes.says("## DRAFT\nA refund is split in three. SETTLED\n## NEXT\nSubmit."),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD), limits=COMPACTING)
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("accepted", outcome.is_accepted, str(outcome))
    ask = _texts(session.model.calls[1])
    harness.check("the model is asked to summarise with the prompt of the executive summary draft worker and its own history",
                  "write the working title and the executive" in ask and "## DRAFT" in ask
                  and "EXECUTIVE_SUMMARY_DRAFT_LENGTH" in ask and "The gateway keeps a long list" in ask, ask[:300])
    resumed = session.model.calls[2]
    harness.check("the conversation went on from the starting messages, the follow-up and one handoff message, not the grown conversation",
                  len(resumed) == 4 and isinstance(resumed[0], SystemMessage), _texts(resumed)[:200])
    handoff = resumed[-1].content
    harness.check("the handoff holds what the program measured, the model's summary and a check",
                  "<measured>" in handoff and "<summary>" in handoff and "A refund is split in three. SETTLED" in handoff
                  and "<check>" in handoff and "Attempt 2 of 3" in handoff, handoff)
    harness.check("the follow-up and the findings are in the rebuilt follow-up message",
                  "Write the executive summary draft" in resumed[-2].content, resumed[-2].content[:200])


def test_a_summary_that_comes_back_empty_twice_lets_the_follow_up_go_on_with_what_the_program_measured():
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **LONG_SHORT), fakes.says(""), fakes.says(""),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD), limits=COMPACTING)
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("accepted after two empty summaries", outcome.is_accepted and session.model.call_count == 4,
                  f"{outcome} calls={session.model.call_count}")
    handoff = session.model.calls[3][-1].content
    harness.check("the handoff says there is no summary and keeps the measured block",
                  "came back empty twice" in handoff and "<measured>" in handoff, handoff)


def test_every_call_is_logged_with_the_ids_of_the_messages_it_sent():
    session = _build_session(fakes.tool_call("find", words="refund"), fakes.tool_call("submit_executive_summary_draft", **GOOD))
    write_conversation_service.run_follow_up(session, DRAFT)
    lines = [json.loads(line) for line in session.conversation_log_path.read_text(encoding="utf-8").splitlines()]
    sent = [line["sent"] for line in lines if "sent" in line]
    harness.check("each call's prompt is logged as its message ids, in order", len(sent) == 2
                  and sent[0][:3] == ["starting_messages-system", "starting_messages-standing", "starting_messages-follow-up"] and len(sent[1]) > 3, str(sent))


def test_the_agenda_gives_the_reading_tools_their_steps_and_a_part_is_asked_through_the_writer():
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    document_conversation_service.record_agenda(session, [{"start": 0.0}, {"start": 60.0}])
    harness.check("the steps start where the parts start", session.reading.point_starts == [0.0, 60.0])
    writing = PartWritingContext(ctx=session.ctx, write_part=functools.partial(
        document_conversation_service.write_part_in_conversation, session))
    is_accepted = writing.write_part("executive_summary_draft", "executive-summary-draft",
                                     document_file_service.build_executive_summary_draft_path(session.ctx.paths),
                                     {"slug": "executive-summary-draft", "moments": [], "maxWords": 500})
    harness.check("the draft asked as a follow-up is accepted", is_accepted)
    working = document_file_service.build_working_draft_path(session.ctx.paths).read_text(encoding="utf-8")
    harness.check("the working file shows the accepted draft at once, and the scaffold still to come",
                  f"Working title: {GOOD['working_title']}" in working and "The table of contents is not written yet" in working,
                  working)
    again = writing.write_part("executive_summary_draft", "executive-summary-draft",
                               document_file_service.build_executive_summary_draft_path(session.ctx.paths),
                               {"slug": "executive-summary-draft", "moments": [], "maxWords": 500})
    harness.check("asked again, it is kept from disk without a call", again and session.model.call_count == 1,
                  str(session.model.call_count))


def test_an_answer_kept_from_disk_after_a_block_is_recorded_accepted():
    """A chapter blocked by a check fixed since passed on resume and the run went on, but its record still said
    blocked, so the page counted a blocked step under a running run (2026-10-04)."""

    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    writing = PartWritingContext(ctx=session.ctx, write_part=functools.partial(
        document_conversation_service.write_part_in_conversation, session))
    path = document_file_service.build_executive_summary_draft_path(session.ctx.paths)
    inputs = {"slug": "executive-summary-draft", "moments": [], "maxWords": 500}
    writing.write_part("executive_summary_draft", "executive-summary-draft", path, inputs)
    document_file_service.write_follow_up_record(session.ctx.paths, "executive-summary-draft", {
        "status": "blocked", "attempts": 1, "calls": 3, "reason": "a check since fixed", "inputsHash": ""})
    is_kept = writing.write_part("executive_summary_draft", "executive-summary-draft", path, inputs)
    record = document_file_service.read_follow_up_records(session.ctx.paths)["executive-summary-draft"]
    harness.check("kept from disk without a call", is_kept and session.model.call_count == 1, str(session.model.call_count))
    harness.check("its record now says accepted", record["status"] == "accepted" and record["reason"] == "", str(record))


def test_a_follow_up_that_spent_its_attempts_is_asked_again_on_a_resume():
    short = {**GOOD, "sentences": GOOD["sentences"][:1]}
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short),
                             limits=ConversationLimitsSpec(**{**LIMITS.__dict__, "max_attempts": 2}))
    writing = PartWritingContext(ctx=session.ctx, write_part=functools.partial(
        document_conversation_service.write_part_in_conversation, session))
    path = document_file_service.build_executive_summary_draft_path(session.ctx.paths)
    inputs = {"slug": "executive-summary-draft", "moments": [], "maxWords": 500}
    harness.check("blocked after its two attempts", not writing.write_part("executive_summary_draft", "executive-summary-draft", path, inputs))
    record = document_file_service.read_follow_up_records(session.ctx.paths)["executive-summary-draft"]
    harness.check("its outcome and why are on disk", record["status"] == "blocked"
                  and "EXECUTIVE_SUMMARY_DRAFT_LENGTH" in record["reason"], str(record))
    calls = session.model.call_count
    writing.write_part("executive_summary_draft", "executive-summary-draft", path, inputs)
    harness.check("asked again with the same inputs (a resume after the cause was fixed), it is asked, not skipped",
                  session.model.call_count > calls, str(session.model.call_count))


ENRICHMENT_INPUTS = {"slug": "enrichment-01", "noted": ENTRIES,
                     "segments": [{"id": "S0001", "text": "A split refund is booked as three separate payments."}],
                     "notes": "## F0 · 00:04 · slide\nSaid: S0001 A split refund is booked as three separate payments.\n"
                              "- F0.1 A refund can be split into at most three parts.\n"}
SAID = {"points": [{"text": "A split refund is booked as three separate payments.", "segment_ids": ["S0001"]}]}


class _Lines(logging.Handler):
    """Every line the run printed, as the operator reads it."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _capture_lines(session: WriteConversationState) -> _Lines:
    """The lines the session's run prints from now on: its context gets a logger of its own, at INFO."""

    logger = logging.getLogger(f"follow-up-lines-{id(session)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    lines = _Lines()
    logger.addHandler(lines)
    session.ctx = dataclasses.replace(session.ctx, log=logger)

    return lines


def test_a_follow_up_names_itself_once_when_it_starts():
    """The owner's rule: a unit prints its name when it starts and its outcome under it, never a second label."""

    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    lines = _capture_lines(session)
    writing = PartWritingContext(ctx=session.ctx, write_part=functools.partial(
        document_conversation_service.write_part_in_conversation, session))
    writing.write_part("executive_summary_draft", "executive-summary-draft", document_file_service.build_executive_summary_draft_path(session.ctx.paths),
                       {"slug": "executive-summary-draft", "moments": [], "maxWords": 500})
    named = [line for line in lines.lines if line.strip().split(" ")[0] == "executive-summary-draft"]
    harness.check("its name is printed once, when it starts", named == ["executive-summary-draft …"], "\n".join(lines.lines))
    harness.check("and its outcome under it", any(line.startswith("  ") and line.endswith("accepted") for line in lines.lines),
                  "\n".join(lines.lines))


def test_the_size_warning_is_left_out_of_a_follow_up_that_asks_for_none():
    """The owner, 2026-09-29: no warning while the draft headings are built."""

    from models.follow_up_spec import FollowUpSpec

    harness.check("a follow-up warns by default", FollowUpSpec(submit_tool="a", slug="b", inputs={}, text="c", position="d").is_size_warned)
    harness.check("and a draft headings follow-up says not to",
                  not FollowUpSpec(submit_tool="a", slug="b", inputs={}, text="c", position="d", is_size_warned=False).is_size_warned)


def test_a_follow_up_with_its_own_attempt_limit_uses_it():
    short = {**GOOD, "sentences": ["Refunds split."]}
    session = _build_session(*[fakes.tool_call("submit_executive_summary_draft", **short) for _ in range(3)],
                             fakes.tool_call("submit_executive_summary_draft", **GOOD))
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=4))
    harness.check("three rejected answers leave a fourth attempt when the follow-up allows four",
                  outcome.is_accepted and outcome.attempts == 4, str(outcome))


def test_report_problem_is_offered_in_every_follow_up_and_writes_to_the_run_folder():
    """The worker's channel to the people watching the run: offered beside the reading tools, carried out without
    ending the follow-up, and written into `worker-feedback.md` with where it was sent from."""

    session = _build_session(fakes.tool_call("report_problem", kind="unclear_instruction",
                                             summary="the draft length rule can be read two ways",
                                             detail="It says 3 to 8 sentences and also at most 500 words."),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD))
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("the follow-up still ends accepted", outcome.is_accepted and outcome.attempts == 1, str(outcome))
    harness.check("report_problem is offered", "report_problem" in session.model.bound[0], str(session.model.bound[0]))
    entries = worker_feedback_service.read_reports(session.ctx.paths.output_root / "worker-feedback.md")
    harness.check("one report in the run folder", len(entries) == 1 and entries[0].kind == "unclear_instruction",
                  str(entries))
    harness.check("it says which phase and follow-up it came from", "finalize" in entries[0].where
                  and "executive-summary-draft" in entries[0].where, str(entries))
    reply = next(message for message in session.model.calls[1] if isinstance(message, ToolMessage))
    harness.check("the worker is told it is on record", "on record" in reply.content, reply.content)


def test_every_rejection_asks_the_worker_to_report_a_wrong_check_or_an_unclear_instruction():
    short = {**GOOD, "sentences": GOOD["sentences"][:1]}
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD))
    write_conversation_service.run_follow_up(session, DRAFT)
    rejection = next(message for message in session.model.calls[1] if isinstance(message, ToolMessage))
    harness.check("the rejection names report_problem and the two kinds", "report_problem" in rejection.content
                  and "wrong_rejection" in rejection.content and "unclear_instruction" in rejection.content,
                  rejection.content)


def test_the_rejection_that_blocks_a_step_is_logged_with_its_findings():
    """With one attempt every rejection blocks its step: its findings must be in the conversation log, where the watch
    prints them for judging (2026-09-29: a blocked chapter's findings reached only the console)."""

    short = {**GOOD, "sentences": ["Refunds split."]}
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short))
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=1))
    rows = [json.loads(line) for line in session.conversation_log_path.read_text(encoding="utf-8").splitlines()]
    results = [row for row in rows if row.get("tool") == "submit_executive_summary_draft"]
    harness.check("blocked after its one answer", not outcome.is_accepted and outcome.attempts == 1, str(outcome))
    harness.check("the blocking rejection names no next attempt", "attempt 2 of 1" not in results[-1]["result"], results[-1]["result"])
    harness.check("the rejection is logged with its findings before the block",
                  results and results[-1]["result"].startswith("Rejected.") and "EXECUTIVE_SUMMARY_DRAFT_LENGTH" in results[-1]["result"]
                  and rows.index(results[-1]) < next(i for i, row in enumerate(rows) if "blocked" in row), str(rows[-3:]))


def test_a_follow_up_asked_again_after_its_block_opens_with_the_rejection_that_blocked_it():
    """With one attempt, a blocked follow-up is asked again on the next resume: it must see why its last answer was
    rejected, or it repeats the same omission (2026-09-29: one note left out four times in a row)."""

    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    session.conversation_log_path.write_text("\n".join(json.dumps(row) for row in (
        {"followUp": "executive-summary-draft", "sent": ["s"], "calls": [
            {"name": "submit_executive_summary_draft", "args": {"working_title": "Refunds", "sentences": ["Refunds split."]}}]},
        {"followUp": "executive-summary-draft", "tool": "submit_executive_summary_draft",
         "result": "Rejected.\n- EXECUTIVE_SUMMARY_DRAFT_LENGTH: the draft has 1 sentence(s) Fix: write three\nThis is attempt 2 of 1."},
        {"followUp": "executive-summary-draft", "blocked": "1 answers rejected: EXECUTIVE_SUMMARY_DRAFT_LENGTH"})) + "\n",
        encoding="utf-8")
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    first = _texts(session.model.calls[0])
    harness.check("accepted", outcome.is_accepted, str(outcome))
    harness.check("the first call carries the rejection that blocked this follow-up before, judged again now",
                  "EXECUTIVE_SUMMARY_DRAFT_LENGTH" in first and "judged again by the checks as they are now" in first, first[-600:])
    harness.check("and the answer it rejected, so what it got right can be kept", "Refunds split." in first, first[-600:])
    harness.check("never an attempt count from the life that blocked", "attempt 2 of 1" not in first, first[-600:])


def test_a_follow_up_blocked_by_a_refused_frame_tool_is_told_that_refusal_when_asked_again():
    """A chapter blocked on a refused mask (it would hide too much of a cut) was asked again with only its last answer's
    rejection, tried the same mask, and blocked again (2026-10-06)."""

    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    session.conversation_log_path.write_text("\n".join(json.dumps(row) for row in (
        {"followUp": "executive-summary-draft", "sent": ["s"], "calls": [
            {"name": "submit_executive_summary_draft", "args": {"working_title": "Refunds", "sentences": ["Refunds split."]}}]},
        {"followUp": "executive-summary-draft", "tool": "submit_executive_summary_draft",
         "result": "Rejected.\n- EXECUTIVE_SUMMARY_DRAFT_LENGTH: the draft has 1 sentence(s) Fix: write three"},
        {"followUp": "executive-summary-draft", "tool": "mask", "result": "Refused: this mask would hide 23% of cut-04"},
        {"followUp": "executive-summary-draft",
         "blocked": "`mask` was refused and `budgets.failOnFrameCutFail` stops the run: Refused: this mask would hide 23% of cut-04"})) + "\n",
        encoding="utf-8")
    write_conversation_service.run_follow_up(session, DRAFT)
    first = _texts(session.model.calls[0])
    harness.check("the frame tool's refusal that blocked it is in the first call", "would hide 23% of cut-04" in first, first[-800:])
    harness.check("and so is the answer's rejection", "EXECUTIVE_SUMMARY_DRAFT_LENGTH" in first, first[-800:])


def test_an_answer_that_blocked_is_judged_again_by_the_checks_as_they_are_now():
    """A check fixed after a block must not hold the answer back on resume, and a check that finds nothing now accepts
    the answer with no call (2026-09-30: a resume after a check fix carried the old finding)."""

    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    rows = ({"followUp": "executive-summary-draft", "sent": ["s"], "calls": [
                {"name": "submit_executive_summary_draft", "args": GOOD}]},
            {"followUp": "executive-summary-draft", "tool": "submit_executive_summary_draft",
             "result": "Rejected.\n- SOME_OLD_CHECK: a finding the check no longer makes"},
            {"followUp": "executive-summary-draft", "blocked": "1 answers rejected: SOME_OLD_CHECK"})
    session.conversation_log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    outcome = write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("accepted without a model call", outcome.is_accepted and not session.model.calls, str(outcome))
    harness.check("and the old finding is never shown", "SOME_OLD_CHECK" not in json.dumps([_texts(m) for m in session.messages]))


def test_a_cut_the_blocked_answer_made_does_not_make_the_request_a_new_one():
    """The blocked answer's own cuts are listed to the next life as cuts already made; that list growing is not a new
    request, so the rejection still opens the next life (2026-09-30: a chapter blocked on an unmasked secret was asked
    again with no word of why, because the cut it had made changed the request's hash)."""

    short = {**GOOD, "sentences": ["Refunds split."]}
    then = dataclasses.replace(DRAFT, max_attempts=1, inputs={**DRAFT.inputs, "sections": [
        {"entryIds": ["F0.1"], "existingCuts": ["cut-001 from frame-000"]}]})
    blocked = _build_session(fakes.tool_call("submit_executive_summary_draft", **short))
    write_conversation_service.run_follow_up(blocked, then)
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    session.conversation_log_path.write_text(blocked.conversation_log_path.read_text(encoding="utf-8"), encoding="utf-8")
    now = dataclasses.replace(then, inputs={**DRAFT.inputs, "sections": [
        {"entryIds": ["F0.1"], "existingCuts": ["cut-001 from frame-000", "cut-002 from frame-000"]}]})
    write_conversation_service.run_follow_up(session, now)
    first = _texts(session.model.calls[0])
    harness.check("the rejection that blocked is carried although a cut was made since",
                  "EXECUTIVE_SUMMARY_DRAFT_LENGTH" in first, first[-400:])


def test_a_rejection_from_a_request_of_another_shape_is_not_carried():
    """A follow-up asked again with other inputs (a chapter split into parts after it blocked) must not be told to keep
    an answer written for the old request: its notes are no longer this request's (2026-09-29)."""

    from utils import document_plan_utils
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    rows = ({"followUp": "executive-summary-draft", "sent": ["s"], "calls": [
                {"name": "submit_executive_summary_draft", "args": {"working_title": "Refunds", "sentences": ["Refunds split."]}}]},
            {"followUp": "executive-summary-draft", "tool": "submit_executive_summary_draft",
             "result": "Rejected.\n- EXECUTIVE_SUMMARY_DRAFT_LENGTH: the draft has 1 sentence(s) Fix: write three"},
            {"followUp": "executive-summary-draft", "blocked": "1 answers rejected: EXECUTIVE_SUMMARY_DRAFT_LENGTH",
             "inputsHash": document_plan_utils.compute_inputs_hash({"maxWords": 999})})
    session.conversation_log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    write_conversation_service.run_follow_up(session, DRAFT)
    first = _texts(session.model.calls[0])
    harness.check("inputs changed since the block: no rejection carried", "EXECUTIVE_SUMMARY_DRAFT_LENGTH" not in first, first[-400:])


def test_a_rejected_answer_is_first_asked_why_then_corrected_when_an_attempt_is_left():
    """The owner, 2026-09-30: after a rejection the worker says why its answer missed the instruction or the template,
    told plainly that it is not asked to correct yet; then, if an attempt is left, it is asked to correct; if not, the
    step blocks. A worker that sends no report is never waited for: the loop goes on."""

    short = {**GOOD, "sentences": ["Refunds split."]}
    asking = ConversationLimitsSpec(**{**LIMITS.__dict__, "should_ask_why_after_rejection": True})
    report = fakes.tool_call("report_problem", kind="unclear_instruction", summary="the length rule",
                             detail="The instruction gives no minimum sentence count.")

    # 1. With an attempt left: asked why, then asked to correct, and the correction is accepted
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short), report,
                             fakes.tool_call("submit_executive_summary_draft", **GOOD), limits=asking)
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=2))
    feedback = (session.ctx.paths.output_root / "worker-feedback.md").read_text(encoding="utf-8")
    asked = _texts(session.messages)
    harness.check("accepted on the second attempt", outcome.is_accepted and outcome.attempts == 2, str(outcome))
    harness.check("the question turn offered only report_problem", ["report_problem"] in session.model.bound,
                  str(session.model.bound))
    asking_turn = session.model.bound.index(["report_problem"])
    harness.check("the question turn has a small output limit, so a looping reasoning ends in minutes",
                  (session.model.output_limits[asking_turn] or 10**9) <= 8000, str(session.model.output_limits))
    harness.check("the question says not to correct yet, and the correction is asked after it",
                  "Do not correct" in asked and asked.index("Do not correct") < asked.index("Now correct"), asked[-1200:])
    harness.check("the report is in the run's feedback file", "the length rule" in feedback, feedback)

    # 2. On the last attempt: asked why, then blocked
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short), report, limits=asking)
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=1))
    asked = _texts(session.messages)
    harness.check("blocked after the question", not outcome.is_accepted and outcome.attempts == 1, str(outcome))
    harness.check("it was told no attempt is left", "No attempt is left" in asked and "Now correct" not in asked, asked[-600:])
    where = (session.ctx.paths.output_root / "worker-feedback.md").read_text(encoding="utf-8")
    harness.check("the report is filed under the attempt it is about, the one there was",
                  "attempt 1" in where and "attempt 2" not in where, where)

    # 3. No report: a reply in words is recorded, and the correction still comes
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short),
                             AIMessage(content="I left out two sentences."),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD), limits=asking)
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=2))
    feedback = (session.ctx.paths.output_root / "worker-feedback.md").read_text(encoding="utf-8")
    harness.check("a reply in words is recorded as its report", "I left out two sentences." in feedback, feedback)
    harness.check("and the loop goes on to the correction", outcome.is_accepted, str(outcome))

    # 3b. An answer written out instead of a report is noted in one line, never recorded whole (2026-09-30: a worker
    # asked why wrote its whole corrected chapter, and the feedback file held the chapter)
    corrected = "### Refunds (T01.1)\n\n" + "A refund can be split into at most three parts. " * 40
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short), AIMessage(content=corrected),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD), limits=asking)
    write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=2))
    feedback = (session.ctx.paths.output_root / "worker-feedback.md").read_text(encoding="utf-8")
    harness.check("an answer given instead of a report is noted, not copied", "instead of a report" in feedback
                  and feedback.count("at most three parts") == 0, feedback[-600:])

    # 4. An empty reply is no report, and nothing waits for one
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short), AIMessage(content=""),
                             fakes.tool_call("submit_executive_summary_draft", **GOOD), limits=asking)
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=2))
    harness.check("an empty reply is passed over", outcome.is_accepted, str(outcome))

    # 5. Switched off: no question
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **short))
    write_conversation_service.run_follow_up(session, dataclasses.replace(DRAFT, max_attempts=1))
    harness.check("switched off, the step blocks with no extra turn", len(session.model.bound) == 1, str(session.model.bound))

def test_a_chapter_that_failed_as_a_whole_keeps_no_section_as_passed():
    """A chapter whose earlier answer failed on its section count (a finding naming no section) had every section on
    disk read as passed, and a stale section from an older answer was put back over the new one (2026-09-30)."""

    from models.validation_report import ValidationReport
    from services import call_log_service, document_file_service, validation_service
    session = _build_session()
    spec = FollowUpSpec(submit_tool="submit_chapter", slug="chapter-01-text", position="chapter 1",
                        inputs={"sections": [{"slug": "heading-001-1"}]}, text="Write chapter 1.")
    log = call_log_service.build_call_log_path(session.ctx.paths, "chapter-01-text")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("{}", encoding="utf-8")
    document_file_service.write_section(session.ctx.paths, "heading-001-1", "Stale text. [F0.1]\n")
    whole = ValidationReport(subject="chapter-01-text")
    whole.add("DOCUMENT_CHAPTER_SECTIONS", "1 section(s) for 2 heading(s)", fix="write both")
    original = validation_service.validate_or_report_crash
    validation_service.validate_or_report_crash = lambda ctx, request: whole
    try:
        passed = write_conversation_service._read_passed_sections(session.ctx, spec, "chapter", "chapter-01-text")
    finally:
        validation_service.validate_or_report_crash = original
    harness.check("no section is kept as passed", passed == {}, str(passed))


def test_a_transcript_search_of_many_words_that_finds_nothing_says_why():
    """A worker searched the transcript for eleven words at once, twice, and read only "Nothing matches." (2026-09-30):
    a line must hold every word, and the reply says so."""

    from utils import reading_tool_utils
    reply = reading_tool_utils.explain_no_line(["refund", "limit", "approval", "time"])
    harness.check("the reply says every word must be in one line and how to ask instead",
                  "every word" in reply and "one or two words" in reply, reply)
    harness.check("one word that finds nothing stays short", reading_tool_utils.explain_no_line(["refund"]) == "Nothing matches.")


def test_an_empty_transcript_read_says_where_the_recording_ends():
    """A worker read from an hour past the recording's end and got "Nothing matches." (2026-09-30)."""

    from utils import reading_tool_utils
    reply = reading_tool_utils.explain_no_line([], 3600.0)
    harness.check("the reply gives the transcript's end", "00:00" in reply and "1:00:00" in reply, reply)


def test_lines_holding_two_of_three_or_more_words_are_ranked_when_none_holds_all():
    """A worker searched the transcript for alternatives, "latency delay fail error retry", and no line holds them all
    (2026-09-30): the lines holding the most of them come back."""

    from utils import reading_tool_utils
    lines = ["S0001 0:01 the refund fails with an error", "S0002 0:02 a retry after an error fails again",
             "S0003 0:03 nothing here"]
    ranked = reading_tool_utils.list_partial_lines(lines, ["retry", "error", "fail", "timeout"])
    harness.check("the line with three of the words comes first, the line with none is left out",
                  ranked == [lines[1], lines[0]], str(ranked))
    harness.check("two words give no partial lines", reading_tool_utils.list_partial_lines(lines, ["retry", "error"]) == [])
    timed = ["S1725 1:23:25 the totals were checked", "S1726 1:24:00 the feed moves in May"]
    texts = ["the totals were checked", "the feed moves in May"]
    harness.check("a line's time is never searched: '23' and '25' do not match a line spoken at 1:23:25",
                  reading_tool_utils.list_partial_lines(timed, ["may", "25", "august", "23"], texts) == [],
                  str(reading_tool_utils.list_partial_lines(timed, ["may", "25", "august", "23"], texts)))


def test_four_reads_in_a_row_with_no_note_bring_a_reminder_to_note():
    """A reviewer re-read the same minutes and re-derived one finding turn after turn, never calling `note`."""

    reads = [fakes.tool_call("find", words="refund") for _ in range(4)]
    session = _build_session(*reads, fakes.tool_call("submit_executive_summary_draft", **GOOD))
    write_conversation_service.run_follow_up(session, DRAFT)
    results = [message.content for message in session.model.calls[-1] if isinstance(message, ToolMessage)]
    harness.check("the fourth read's result asks for a note, the first three do not",
                  len(results) >= 4 and "call `note` now" in results[3]
                  and not any("call `note` now" in result for result in results[:3]), str([r[-80:] for r in results]))


def test_a_frame_tool_call_is_no_read_and_brings_no_reminder_to_note():
    """A worker masking a cut's secrets was told after each mask to note what its "reads" showed (2026-10-02): a look,
    a cut or a mask acts on a frame and reads nothing to note."""

    calls = [fakes.tool_call("find", words="refund") for _ in range(3)] + [fakes.tool_call("look", frame="frame-000")]
    session = _build_session(*calls, fakes.tool_call("submit_executive_summary_draft", **GOOD))
    write_conversation_service.run_follow_up(session, DRAFT)
    results = [message.content for message in session.model.calls[-1] if isinstance(message, ToolMessage)]
    harness.check("the look after three reads brings no reminder", len(results) >= 4
                  and not any("call `note` now" in result for result in results), str([r[-80:] for r in results]))


CHAPTER = FollowUpSpec(submit_tool="submit_chapter", slug="chapter-01-text", position="chapter 1",
                       inputs={"sections": [{"slug": "heading-001-1", "entryIds": ["F0.1"]}]}, text="Write chapter 1.")
CHAPTER_MARKDOWN = "### Refund limits (T01.1)\n\nA refund is split into at most three parts. [F0.1]\n"


def _judge_with(reports: list):
    """Replace the validator with one that hands out `reports` in order, and keeps whether each judged section was on
    disk; restore it with the returned function."""

    from services import validation_service
    original, seen = validation_service.validate_or_report_crash, []

    def judge(ctx, request):
        seen.append(document_file_service.build_section_path(ctx.paths, "heading-001-1").is_file())
        return reports.pop(0)

    validation_service.validate_or_report_crash = judge

    return seen, lambda: setattr(validation_service, "validate_or_report_crash", original)


def test_check_chapter_is_offered_only_beside_submit_chapter():
    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    write_conversation_service.run_follow_up(session, DRAFT)
    harness.check("an executive summary draft is not offered the chapter check",
                  "check_chapter" not in session.model.bound[0], str(session.model.bound[0]))
    session = _build_session(fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN), limits=DRY_RUNS)
    write_conversation_service.run_follow_up(session, CHAPTER)
    harness.check("a chapter is", "check_chapter" in session.model.bound[0], str(session.model.bound[0]))


def test_with_no_dry_runs_a_chapter_has_no_check_and_its_first_answer_is_judged():
    """The owner: no second chance in any context of the run. With `budgets.chapterDryRuns` off, `check_chapter` is
    not offered, and a chapter's first answer is judged, never checked first: a rejection blocks it."""

    from models.validation_report import ValidationReport
    rejected = ValidationReport(subject="chapter-01-text")
    rejected.add("DOCUMENT_BRACKET_UNREAD", "heading-001-1: [F0.1 context] is no note id", fix="name note ids only")
    seen, restore = _judge_with([rejected, ValidationReport(subject="chapter-01-text")])
    session = _build_session(fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN))
    try:
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    harness.check("no chapter check offered", "check_chapter" not in session.model.bound[0], str(session.model.bound[0]))
    harness.check("the first answer is judged and blocks", not outcome.is_accepted and len(seen) == 1,
                  f"{outcome} {seen}")


def test_a_reply_the_host_cannot_read_as_a_tool_call_is_asked_again_defect():
    # Defect: a chapter part blocked after 0 attempts when LM Studio could not read the model's reply as a tool call
    # ("does not match the expected peg-native format"). Fix: that reply spends a call, and the worker is asked again.
    from models.validation_report import ValidationReport
    seen, restore = _judge_with([ValidationReport(subject="chapter-01-text")])
    session = _build_session(fakes.host_error('{"code":500,"message":"The model produced output that does not match '
                                              'the expected peg-native format","type":"server_error"}'),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN))
    try:
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    harness.check("the chapter is accepted on its next reply", outcome.is_accepted and len(seen) == 1, f"{outcome} {seen}")
    harness.check("the worker is told its reply was not read", "could not be read" in _texts(session.model.calls[-1]),
                  _texts(session.model.calls[-1])[-300:])
    down = _build_session(fakes.host_error("Connection refused"))
    harness.check("a host that is down still blocks the step",
                  not write_conversation_service.run_follow_up(down, CHAPTER).is_accepted)


def test_a_checked_draft_names_its_findings_and_spends_no_attempt():
    """Three chapter answers of one run in a row were blocked for an id left out of the brackets, each on its one
    answer: a draft checked first is told what the check would name, and its answer is still to come."""

    from models.validation_report import ValidationReport
    rejected = ValidationReport(subject="chapter-01-text")
    rejected.add("DOCUMENT_ENTRY_UNCOVERED", "heading-001-1: no block of the section uses F0.2", fix="cite F0.2")
    seen, restore = _judge_with([rejected, ValidationReport(subject="chapter-01-text"),
                                 ValidationReport(subject="chapter-01-text")])
    session = _build_session(fakes.tool_call("check_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN), limits=DRY_RUNS)
    try:
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    harness.check("accepted on its one answer, the check spending none",
                  outcome.is_accepted and outcome.attempts == 1, str(outcome))
    results = [message.content for message in session.messages if isinstance(message, ToolMessage)]
    harness.check("the check's result names the finding and says the draft would be rejected",
                  any("would be rejected" in text and "DOCUMENT_ENTRY_UNCOVERED" in text and "F0.2" in text
                      for text in results), str(results))
    harness.check("a failed check invites a report, as a rejection does",
                  any("would be rejected" in text and "wrong_rejection" in text for text in results), str(results))
    harness.check("the draft was on disk while it was judged", seen == [True, True, True], str(seen))


def test_a_check_names_the_notes_an_earlier_draft_covered_and_this_one_lost():
    """A rewrite made to cite 20 missing notes dropped 17 others the draft before it covered, and took three more checks
    to put them back (lecture-9, chapter 7)."""

    from models.validation_report import ValidationReport
    first = ValidationReport(subject="chapter-01-text")
    first.add("DOCUMENT_ENTRY_UNCOVERED", "heading-001-1: no block of the section uses F0.2", fix="cite F0.2")
    second = ValidationReport(subject="chapter-01-text")
    second.add("DOCUMENT_ENTRY_UNCOVERED", "heading-001-1: no block of the section uses F0.1, F0.3", fix="cite them")
    passed = [ValidationReport(subject="chapter-01-text") for _ in range(4)]
    _, restore = _judge_with([first, second, *passed])
    rewritten = "### Refund limits (T01.1)\n\nA refund needs an approver. [F0.2]\n"
    session = _build_session(fakes.tool_call("check_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("check_chapter", markdown=rewritten),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN), limits=DRY_RUNS)
    try:
        write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    results = [message.content for message in session.messages if isinstance(message, ToolMessage)]
    harness.check("the first check names no earlier draft", "earlier draft" not in results[0], results[0])
    harness.check("the second names the note the first draft cited and this one lost, never one no draft cited",
                  "an earlier draft of this chapter covered F0.1, and this one does not" in results[1], results[1])


def test_an_answer_after_a_failed_check_is_told_no_check_has_passed():
    """An answer sent after a `check_chapter` that failed was told "no `check_chapter` came before this answer", which
    was untrue, and the writer took it as an order to call the tool it had just called."""

    from models.validation_report import ValidationReport
    rejected = ValidationReport(subject="chapter-01-text")
    rejected.add("DOCUMENT_ENTRY_UNCOVERED", "heading-001-1: no block of the section uses F0.2", fix="cite F0.2")
    _, restore = _judge_with([rejected, rejected, ValidationReport(subject="chapter-01-text"),
                              ValidationReport(subject="chapter-01-text")])
    session = _build_session(fakes.tool_call("check_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN), limits=DRY_RUNS)
    try:
        write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    results = [message.content for message in session.messages if isinstance(message, ToolMessage)]
    checked_first = [text for text in results if text.startswith("Not judged yet")]
    harness.check("the answer after the failed check is told no check has passed yet, never that none came before",
                  len(checked_first) == 1 and "has passed" in checked_first[0] and "came before" not in checked_first[0],
                  str(checked_first))


def test_a_check_puts_back_every_file_it_wrote():
    """A draft never stands in for an answer: a section an earlier answer wrote is as it was after a check, and a file
    no answer wrote is gone again."""

    from models.validation_report import ValidationReport
    from services import call_log_service
    session = _build_session()
    document_file_service.write_section(session.ctx.paths, "heading-001-1", "Earlier text. [F0.1]\n")
    _, restore = _judge_with([ValidationReport(subject="chapter-01-text")])
    try:
        result = write_conversation_service._check_draft(session, CHAPTER, {"markdown": CHAPTER_MARKDOWN},
                                                         AIMessage(content=""))
    finally:
        restore()
    harness.check("a passing draft is told to submit", result.startswith("The draft passes"), result)
    section = document_file_service.build_section_path(session.ctx.paths, "heading-001-1").read_text(encoding="utf-8")
    harness.check("the earlier section is back word for word", section == "Earlier text. [F0.1]\n", section)
    harness.check("the call log and the chapter answer no answer wrote are gone",
                  not call_log_service.build_call_log_path(session.ctx.paths, "chapter-01-text").exists()
                  and not document_file_service.build_chapter_submission_path(session.ctx.paths, "chapter-01-text").exists(), "")


def test_a_chapter_submitted_with_no_check_before_it_is_checked_first_and_spends_no_answer():
    """The writer never called `check_chapter`, and a chapter answer of the same run was blocked on findings it names:
    the first answer with no check before it is checked, its findings come back, and the next answer is judged."""

    from models.validation_report import ValidationReport
    rejected = ValidationReport(subject="chapter-01-text")
    rejected.add("DOCUMENT_BRACKET_UNREAD", "heading-001-1: [F0.1 context] is no note id", fix="name note ids only")
    # A check that found something leaves the answer unchecked: the fixed answer is checked again, not judged, so a
    # second miss in the fix does not cost the chapter's one answer (2026-10-07)
    seen, restore = _judge_with([rejected, ValidationReport(subject="chapter-01-text"),
                                 ValidationReport(subject="chapter-01-text")])
    session = _build_session(fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN), limits=DRY_RUNS)
    try:
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    harness.check("accepted on its one answer: the first was checked, not judged",
                  outcome.is_accepted and outcome.attempts == 1, str(outcome))
    results = [message.content for message in session.messages if isinstance(message, ToolMessage)]
    harness.check("the first answer's result says it was checked first and names the finding",
                  any(text.startswith("Not judged yet") and "DOCUMENT_BRACKET_UNREAD" in text for text in results),
                  str(results))
    harness.check("checked until it passes, then judged once", len(seen) == 3, str(seen))


def test_a_search_that_finds_nothing_names_the_transcript_language():
    """A reviewer searched a German transcript in English words three times in a row (2026-10-03)."""

    from utils import reading_tool_utils
    reply = reading_tool_utils.explain_no_line(["refund", "limit"], 600.0, "de")
    harness.check("the reply names the language and says to search in it, never with a translation",
                  "(de)" in reply and "never with a translation" in reply, reply)
    harness.check("without a known language it says nothing about one",
                  "language" not in reading_tool_utils.explain_no_line(["refund", "limit"], 600.0))


def _run_with_refused_cut(limits: ConversationLimitsSpec):
    """A chapter whose writer makes one cut the frame tools refuse, then submits."""

    from models.write_read_result import WriteReadResult
    refused = WriteReadResult(text="[r1] Refused: frame-000 has 3 cuts already", is_frame_tool_refused=True)
    original = write_conversation_service._READERS["write"]
    write_conversation_service._READERS["write"] = lambda *arguments: refused
    session = _build_session(fakes.tool_call("cut", frame="frame-000", rect=[0, 0, 10, 10]),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN), limits=limits)
    try:
        return write_conversation_service.run_follow_up(session, CHAPTER)
    finally:
        write_conversation_service._READERS["write"] = original


def test_a_refused_frame_tool_stops_the_run_when_fail_on_frame_cut_fail_is_on():
    """The owner: "when agent reaches the cutsPerFrame 3 or when it fail in general, the run should be stopped"."""

    outcome = _run_with_refused_cut(ConversationLimitsSpec(**{**LIMITS.__dict__,
                                                            "should_stop_on_frame_tool_refusal": True}))
    harness.check("blocked on the refusal, the answer never asked", not outcome.is_accepted
                  and "failOnFrameCutFail" in outcome.reason and outcome.attempts == 0, str(outcome))
    outcome = _run_with_refused_cut(LIMITS)
    harness.check("off, the writer reads the refusal and its answer is judged",
                  outcome.attempts >= 1 and "failOnFrameCutFail" not in outcome.reason, str(outcome))


def test_a_follow_up_taken_back_by_a_reset_starts_clean():
    """`reset-chapter` removed a blocked chapter's text, and the chapter written again still opened with that text's
    rejection, read back from the log the reset keeps as history (2026-10-04)."""

    session = _build_session(fakes.tool_call("submit_executive_summary_draft", **GOOD))
    session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    session.conversation_log_path.write_text("\n".join(json.dumps(row) for row in (
        {"followUp": "executive-summary-draft", "sent": ["s"], "calls": [
            {"name": "submit_executive_summary_draft", "args": {"working_title": "Refunds", "sentences": ["Refunds split."]}}]},
        {"followUp": "executive-summary-draft", "tool": "submit_executive_summary_draft",
         "result": "Rejected.\n- EXECUTIVE_SUMMARY_DRAFT_LENGTH: the draft has 1 sentence(s) Fix: write three"},
        {"followUp": "executive-summary-draft", "blocked": "1 answers rejected: EXECUTIVE_SUMMARY_DRAFT_LENGTH"},
        {"followUp": "executive-summary-draft", "reset": True})) + "\n", encoding="utf-8")
    write_conversation_service.run_follow_up(session, DRAFT)
    first = _texts(session.model.calls[0])
    harness.check("the request opens with no rejection and no earlier answer",
                  "EXECUTIVE_SUMMARY_DRAFT_LENGTH" not in first and "Refunds split." not in first, first[-600:])


def test_phase_4_reads_its_output_limit_from_the_default_model_when_no_document_model_is_set():
    """The model is built through `resolve_phase_model`, which falls back to the `default` model; the limits must read the
    same model, or settings that give only `default` leave the conversation an output limit of 0."""

    import config as app_config
    from unittest import mock

    session = _build_session()
    only_default = {"default": app_config.ModelSettings(spec="ollama:some-model", max_tokens=12_345)}
    with mock.patch.object(app_config.settings, "models", only_default):
        state = document_conversation_service.open_conversation(session.ctx, ENTRIES, [], [], max_attempts=1)
    harness.check("the default model's output limit reaches the conversation", state.limits.max_output_tokens == 12_345,
                  str(state.limits.max_output_tokens))


def test_find_reaches_its_second_chunk():
    """A search whose matches fill two chunks says "ask for chunk 2"; asking must return the second chunk."""

    entries = [{"id": f"F{index}.1", "kind": "facts", "text": f"Refund rule number {index} " + "detail " * 30,
                "timestamps": [index * 60.0], "frames": [index], "tags": ["refund"]} for index in range(12)]
    context = WriteReadingContext(paths=_build_session().ctx.paths, moments=write_reading_service.build_reading_moments(entries),
                                  point_starts=[], frame_items={}, frame_records={}, read_chars=600, notes_chars=400,
                                  read_budget=10)
    first = write_reading_service.run_read(context, WriteReadingState(), "find", {"words": "refund"}).text
    second = write_reading_service.run_read(context, WriteReadingState(), "find", {"words": "refund", "chunk": 2}).text
    harness.check("the first chunk offers a second", "ask for chunk 2" in first, first[-200:])
    harness.check("chunk 2 is the second chunk, not the first again", "chunk 2 of" in second or second != first, second[-200:])


def test_a_chapter_answer_with_its_headings_swapped_files_each_text_under_its_own_heading():
    from models.attempt_request import AttemptRequest
    from services import chain_dispatch_service

    session = _build_session()
    paths = session.ctx.paths
    sections = [{"slug": "heading-001-1", "topicId": "T02.1", "entryIds": ["F1.1"]},
                {"slug": "heading-002-1", "topicId": "T02.2", "entryIds": ["F2.1"]}]
    markdown = "### Second (T02.2)\n\nThe second heading's text. [F2.1]\n\n### First (T02.1)\n\nThe first heading's text. [F1.1]\n"
    request = AttemptRequest(job_kind="chapter", attempt=1, repository=session.ctx.repository, module=session.ctx.module,
                             paths=paths, ignore=session.ctx.ignore, inputs={"slug": "chapter-02-text", "sections": sections})
    chain_dispatch_service.write_record(request, {"markdown": markdown})
    first = document_file_service.build_section_path(paths, "heading-001-1").read_text(encoding="utf-8")
    harness.check("T02.1's text is filed under T02.1's section", "first heading's text" in first, first)


def test_a_look_remembered_by_an_earlier_session_is_forgotten_when_the_conversation_opens():
    session = _build_session()
    document_file_service.write_image_store(session.ctx.paths, {"cuts": {}, "looks": {}, "lastCall": ["look", '{"frame": "frame-000"}']})
    state = document_conversation_service.open_conversation(session.ctx, ENTRIES, [], [], max_attempts=1)
    harness.check("the new conversation holds no last call: it never saw that look's result",
                  "lastCall" not in state.reading_state.image_store, str(state.reading_state.image_store))


def test_a_blocked_answer_logged_without_a_hash_that_names_a_foreign_note_is_not_carried():
    from types import SimpleNamespace

    log = Path(tempfile.mkdtemp()) / "conversation.jsonl"
    slug = "chapter-01-text"
    rows = [{"followUp": slug, "calls": [{"name": "submit_chapter", "args": {"markdown": "A sentence. [F9.9]"}}]},
            {"followUp": slug, "tool": "submit_chapter", "result": "Rejected. DOCUMENT_UNKNOWN_SOURCE: F9.9"},
            {"followUp": slug, "blocked": "the attempts are spent"}]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    spec = SimpleNamespace(slug=slug, submit_tool="submit_chapter", inputs={"sections": [{"entryIds": ["F1.1"]}]})
    carried = write_conversation_service._read_blocking_rejection(SimpleNamespace(conversation_log_path=log), spec)
    harness.check("an answer citing a note outside this request starts clean", carried == ("", None), str(carried))


def test_a_read_whose_number_is_sent_as_words_is_answered_by_name():
    session = _build_session()
    answers = []
    for name, args in (("read_frame", {"frame": "frame-019"}), ("find", {"words": "refund", "chunk": "next"}),
                       ("read_moments", {"tag": "reference", "chunk": "two"}), ("read_document", {"chapter": "two"}), ("read_moments", {"tag": "reference", "chunk": 2.5})):
        try:
            answers.append(write_reading_service.run_read(session.reading, session.reading_state, name, args).text)
        except Exception as exc:
            answers.append(f"{type(exc).__name__}: {exc}")
    harness.check("each names the argument and what it takes", all("whole number" in answer for answer in answers),
                  str(answers))


def test_an_empty_chapter_is_no_chapter():
    session = _build_session()
    try:
        answer = write_reading_service.run_read(session.reading, session.reading_state, "read_notes", {"chapter": ""}).text
    except Exception as exc:
        answer = f"{type(exc).__name__}: {exc}"
    harness.check("read as the whole meeting, never as a chapter named nothing", "chapter ." not in answer
                  and "Error" not in answer, answer)


def test_a_check_keeps_a_heading_that_passed_on_an_earlier_answer_as_the_answer_would():
    """`submit_chapter` puts back a heading that passed before when the new answer breaks it; `check_chapter`, the gate
    every chapter answer passes first, judged the draft without it."""

    from models.validation_report import ValidationReport
    from services import call_log_service
    session = _build_session()
    document_file_service.write_section(session.ctx.paths, "heading-001-1", "Earlier text that passed. [F0.1]\n")
    call_log_service.write_call_log(call_log_service.build_call_log_path(session.ctx.paths, "chapter-01-text"),
                                    {"response": {"outcome": "completed"}})
    broken = ValidationReport(subject="chapter-01-text")
    broken.add("DOCUMENT_ENTRY_UNCOVERED", "heading-001-1: no block of the section uses F0.1", fix="cite F0.1")
    # The judge passes the heading as it passed before, and names it broken as the draft writes it
    from services import validation_service
    original = validation_service.validate_or_report_crash

    def judge(ctx, request):
        section = document_file_service.build_section_path(ctx.paths, "heading-001-1").read_text(encoding="utf-8")
        return ValidationReport(subject="chapter-01-text") if "Earlier text" in section else broken

    validation_service.validate_or_report_crash = judge
    try:
        result = write_conversation_service._check_draft(session, CHAPTER, {"markdown": CHAPTER_MARKDOWN},
                                                         AIMessage(content=""))
    finally:
        validation_service.validate_or_report_crash = original
    harness.check("the draft passes, its broken heading kept as it passed", result.startswith("The draft passes"), result)


def test_a_passed_heading_put_back_that_breaks_the_answer_s_other_heading_is_not_kept_defect():
    # A heading that passed beside the old answer's other heading was put back beside the new one, which no longer
    # covered a note the old one did: the check named that note, not the draft's own fault. Such a put-back is undone
    from models.validation_report import ValidationReport
    from services import call_log_service
    from services import validation_service
    session = _build_session()
    spec = FollowUpSpec(submit_tool="submit_chapter", slug="chapter-01-text", position="chapter 1",
                        inputs={"sections": [{"slug": "heading-001-1", "entryIds": ["F0.1"], "heading": "One",
                                              "topicId": "T01.1"},
                                             {"slug": "heading-002-1", "entryIds": ["F0.2"], "heading": "Two",
                                              "topicId": "T01.2"}]}, text="Write chapter 1.")
    document_file_service.write_section(session.ctx.paths, "heading-001-1", "Old first. [F0.1, F0.2]\n")
    document_file_service.write_section(session.ctx.paths, "heading-002-1", "Old second. [F0.2]\n")
    call_log_service.write_call_log(call_log_service.build_call_log_path(session.ctx.paths, "chapter-01-text"),
                                    {"response": {"outcome": "completed"}})

    def judge(ctx, request):
        first, second = (document_file_service.build_section_path(ctx.paths, slug).read_text(encoding="utf-8")
                         for slug in ("heading-001-1", "heading-002-1"))
        report = ValidationReport(subject="chapter-01-text")
        if "Old first" in first:
            return report
        if "Old second" in second:
            report.add("DOCUMENT_ENTRY_UNCOVERED", "heading-002-1: no block of the section uses F0.9", fix="cite F0.9")
        else:
            report.add("DOCUMENT_RECORDING_NARRATED", "heading-002-1: 1 sentence(s) tell what happened", fix="state it")
        return report

    original = validation_service.validate_or_report_crash
    validation_service.validate_or_report_crash = judge
    markdown = "### One (T01.1)\n\nNew first. [F0.1]\n\n### Two (T01.2)\n\nThe talk then covers it. [F0.2, F0.9]\n"
    try:
        result = write_conversation_service._check_draft(session, spec, {"markdown": markdown}, AIMessage(content=""))
    finally:
        validation_service.validate_or_report_crash = original
    harness.check("the draft is told its own fault, not the put-back's",
                  "DOCUMENT_RECORDING_NARRATED" in result and "DOCUMENT_ENTRY_UNCOVERED" not in result, result)


def test_a_chapter_whose_title_starts_like_a_fixed_section_is_still_a_chapter():
    session = _build_session()
    path = document_file_service.build_working_draft_path(session.ctx.paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Weekly\n\n## Executive summary\n\nSummary.\n\n## Contents of the release\n\nFirst chapter.\n\n"
                    "## Rollout\n\nSecond chapter.\n", encoding="utf-8")
    first = write_reading_service.run_read(session.reading, session.reading_state, "read_document", {"chapter": 1}).text
    second = write_reading_service.run_read(session.reading, session.reading_state, "read_document", {"chapter": 2}).text
    harness.check("chapter 1 is 'Contents of the release', chapter 2 'Rollout'",
                  "Contents of the release" in first and "Rollout" in second, f"{first!r} / {second!r}")


def test_a_conversation_row_after_a_line_a_kill_cut_in_half_is_read():
    log = Path(tempfile.mkdtemp()) / "logs" / "conversation.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text('{"followUp": "chapter-01", "blocked": "the killed li', encoding="utf-8")
    write_conversation_service._append_conversation_log_row(types.SimpleNamespace(conversation_log_path=log),
                                                  {"followUp": "chapter-01", "accepted": 1})
    rows = conversation_log_service.read_rows(log)
    harness.check("the row written after the torn line reads", [row.get("accepted") for row in rows] == [1], str(rows))


def test_a_frame_tool_block_is_told_to_a_resumed_life_whatever_its_reason_says():
    from types import SimpleNamespace

    log = Path(tempfile.mkdtemp()) / "conversation.jsonl"
    slug = "chapter-01-text"
    rows = [{"followUp": slug, "calls": [{"name": "submit_chapter", "args": {"markdown": "A sentence. [F1.1]"}}]},
            {"followUp": slug, "tool": "submit_chapter", "result": "Rejected.\n- DOCUMENT_EMPTY: nothing"},
            {"followUp": slug, "blocked": "`mask` stopped the run: no box C9", "toolBlock": "no box C9"}]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    spec = SimpleNamespace(slug=slug, submit_tool="submit_chapter", inputs={"sections": [{"entryIds": ["F1.1"]}]})
    carried, _ = write_conversation_service._read_blocking_rejection(SimpleNamespace(conversation_log_path=log), spec)
    harness.check("the block is carried by its own field, not by two words of its sentence", "no box C9" in carried, carried)


def test_a_calls_thinking_is_written_beside_the_conversation_log_while_it_streams():
    thinking = " ".join(f"Point {number} of the agenda goes after point {number - 1}." for number in range(1, 80))
    answer = fakes.tool_call("submit_executive_summary_draft", **GOOD)
    answer.additional_kwargs["reasoning_content"] = thinking
    session = _build_session(answer)
    write_conversation_service.run_follow_up(session, DRAFT)
    live = session.conversation_log_path.with_name(write_conversation_service.THINKING_LIVE_NAME)
    harness.check("the thinking so far is on disk beside the log, so a long call can be told from a loop",
                  live.is_file() and live.read_text(encoding="utf-8") == thinking, str(live))


def test_an_agenda_answer_that_would_be_refused_is_checked_first_and_spends_no_answer_defect():
    # Defect: the agenda's one answer titled its first point as who presents, a rule the instructions state, and the run
    # blocked. Fix: the agenda's answer is checked first, as a chapter's is: its findings come back unjudged, no answer spent.
    narrated = {"announced_agenda": [], "topic": "refund splits", "event_name": "Gateway review",
                "points": [{"title": "Speaker introduction and the gateway", "first_moment": 1}]}
    good = {**narrated, "points": [{"title": "Refund splits at the gateway", "first_moment": 1}]}
    session = _build_session(fakes.tool_call("submit_agenda", **narrated), fakes.tool_call("submit_agenda", **good))
    agenda = FollowUpSpec(submit_tool="submit_agenda", slug=document_file_service.PARTS_SLUG,
                          inputs={"moments": [{"number": 1}]}, text="Write the agenda.", position="the agenda")
    outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(agenda, max_attempts=1))
    first = next(message for message in session.model.calls[1] if isinstance(message, ToolMessage))
    harness.check("the first answer comes back with its findings, unjudged", "checked first and spends no answer" in first.content,
                  first.content)
    harness.check("the second answer, right, is accepted on the one attempt", outcome.is_accepted and outcome.attempts == 1, str(outcome))


def test_a_headings_answer_that_would_be_refused_is_checked_first_and_spends_no_answer_defect():
    # Defect: a chapter's one headings answer narrated the talk in a heading ("Speaker introduction ...") and the run
    # blocked. Fix: the draft headings' answer is checked first, as the agenda's is: findings come back, no answer spent.
    first_id = ENTRIES[0]["id"]
    narrated = {"purpose": "Show the refund splits.",
                "headings": [{"title": "Speaker introduction as gateway maintainer", "first_entry_id": first_id,
                              "what": "Speaker introduction as gateway maintainer."}]}
    good = {**narrated, "headings": [{"title": "Refund splits at the gateway", "first_entry_id": first_id,
                                      "what": "How the gateway splits a refund."}]}
    session = _build_session(fakes.tool_call("submit_draft_headings", **narrated),
                             fakes.tool_call("submit_draft_headings", **good))
    slug = document_file_service.build_draft_headings_slug(1)
    headings = FollowUpSpec(submit_tool="submit_draft_headings", slug=slug, max_attempts=1,
                            inputs={"slug": slug, "chapter": 1, "chapterCount": 1, "title": "Refunds", "chunkCount": 0,
                                    "entries": ENTRIES, "documentHeadings": [], "chapterMinutes": 4, "startIds": [first_id]},
                            text="Draft the headings of chapter 1.", position="the headings of chapter 1")
    outcome = write_conversation_service.run_follow_up(session, headings)
    first = next(message for message in session.model.calls[1] if isinstance(message, ToolMessage))
    harness.check("the first answer comes back with its findings, unjudged", "checked first and spends no answer" in first.content,
                  first.content)
    harness.check("the second answer, right, is accepted on the one attempt", outcome.is_accepted and outcome.attempts == 1, str(outcome))


def test_a_chapter_that_repeats_its_last_lines_is_checked_once_and_spends_no_attempt_defect():
    # A chapter written whole, then its set-aside lines over and over until the stream stopped it, was dropped after
    # about 800 s, twice in one run; its text up to the repeats now goes to the dry check, never judged as the answer
    from models.validation_report import ValidationReport
    from chains import chain_runner
    looped = CHAPTER_MARKDOWN + "\n" + "> **Set aside:** a note about where the presenter stood. [F0.2]\n\n" * 80
    harness.check("the repeated lines are cut to one copy",
                  chain_runner.drop_repeated_tail(looped).count("> **Set aside:**") == 1,
                  str(chain_runner.drop_repeated_tail(looped).count("> **Set aside:**")))
    seen, restore = _judge_with([ValidationReport(subject="chapter-01-text"), ValidationReport(subject="chapter-01-text")])
    session = _build_session(AIMessage(content=looped), fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN),
                             limits=DRY_RUNS)
    try:
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    results = [str(message.content) for message in session.messages if isinstance(message, (ToolMessage, HumanMessage))]
    harness.check("the looped chapter was checked and the worker told why",
                  any("repeated its last lines" in text for text in results), str(results)[:400])
    harness.check("and the answer after it is accepted on its one attempt",
                  outcome.is_accepted and outcome.attempts == 1, str(outcome))


def test_a_frame_tool_s_result_names_the_headings_the_follow_up_asks_defect():
    # After a resume a worker answered chapter 3's and then chapter 4's heading inside chapter 2's follow-up, from its
    # own memory of the run; every frame tool's result now says which headings this follow-up asks for
    spec = dataclasses.replace(CHAPTER, inputs={"sections": [{"slug": "heading-001-1", "entryIds": ["F0.1"],
                                                              "heading": "Refund limits", "topicId": "T01.1"}]})
    session = _build_session(fakes.tool_call("look", frame="frame-000"),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN))
    write_conversation_service.run_follow_up(session, spec)
    results = [str(message.content) for call in session.model.calls for message in call if isinstance(message, ToolMessage)]
    harness.check("the look's result names chapter 1 and its one heading",
                  any("This follow-up asks for chapter 1 and only these headings: ### Refund limits (T01.1)" in text
                      for text in results), str([text[-200:] for text in results]))


def test_a_chapter_draft_that_passes_its_check_is_the_answer_defect():
    # A writer told its draft passed and to submit the same markdown wrote notes that the chapter was accepted, until it
    # spent its 40 calls and blocked. Now a draft that passes its check is judged as the answer at once
    from models.validation_report import ValidationReport
    seen, restore = _judge_with([ValidationReport(subject="chapter-01-text") for _ in range(3)])
    session = _build_session(fakes.tool_call("check_chapter", markdown=CHAPTER_MARKDOWN),
                             fakes.tool_call("note", text="Chapter accepted."), limits=DRY_RUNS)
    try:
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    harness.check("the passing check is accepted as the answer", outcome.is_accepted, str(outcome))
    harness.check("no note turn was needed", len(session.model.calls) == 1, str(len(session.model.calls)))


def test_a_reply_with_no_usage_keeps_the_conversation_s_last_size_defect():
    # A stream the loop test stopped brought no usage, and the conversation's size was taken as 0, its row logging
    # "prompt 0 tokens". Now the last count the host gave stays, and the row logs the prompt as not known
    import json
    from models.validation_report import ValidationReport
    looped = CHAPTER_MARKDOWN + "\n" + "> **Set aside:** a note about where the presenter stood. [F0.2]\n\n" * 80
    failing = ValidationReport(subject="chapter-01-text")
    failing.add("DOCUMENT_ENTRY_UNCOVERED", "no block of the section uses F0.2", fix="cite it")
    _, restore = _judge_with([failing] + [ValidationReport(subject="chapter-01-text") for _ in range(3)])
    counted = fakes.tool_call("check_chapter", markdown=CHAPTER_MARKDOWN)
    counted.usage_metadata = {"input_tokens": 50_000, "output_tokens": 900, "total_tokens": 50_900}
    session = _build_session(counted, AIMessage(content=looped),
                             fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN), limits=DRY_RUNS)
    try:
        write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    harness.check("the last reported size stays", session.reported_tokens == 50_000, str(session.reported_tokens))
    rows = [json.loads(line) for line in session.conversation_log_path.read_text(encoding="utf-8").splitlines()]
    turns = [row for row in rows if "promptTokens" in row]
    harness.check("the looped turn's prompt is logged as not known", len(turns) > 1 and turns[1]["promptTokens"] is None,
                  str([row["promptTokens"] for row in turns]))


def test_a_looped_chapter_s_text_is_never_handed_back_to_the_worker_defect():
    # A looped chapter's text, kept at its first characters, was written again as text and looped again, six replies in
    # a row; the next call now sees one line saying the reply was stopped, and is told to use the check's argument
    from models.validation_report import ValidationReport
    looped = CHAPTER_MARKDOWN + "\n" + "> **Set aside:** a note about where the presenter stood. [F0.2]\n\n" * 80
    seen, restore = _judge_with([ValidationReport(subject="chapter-01-text"), ValidationReport(subject="chapter-01-text")])
    session = _build_session(AIMessage(content=looped), fakes.tool_call("submit_chapter", markdown=CHAPTER_MARKDOWN),
                             limits=DRY_RUNS)
    try:
        write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        restore()
    later = session.model.calls[1] if len(session.model.calls) > 1 else []
    replies = [str(message.content) for message in later if isinstance(message, AIMessage)]
    told = [str(message.content) for message in later if isinstance(message, (HumanMessage, ToolMessage))]
    harness.check("no earlier reply handed back holds the chapter's text",
                  not any("Refund limits" in text for text in replies), str([text[:120] for text in replies]))
    harness.check("the worker is told to write its draft as the check's argument",
                  any("`markdown` argument of a `check_chapter` call" in text for text in told), str([t[-200:] for t in told]))


def test_a_final_summary_that_would_be_refused_is_checked_first_and_spends_no_answer_defect():
    # Defect: the final summary's first answer opened "The presentation traces ...", narration the instruction forbids,
    # and spent an attempt. Fix: its first answer is checked first, as the agenda's is: findings back, no answer spent.
    narrated = {"title": "Refunds split at the gateway",
                "summary": [{"text": "The presentation traces how the gateway splits each refund.", "entry_ids": ["F1.1"]}],
                "agenda": []}
    good = {**narrated, "summary": [{"text": "The gateway splits each refund into three ledger postings.",
                                     "entry_ids": ["F1.1"]}]}
    session = _build_session(fakes.tool_call("submit_final", **narrated), fakes.tool_call("submit_final", **good))
    final = FollowUpSpec(submit_tool="submit_final", slug=document_file_service.SUMMARY_SLUG, max_attempts=1,
                         inputs={"parts": [], "knownEntryIds": ["F1.1"], "maxWords": 500},
                         text="Write the final title and summary.", position="the final summary")
    outcome = write_conversation_service.run_follow_up(session, final)
    first = next(message for message in session.model.calls[1] if isinstance(message, ToolMessage))
    harness.check("the first answer comes back with its findings, unjudged", "checked first and spends no answer" in first.content,
                  first.content)
    harness.check("the second answer, right, is accepted on the one attempt", outcome.is_accepted and outcome.attempts == 1,
                  str(outcome))


def test_a_failed_check_s_draft_is_edited_block_by_block_and_the_rest_kept_defect():
    # Defect: a chapter part rewritten whole for each finding of its check lost what its draft before had right
    # (DEFECTS 584). Fix: `edit_chapter_draft` changes only the numbered blocks it names, and the edit is checked at once
    from models.validation_report import ValidationReport
    first = ValidationReport(subject="chapter-01-text")
    first.add("DOCUMENT_ENTRY_UNCOVERED", "heading-001-1: no block of the section uses F0.2", fix="cite F0.2")
    seen_drafts: list[str] = []
    from services import validation_service
    original = validation_service.validate_or_report_crash
    reports = [first, ValidationReport(subject="chapter-01-text"), ValidationReport(subject="chapter-01-text")]

    def judge(ctx, request):
        seen_drafts.append(document_file_service.build_section_path(ctx.paths, "heading-001-1").read_text(encoding="utf-8"))
        return reports.pop(0)

    draft = ("### Refund limits (T01.1)\n\nA refund is split into at most three parts. [F0.1]\n\n"
             "```\nsplit(refund, 3)\n```\n[F0.1]\n\nAn approver signs each part.\n")
    session = _build_session(fakes.tool_call("check_chapter", markdown=draft),
                             fakes.tool_call("edit_chapter_draft", edits=[
                                 {"action": "replace", "block": 4, "markdown": "An approver signs each part. [F0.2]"}]),
                             limits=DRY_RUNS)
    validation_service.validate_or_report_crash = judge
    try:
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(CHAPTER, max_attempts=1))
    finally:
        validation_service.validate_or_report_crash = original
    results = [message.content for message in session.messages if isinstance(message, ToolMessage)]
    harness.check("the edit tool is offered beside the check", "edit_chapter_draft" in session.model.bound[0],
                  str(session.model.bound[0]))
    harness.check("the failed check lists the draft's blocks by number, the code block with its ids as one",
                  'block 3: "```"' in results[0] and 'block 4: "An approver signs each part."' in results[0], results[0])
    harness.check("the edited draft passes and is the answer", outcome.is_accepted, str(outcome))
    harness.check("only the named block changed: the paragraph and the code kept word for word",
                  "at most three parts. [F0.1]" in seen_drafts[1] and "split(refund, 3)" in seen_drafts[1]
                  and "signs each part. [F0.2]" in seen_drafts[1], seen_drafts[1])


def test_ids_cited_as_a_range_with_a_typographic_dash_are_read_and_their_loss_named_defect():
    # Defect: a block edit dropped "[F81.1–F81.3]", and the check never said an earlier draft covered them, since the
    # cited ids were read without ranges written with a typographic dash (DEFECTS 591). Fix: such a range is read
    from utils import document_text_utils, section_markdown_utils

    harness.check("a range with a typographic dash is read whole",
                  document_text_utils.list_cited_ids("Done [F81.1–F81.3, S0530].") == ["F81.1", "F81.2", "F81.3", "S0530"],
                  str(document_text_utils.list_cited_ids("Done [F81.1–F81.3, S0530].")))
    draft = "Intro. [F0.1]\n\n```text\nhatch test\n```\n[F79.1–F79.4]\n\nIt passes. [F80.1]\n"
    harness.check("its ids line under a fence is the fence's block",
                  len(section_markdown_utils.split_draft_blocks(draft)) == 3, str(section_markdown_utils.split_draft_blocks(draft)))


def test_a_terminal_output_sharing_another_s_opening_lines_is_no_repeat_defect():
    # Two terminal outputs opening with the same prompt refused every edit of chapter 7 writing one of them (DEFECTS 656).
    # Now a kept code block counts as written again only when a written code block holds the same lines
    from utils import section_markdown_utils

    draft = ("### Demo (T07.1)\n\nConnect [F45.1].\n\n```text\n> python sploit.py\nTrying to connect.\n```\n[F45.3]\n\n"
             "Then send [F49.1].\n\n```text\n> python sploit.py\nTrying to connect.\nSending payload\n```\n[F49.4]")
    longer = "```text\n> python sploit.py\nTrying to connect.\nSending payload\n100%\n```\n[F49.4]"
    _, errors = section_markdown_utils.apply_block_edits(draft, [{"action": "replace", "block": 5, "markdown": longer}])
    harness.check("a longer output opening with the kept block's lines is applied", errors == [], str(errors))
    same = "```text\n> python sploit.py\nTrying to connect.\n```\n[F47.3]"
    _, errors = section_markdown_utils.apply_block_edits(draft, [{"action": "add_after", "block": 5, "markdown": same}])
    harness.check("the same code block written again is still refused", any("block 3" in error for error in errors),
                  str(errors))


def test_a_code_block_left_open_is_named_in_the_block_list_defect():
    # A ```assembly block left open ran to the draft's end, listed only by its fence line; the writer edited blocks 14 to
    # 23 of a draft of 12, refused six times (DEFECTS 653). Now the list names it never closed and the heading it holds
    from utils import section_markdown_utils

    draft = "### A (T01.1)\n\nText [F1.1].\n\n```assembly\nNOP\n[F1.2]\n\n### B (T01.2)\n\nMore [F2.1]."
    listed = section_markdown_utils.render_numbered_blocks(draft)
    harness.check("the open block is named, with the heading it swallowed",
                  "never closed" in listed and "### B (T01.2)" in listed, listed)
    closed = "### A (T01.1)\n\nText [F1.1].\n\n```assembly\nNOP\n```\n[F1.2]"
    harness.check("a closed code block gets no such note",
                  "never closed" not in section_markdown_utils.render_numbered_blocks(closed),
                  section_markdown_utils.render_numbered_blocks(closed))


def test_the_numbered_blocks_name_the_ids_each_block_cites_defect():
    # Defect: a code block listed by its fence line alone was replaced with a new ids line, and the ids it had were lost
    # (DEFECTS 597). Each block in the list names the ids it cites
    from utils import section_markdown_utils

    listed = section_markdown_utils.render_numbered_blocks("Intro. [F0.1]\n\n```text\nls\n```\n[F2.4]\n\nNo ids here.\n")
    harness.check("a code block names its ids beside its fence line", 'block 2: "```text" [F2.4]' in listed, listed)
    harness.check("a block citing nothing names none", 'block 3: "No ids here."' in listed
                  and listed.rstrip().endswith('"No ids here."'), listed)


def test_a_finding_names_the_block_its_quote_stands_in_defect():
    # Defect: a finding quoted call tags at the end of a set-aside line, the numbered list showed that block by its first
    # line only, and the worker looked for them in another block (DEFECTS 593). Fix: each quote names its block
    from utils import section_markdown_utils

    draft = "Intro. [F0.1]\n\n> **Set aside:** speaker actions. [F0.2]\n</parameter>\n</tool_call><br>\n"
    detail = "heading-001-1: these blocks name no entry id: '</parameter>\\n</tool_call><br>'"
    named = section_markdown_utils.name_quoted_blocks(detail, draft)
    harness.check("the quote is followed by the block holding it", named.endswith("' (block 2)"), named)
    harness.check("a quote the draft does not hold names nothing",
                  section_markdown_utils.name_quoted_blocks("'nothing like this here'", draft) == "'nothing like this here'", "")


def test_an_edit_naming_no_block_of_the_draft_changes_nothing_and_says_why():
    session = _build_session()
    session.checked_drafts["chapter-01-text"] = "First. [F0.1]\n\nSecond. [F0.2]\n"
    edited, refused = write_conversation_service._apply_draft_edits(
        session, "chapter-01-text", {"edits": [{"action": "remove", "block": 7}]})
    harness.check("no draft comes back, the reason names the blocks there are",
                  edited is None and "no block 7" in refused and "blocks 1 to 2" in refused, refused)
    edited, refused = write_conversation_service._apply_draft_edits(session, "chapter-02-text", {"edits": []})
    harness.check("with no checked draft, the check is asked for first", edited is None and "check_chapter" in refused,
                  refused)


def test_a_cite_edit_adds_ids_to_a_block_and_keeps_its_words_and_ids_defect():
    # Defect: adding an id to a block was a replace of the whole block, and a worker's replace lost three ids the
    # block cited. Fix: a cite edit adds the ids to the block's brackets and keeps the rest of it as it was
    session = _build_session()
    session.checked_drafts["chapter-01-text"] = ("### Setup (T01.1)\n\nThe proxy signs the token. [F1.1, F1.2]\n\n"
                                                 "```bash\nrun --now\n```\n\n```text\nok\n```\n[F2.1]\n")
    edited, refused = write_conversation_service._apply_draft_edits(session, "chapter-01-text", {"edits": [
        {"action": "cite", "block": 2, "ids": ["F1.2", "F1.3"]}, {"action": "cite", "block": 3, "ids": ["F2.2"]},
        {"action": "cite", "block": 4, "ids": ["S0415"]}]})
    harness.check("the cites apply", refused == "", refused)
    harness.check("a paragraph keeps its ids and gains the new one once",
                  "The proxy signs the token. [F1.1, F1.2] [F1.3]" in (edited or ""), str(edited))
    harness.check("a fence with no ids line gets one", "run --now\n```\n[F2.2]" in (edited or ""), str(edited))
    harness.check("a fence's ids line gains the new id", "ok\n```\n[F2.1] [S0415]" in (edited or ""), str(edited))
    edited, refused = write_conversation_service._apply_draft_edits(session, "chapter-01-text", {"edits": [
        {"action": "cite", "block": 1, "ids": ["F1.3"]}, {"action": "cite", "block": 2, "ids": ["frame-12"]}]})
    harness.check("a heading and a word that is no id are refused by name", edited is None and "is a heading" in refused
                  and "not ids: frame-12" in refused, refused)


def test_an_edit_writing_a_kept_block_again_is_refused_naming_it_defect():
    # Defect: a worker merged two blocks by replacing one with both texts and keeping the other, and the next check
    # refused the repeated sentences. Fix: the edit is refused at once, naming the kept block to remove
    session = _build_session()
    session.checked_drafts["chapter-01-text"] = ("The proxy signs every token it forwards. [F1.1]\n\n"
                                                 "The gateway checks the signature on arrival. [F1.2]\n")
    merged = "The proxy signs every token it forwards. The gateway checks the signature on arrival. [F1.1, F1.2]"
    edited, refused = write_conversation_service._apply_draft_edits(session, "chapter-01-text", {"edits": [
        {"action": "replace", "block": 2, "markdown": merged}]})
    harness.check("the merge keeping block 1 is refused, naming it", edited is None and "block 1 says" in refused
                  and "remove block 1" in refused, refused)
    edited, refused = write_conversation_service._apply_draft_edits(session, "chapter-01-text", {"edits": [
        {"action": "replace", "block": 2, "markdown": merged}, {"action": "remove", "block": 1}]})
    harness.check("with block 1 removed in the same edits, it applies", edited == merged, str(edited))
