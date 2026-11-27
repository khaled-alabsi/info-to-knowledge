"""Run one follow-up of phase 4's conversation (REQUIREMENTS.md R12), or of phase 5's review (R16), which is built the same
way with its own tools: the session names its tool set.

A follow-up starts from the starting messages: the instruction, the plan, the notes, the follow-ups answered, this topic's parts
and the follow-up itself, rebuilt by the program from disk. The agent may read more of the meeting, then answers
through the one submit tool this follow-up offers. The answer is written and judged exactly as the calls path writes
and judges it: a call log first, then the record, then the kind's validator, from disk. A rejected answer comes back
as the tool's result with each finding's code, detail and fix, in the same conversation.

The conversation is kept inside the window by compaction ("we use compacting"):

- a warning at 60% and 80% of the size limit, once each, on the next tool result, saying it is not a request to finish;
- at the size limit the model writes a summary of its work in the follow-up, and the conversation becomes the starting messages,
  one handoff message (what the program measured, the summary, its check) and the last turns word for word; a summary that
  cannot be written blocks the follow-up;
- starting messages that are themselves past the size limit block the follow-up as `STARTING_MESSAGES_OVER_SIZE_LIMIT`, rather than compacting forever;
- a prompt the host reports at the size it would cut to is compacted once and counted as an attempt;
- a host that reports an overflow gets one compaction and one retry.

For the local host: a call written as text is read as a call, a frame reaches the model as a user message after the
tool's text.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, ValidationError

import config
from agent_client import finish_reason_utils
from agent_client.agent_errors import ControlStop
from chains import chain_runner
from models import tool_set_vocabulary
from models import call_outcome_vocabulary
from models.attempt_request import AttemptRequest
from models.compaction_request import CompactionRequest
from models.follow_up_outcome import FollowUpOutcome
from models.reading_errors import ReadArgumentError
from models.follow_up_spec import FollowUpSpec
from models.run_context import RunContext
from models.write_conversation_state import WriteConversationState
from models import output_contract
from models import worker_feedback_vocabulary
from prompts import write_messages
from schemas import chapter_draft_edits
from schemas import feedback_actions
from schemas import review_actions
from schemas import write_actions
from services.openai_compatible_client import OpenAICompatibleChatModel
from services import call_log_service
from services import call_record_service
from services import chain_dispatch_service
from services import conversation_log_service
from services import document_file_service
from services import host_return_service
from services import worker_message_service
from services import illustration_action_service
from services import review_file_service
from services import review_reading_service
from services import compaction_service
from services import validation_service
from validators import document_validator
from services import write_reading_service
from services import run_tags_service
from services import worker_feedback_service
from utils import document_text_utils
from utils import turn_line_utils
from utils import document_plan_utils
from utils import starting_messages_utils
from utils import finding_text_utils
from utils import message_content_utils
from utils import frame_message_utils
from utils import json_lines_utils
from utils import section_markdown_utils
from utils import tool_call_text_utils
from utils import reading_tool_utils

__all__ = ["start_afresh", "run_follow_up", "render_rejection_lines"]

# The final follow-up's answer is written as the calls path writes two answers: the title, and the summary with the
# agenda, each with its own call log and validator.
_FINAL_PARTS = (("contents", "contents", ("title",)), ("summary", "summary", ("summary", "agenda")))
# The result a call gets when its follow-up ends before it is carried out: an OpenAI-compatible host refuses a
# conversation that holds a call with no result, and the next follow-up is asked in the same conversation (R12).
_CLOSED_CALL = "Not carried out: this follow-up has ended."
_ACCEPTED = "Accepted."
# The codes of two blocks, which `i2k blocks` and the detail view count by
_STARTING_MESSAGES_OVER_SIZE_LIMIT = "STARTING_MESSAGES_OVER_SIZE_LIMIT"
_PROMPT_SUSPECTED_CUT = "PROMPT_SUSPECTED_CUT"
# What the model reads before the results of calls it wrote as text
_TEXT_CALL_RESULTS_LEAD = "The result of the call you wrote as text:"
# A tool's result is logged cut at this length; the conversation itself keeps it whole
_LOGGED_RESULT_CHARS = 4000
_OVERFLOW_WORDS = ("context length", "context window", "maximum context", "too many tokens", "exceeds the context")
# What a host says when the model's own reply could not be read as a tool call (LM Studio's tool-call parser)
_UNREADABLE_REPLY_WORDS = ("does not match the expected", "failed to parse tool call", "error parsing tool call")
# What the worker is told after such a reply, which reached nobody
_UNREADABLE_REPLY_NOTE = ("Your last reply could not be read: the host found no tool call it could read in it, so nothing "
                          "of it was kept. Call one tool now, its arguments as plain JSON; to answer, call `{tool}`.")
# Every tool that opens, cuts or reads a frame, in phase 4 and phase 5
_FRAME_TOOL_NAMES = frozenset({*write_actions.FRAME_TOOLS, "read_frame", *review_actions.FRAME_READS})
# tool set -> its reading tools, its submit tools, and the service that carries out a read. An unknown set raises.
_READ_TOOLS = {tool_set_vocabulary.WRITE_TOOL_SET: write_actions.READ_TOOLS,
               tool_set_vocabulary.REVIEW_TOOL_SET: review_actions.READ_TOOLS}
_SUBMIT_TOOLS = {tool_set_vocabulary.WRITE_TOOL_SET: write_actions.SUBMIT_TOOLS,
                 tool_set_vocabulary.REVIEW_TOOL_SET: review_actions.SUBMIT_TOOLS}
_READERS = {tool_set_vocabulary.WRITE_TOOL_SET: write_reading_service.run_read,
            tool_set_vocabulary.REVIEW_TOOL_SET: review_reading_service.run_read}
_UNRESOLVED_BEFORE_NOTES = ("Not judged yet: an unresolved answer needs the frames' notes of the todo's time read in this "
                            "todo's own turns, and none was. Call `read_moments` for that time, or `find` one distinctive "
                            "word, then answer again; no attempt was spent.")
_UNRESOLVED_WITH_NOTES = ("Not judged yet: an unresolved answer needs the frames' notes of the todo's time, and this todo "
                          "read only the transcript. The program read the notes of what you read there; they follow. "
                          "Answer again from them, resolved where they settle the todo; no attempt was spent.")
_ANSWER_BEFORE_READS = ("Not judged yet: this answer came in the same turn as reads whose results you had not seen. Read "
                        "their results below, then send the answer again, changed by what they show; no attempt was spent.")
# After this many reads in a row with no note, the next read's result asks for one
_READS_BEFORE_NOTE_REMINDER = 4
_NOTE_REMINDER = ("{reads} reads in a row and no `note`: call `note` now with what these reads showed, each with its "
                  "source, so you never read or work it out again.")
# A chapter's draft judged by every check `submit_chapter` runs, with no answer spent: one answer per chapter, and three
# chapter answers of one run in a row were blocked for an id left out of the brackets, a miss the check names exactly
_CHECK_TOOL = "check_chapter"
_CHECKED_SUBMIT = "submit_chapter"
_CHECK_DESCRIPTION = ("Check a draft of your whole chapter against every check `submit_chapter` runs, without spending "
                      "your answer: the result names each finding. Nothing is kept while a finding is left. A draft that "
                      "passes every check is judged as your answer at once, so you need not submit it again.")
_CHECK_PASSED = "The draft passes every check. Submit the same markdown through `submit_chapter` now."
# A failed check's draft changed block by block, the edited draft checked at once: a part rewritten whole for each
# finding lost what its draft before had right (DEFECTS 584)
_EDIT_TOOL = "edit_chapter_draft"
_EDIT_LEAD = "Change your last checked chapter draft block by block, with no rewrite: "
_BLOCKS_LEAD = ("The blocks of this draft, by the numbers `edit_chapter_draft` takes: fix a finding by changing only the "
                "blocks it names, and every other block stays as it is.\n{blocks}")
_NO_DRAFT_TO_EDIT = "No draft of this follow-up was checked yet: write the whole chapter through `check_chapter` first."
_EDIT_REFUSED = ("No edit was applied, and the last checked draft stands as it was:\n{errors}\n\n" + _BLOCKS_LEAD)
_CHECK_FAILED = ("The draft would be rejected. Fix every one of these, then call `check_chapter` again, and submit only "
                 "once it passes: an answer submitted with a finding left is judged and blocks the chapter.\n{findings}")
# The agenda's first answer is checked the same way, with no tool offered: the one agenda answer of a run blocked on a
# point titled as who presents, a rule the instructions state (the Matrix talk), and the next run's one headings answer
# on a heading that narrated the talk. Their findings come back unjudged; each names the file its judging writes
# The final summary's first answer too: one narrated sentence ("The presentation traces ...") spent an attempt (DEFECTS 397)
_FIRST_CHECKED_SUBMITS = {"submit_agenda": lambda paths, slug: [document_file_service.build_parts_path(paths)],
                          "submit_draft_headings":
                              lambda paths, slug: [document_file_service.build_draft_headings_path(paths, slug)],
                          "submit_final": lambda paths, slug: [document_file_service.build_contents_path(paths),
                                                               document_file_service.build_summary_path(paths)]}
_FIRST_CHECK_FAILED = ("The answer would be rejected. Fix every one of these and submit it again: this answer was "
                       "checked first and spends no answer.\n{findings}")
# A chapter answer is checked first, spending a call and no answer, until a check in its follow-up passes; after that,
# an answer is judged: three chapter answers in a row were blocked on findings `check_chapter` names, and the writer
# never called it
_CHECKED_FIRST = ("Not judged yet: no `check_chapter` of this follow-up has passed, so this answer was checked first and "
                  "spends no answer. {result}")
# Sent under every rejection: the worker is the only one who sees, while it happens, that a check
# misread a right answer or that the instruction behind it can be read two ways, and a report is how that reaches us.
_REPORT_ROUTE = (f"If you believe a finding above is wrong, because your answer does what the instructions ask, report it "
                 f"with `{worker_feedback_vocabulary.FEEDBACK_TOOL}` and kind `wrong_rejection`; if the instruction behind it "
                 f"was not clear enough, use kind `unclear_instruction`. Say which finding and which sentence. Then fix what "
                 f"you can and submit again: a report does not end your attempt.")


def _build_tool_spec(name: str,
                     schema: type[BaseModel],
                     lead: str = "") -> dict[str, Any]:
    """One tool as the model sees it: its name, its schema's docstring on one line after `lead`, its parameters."""

    return {"type": "function", "function": {"name": name, "description": lead + " ".join((schema.__doc__ or "").split()),
                                             "parameters": schema.model_json_schema()}}


def _build_tool_specs(tool_set: str,
                      submit_tool: str,
                      is_check_offered: bool,
                      has_frames: bool) -> list[dict[str, Any]]:
    """The reading tools and the one submit tool this follow-up expects, as the model sees them. A recording with no
    frames (an audio recording) is offered no tool that opens, cuts or reads a frame."""

    submit = _SUBMIT_TOOLS[tool_set][submit_tool].schema
    reads = {name: schema for name, schema in _READ_TOOLS[tool_set].items() if has_frames or name not in _FRAME_TOOL_NAMES}
    tools = {**reads, worker_feedback_vocabulary.FEEDBACK_TOOL: feedback_actions.ReportProblem, submit_tool: submit}

    specs = [_build_tool_spec(name, schema, "Submit your answer to this follow-up: " if name == submit_tool else "")
             for name, schema in tools.items()]
    if is_check_offered:
        specs.append({"type": "function", "function": {"name": _CHECK_TOOL, "description": _CHECK_DESCRIPTION,
                                                       "parameters": submit.model_json_schema()}})
        specs.append(_build_tool_spec(_EDIT_TOOL, chapter_draft_edits.ChapterDraftEdits, _EDIT_LEAD))

    return specs


def _apply_draft_edits(session: WriteConversationState,
                        slug: str,
                        args: dict[str, Any]) -> tuple[str | None, str]:
    """Carry out `edit_chapter_draft` on the follow-up's last checked draft: the edited markdown, to be checked as
    `check_chapter` checks a draft, or None and the reason no edit was applied."""

    draft = session.checked_drafts.get(slug)
    if not draft:
        return None, _NO_DRAFT_TO_EDIT
    edits = [edit for edit in args.get("edits") or [] if isinstance(edit, dict)]
    edited, errors = section_markdown_utils.apply_block_edits(draft, edits)
    if errors or not edits:
        return None, _EDIT_REFUSED.format(errors="\n".join(f"- {error}" for error in errors or ["no edit was given"]),
                                          blocks=section_markdown_utils.render_numbered_blocks(draft))

    return edited, ""


# The running call's thinking, written as it streams, beside the conversation's log
THINKING_LIVE_NAME = "thinking-live.md"


def _append_conversation_log_row(session: WriteConversationState,
                                 record: dict[str, Any]) -> None:
    # Every row carries its time, so how often a step blocked can be told with when (`i2k blocks`, the detail view)
    stamped = {**record, "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    json_lines_utils.append_json_line(session.conversation_log_path, stamped)


def _build_message_id(session: WriteConversationState) -> str:
    """The id the next message of the conversation gets, the counter moved on."""

    message_id = f"m{session.next_message}"
    session.next_message += 1

    return message_id


def _append(session: WriteConversationState,
            message: BaseMessage) -> None:
    """Add a message with the next id, so every prompt can be logged as the ids it held."""

    session.messages.append(message.model_copy(update={"id": _build_message_id(session)}))


def _build_person_messages_path(session: WriteConversationState) -> Path:
    phase = tool_set_vocabulary.PHASE_OF_TOOL_SET[session.tool_set]

    return worker_message_service.build_phase_messages_path(session.ctx.paths.output_root, phase)


def _add_person_messages(session: WriteConversationState) -> int:
    """Add what the person wrote since the last call: a user message last, so the host reads only it; a system message
    into the first message. How many; a reply in words to a question is sent back to work by the loop."""

    waiting = worker_message_service.take_waiting(_build_person_messages_path(session))
    for message in waiting:
        if message.get("role") == "system":
            first = session.messages[0]
            content = worker_message_service.render_system_text(first.content, message)
            session.messages[0] = first.model_copy(update={"content": content})
        else:
            _append(session, HumanMessage(content=worker_message_service.render_user_text(message)))

    return len(waiting)


def _count_prompt_tokens(session: WriteConversationState,
                         tools_tokens: int) -> int:
    """The host's own count of the last prompt plus an estimate of what was appended since; the estimate of the whole
    prompt when the host has not counted it since the last compaction."""

    limits = session.limits
    if session.reported_tokens and session.reported_messages <= len(session.messages):
        return session.reported_tokens + starting_messages_utils.estimate_prompt_tokens(
            session.messages[session.reported_messages:], limits.chars_per_token, limits.frame_tokens, 0)

    return starting_messages_utils.estimate_prompt_tokens(session.messages, limits.chars_per_token,
                                                           limits.frame_tokens, tools_tokens)


def _start_conversation(session: WriteConversationState,
                        spec: FollowUpSpec,
                        findings_text: str) -> None:
    """The conversation the first follow-up starts from: the starting messages with this follow-up and its last findings."""

    starting_messages = dataclasses.replace(session.starting_messages, follow_up_text=spec.text, findings_text=findings_text)
    session.messages = starting_messages_utils.build_starting_messages(starting_messages)
    session.reported_tokens = session.reported_messages = 0


def _save_follow_up_view(session: WriteConversationState,
                         spec: FollowUpSpec) -> None:
    """The conversation's instructions and this follow-up's request, beside the conversation's log, for the detail
    view's "What the worker saw": the log names what was sent by id only."""

    log_dir = session.conversation_log_path.parent
    (log_dir / "requests").mkdir(parents=True, exist_ok=True)
    (log_dir / "system-prompt.md").write_text(session.starting_messages.system_text, encoding="utf-8")
    (log_dir / "requests" / f"{spec.slug}.md").write_text(spec.text, encoding="utf-8")


def _estimate_compacted_tokens(session: WriteConversationState,
                               spec: FollowUpSpec,
                               findings_text: str,
                               tail: list[BaseMessage],
                               handoff: str) -> int:
    """The size the conversation will have once compacted: the starting messages, the handoff and the kept turns."""

    starting = dataclasses.replace(session.starting_messages, follow_up_text=spec.text, findings_text=findings_text)
    messages = [*starting_messages_utils.build_starting_messages(starting), HumanMessage(content=handoff), *tail]

    return starting_messages_utils.estimate_prompt_tokens(messages, session.limits.chars_per_token,
                                                          session.limits.frame_tokens, 0)


def _write_compaction(session: WriteConversationState,
                      spec: FollowUpSpec,
                      findings_text: str,
                      attempts: int,
                      calls: int,
                      max_calls: int,
                      tokens: int,
                      size_limit: int,
                      tools_tokens: int) -> str:
    """Compact the conversation (one prompt for each worker): the model writes a summary of
    its own work with its worker's prompt, and the conversation becomes the starting messages, the follow-up, the last findings and ONE
    handoff message: what the program measured, that summary, and what the program checked in it, then the last turns word for word, up to
    TAIL_TOKENS. Returns "" when it was compacted, otherwise why it could not be."""

    # 1. Every open call answered, the turns kept word for word picked, and the worker whose prompt writes the summary
    _answer_open_calls(session)
    tail_start = compaction_service.find_tail_start(session.messages, session.turns_start, session.limits.chars_per_token,
                                                    session.limits.frame_tokens)
    tail = session.messages[tail_start:]
    try:
        worker = compaction_service.pick_worker(spec.submit_tool)
    except KeyError:
        return f"no compaction prompt exists for `{spec.submit_tool}`"
    request = CompactionRequest(worker=worker, session=session, spec=spec, findings_text=findings_text, attempts=attempts,
                                calls=calls, max_calls=max_calls, tokens=tokens, size_limit_tokens=size_limit, tail_start=tail_start,
                                tools_tokens=tools_tokens)

    # 2. What the program measured, and the room left for the summary
    measured = compaction_service.compute_measured_values(request)
    allowance = compaction_service.compute_allowance(request, "\n".join(measured.values()))
    if allowance < compaction_service.MIN_ALLOWANCE:
        return (f"{_STARTING_MESSAGES_OVER_SIZE_LIMIT}: the starting messages and what the program measured leave {allowance:,} tokens for "
                f"a summary, under {compaction_service.MIN_ALLOWANCE}")

    # 3. The model's summary of its own work, and the program's check of it
    try:
        summary, outcome = compaction_service.write_summary(request, allowance)
    except Exception as exc:  # noqa: BLE001 (a failed host ends this follow-up, never the phase)
        return f"the host failed while compacting: {type(exc).__name__}: {exc}"
    shown_summary = summary or "(none: the summary came back empty twice; only what the program measured above is known)"
    checks = compaction_service.check_summary(request, summary)

    # 4. The handoff, measured as the compacted conversation will be. The handoff is read AFTER the compaction, so its size and what fits are the compacted conversation's: measured
    # before it, a read of 18,795 tokens was called too big for 141,875 of 120,000 tokens in a conversation of 15,511,
    # and the worker answered without reading.
    draft = compaction_service.build_handoff(worker, measured, shown_summary, checks, has_tail=bool(tail))
    after_estimate = _estimate_compacted_tokens(session, spec, findings_text, tail, draft) + tools_tokens
    measured = compaction_service.compute_measured_values(dataclasses.replace(request, tokens=after_estimate))
    handoff = compaction_service.build_handoff(worker, measured, shown_summary, checks, has_tail=bool(tail))

    # 5. The conversation rebuilt: the starting messages, the handoff, the kept turns
    _start_conversation(session, spec, findings_text)
    _append(session, HumanMessage(content=handoff))
    session.turns_start = len(session.messages)
    for message in tail:
        _append(session, message)
    after = starting_messages_utils.estimate_prompt_tokens(session.messages, session.limits.chars_per_token, session.limits.frame_tokens, 0)
    row = {"followUp": spec.slug, "compacted": True, "worker": worker, "summary": summary, "summaryOutcome": outcome,
           "tokensBefore": tokens, "tokensAfter": after, "allowance": allowance, "tailMessages": len(tail)}
    _append_conversation_log_row(session, row)
    session.ctx.log.info(f"  conversation compacted ({worker}): {tokens:,} tokens became about {after:,}; its summary is "
                         f"{len(summary):,} characters, allowance {allowance:,} tokens ({outcome}); {len(tail)} last messages kept word for word")

    return ""


# What a compacted conversation holds beside a whole-chapter read: the starting messages, the handoff and the kept turns.
# A read that would not fit beside it is made without compacting first, since compacting would not make room for it
_COMPACTED_ROOM_TOKENS = 15_000


def _is_read_overflowing(session: WriteConversationState,
                         name: str,
                         args: dict[str, Any],
                         tools_tokens: int,
                         size_limit: int) -> bool:
    """Would this read, made now, push the conversation past its size limit, though it would fit in a conversation that was compacted first
    (plan rule 5: compact before a read, never after it: two of seven chapters had paid for the same read twice)? Only a whole chapter of
    the notes has a size known before it is read."""

    if session.tool_set != tool_set_vocabulary.WRITE_TOOL_SET or name != "read_notes" or args.get("chapter") in (None, ""):
        return False
    # The chapter is read as the reader reads it, so a chapter sent as 2.0 or "2" is measured as the chapter it reads
    try:
        chapter = reading_tool_utils.read_whole_number(args, "chapter", None)
    except ReadArgumentError:
        return False
    cost = write_reading_service.estimate_chapter_read_tokens(session.reading, chapter, session.limits.chars_per_token)
    now = _count_prompt_tokens(session, tools_tokens)

    return now + cost >= size_limit and cost + _COMPACTED_ROOM_TOKENS < size_limit


def _answer_open_calls(session: WriteConversationState) -> None:
    """Give every call of the last reply that has no result one, placed right after that reply's other results."""

    last = max((position for position, message in enumerate(session.messages) if isinstance(message, AIMessage)),
               default=None)
    if last is None:
        return
    end = last + 1
    while end < len(session.messages) and isinstance(session.messages[end], ToolMessage):
        end += 1
    answered = {message.tool_call_id for message in session.messages[last + 1:end]}
    closing = []
    for call in session.messages[last].tool_calls or []:
        if call.get("id") and call["id"] not in answered:
            closing.append(ToolMessage(content=_CLOSED_CALL, tool_call_id=call["id"], id=_build_message_id(session)))
    session.messages[end:end] = closing


# Said before a follow-up asked again after its block: with one attempt per run, this is its only way to learn why
_EARLIER_REJECTION = ("Your answer to this follow-up was rejected in an earlier life of this run, and the follow-up blocked. "
                      "The rejection below is that answer judged again by the checks as they are now. Fix exactly what it names, "
                      "and keep everything else it did not name. Every tool of this follow-up is yours again in this turn, "
                      "as on a first attempt: use them before you answer (a frame the rejection names is looked at and "
                      "cut, or left out, with the frame tools; an image line naming a cut never made is no fix):")


def _compute_request_hash(inputs: dict) -> str:
    """The hash a block is recorded under, and the one a later life compares it with. The cuts already made are left
    out: the blocked answer's own cuts are listed to the next life, and would make every block that cut a frame look
    like a new request (a chapter blocked on an unmasked secret was asked again with no word of why)."""

    sections = [{key: value for key, value in section.items() if key not in ("existingCuts", "placedMatches")}
                for section in inputs.get("sections") or []]

    return document_plan_utils.compute_inputs_hash({**inputs, "sections": sections} if "sections" in inputs else inputs)


_TOOL_BLOCK_LEAD = "Then a frame tool was refused, which stopped the run: "
# The attempt a rejection names, and the pattern that strips it from a rejection carried into a later life
_ATTEMPT_LINE = "This is attempt {attempt} of {max_attempts}."
_ATTEMPT_LINE_PATTERN = re.compile(r"\n?" + re.escape(_ATTEMPT_LINE).replace(r"\{attempt\}", r"\d+").replace(
    r"\{max_attempts\}", r"\d+"))
# A chapter request's own headings, each a line of its own; other chapters' ids stand only in its list of links
_REQUESTED_HEADING_ID = re.compile(r"(?m)^### .*\((T\d+\.\d+)\)\s*$")
# What `_read_blocking_rejection` holds of a follow-up's earlier life before it reads one, and after a reset: the
# rejection, the answer, the last end, the request hash, the answer's arguments and the frame tool's block
_NO_EARLIER_LIFE = ("", "", "", None, None, "")


def _read_blocking_rejection(session: WriteConversationState,
                             spec: FollowUpSpec) -> tuple[str, dict | None]:
    """The rejection that blocked this follow-up in an earlier life of the run, as its tool result read, and the
    arguments of the answer it rejected; ("", None) when the follow-up never blocked, or its last end was an acceptance
    (one note was left out four times in a row, each resume asking afresh)."""

    rejection, answer, last_end, blocked_hash, answer_args, tool_block = _NO_EARLIER_LIFE
    for row in conversation_log_service.read_rows(session.conversation_log_path):
        if row.get("followUp") != spec.slug:
            continue
        submits = [call for call in row.get("calls") or [] if call.get("name") == spec.submit_tool]
        # `i2k reset-chapter` took the follow-up back: what it did before is history, never this life's rejection
        if row.get("reset"):
            rejection, answer, last_end, blocked_hash, answer_args, tool_block = _NO_EARLIER_LIFE
        elif submits:
            args = answer_args = submits[-1].get("args") or {}
            answer = str(args["markdown"]) if isinstance(args.get("markdown"), str) else json.dumps(args, ensure_ascii=False)
        elif row.get("tool") == spec.submit_tool and conversation_log_service.is_rejection(str(row.get("result") or "")):
            # The attempt count belongs to the life that blocked, never to this one
            rejection = _ATTEMPT_LINE_PATTERN.sub("", str(row["result"]).split("\n\n", 1)[0])
        elif "blocked" in row or "accepted" in row:
            last_end = "blocked" if "blocked" in row else "accepted"
            blocked_hash = row.get("inputsHash")
            # A block a refused frame tool made (`budgets.failOnFrameCutFail`) is told too: asked again with only its last
            # answer's rejection, a chapter tried the same mask and blocked again
            reason = str(row.get("blocked") or "")
            # A row logged before the field existed is told by its sentence
            tool_block = str(row.get("toolBlock") or (reason if "was refused" in reason else ""))
    if tool_block:
        rejection = (f"{rejection}\n\n" if rejection else "") + f"{_TOOL_BLOCK_LEAD}{tool_block}"
    if last_end != "blocked" or not rejection:
        return "", None
    # A rejection belongs to the request it judged: asked again with other inputs (a chapter split into parts), the
    # follow-up starts clean. A block logged before its hash was: carried only when its answer names no foreign note.
    if blocked_hash is not None and blocked_hash != _compute_request_hash(spec.inputs):
        return "", None
    allowed = {entry_id for section in spec.inputs.get("sections") or [] for entry_id in section.get("entryIds") or []}
    if blocked_hash is None and allowed and set(document_text_utils.find_entry_ids(answer)) - allowed:
        return "", None

    # The rejected answer goes with it, so what it got right is kept rather than written again from nothing
    return (f"{rejection}\n\nThe answer it rejected:\n\n{answer}" if answer else rejection), (answer_args if answer else None)


def _write_and_judge_blocked_answer(session: WriteConversationState,
                                    spec: FollowUpSpec,
                                    args: dict) -> list[dict[str, str]]:
    """The answer that blocked in an earlier life, written again (its call log, its record and its sections, as a new
    answer is) and judged by the checks as they are now: a check fixed since the block no longer holds it back, and a check added since names what it finds (a resume after a check was
    fixed would have carried the old finding, and the new one would have blocked the one attempt unannounced)."""

    return _submit(session, spec, args, AIMessage(content=""))


def _open_follow_up(session: WriteConversationState,
                    spec: FollowUpSpec) -> None:
    """R12: the first follow-up starts from the starting messages; every later one is asked in the same conversation, with this
    topic's parts written so far when it has any. Only the size limit compacts the conversation."""

    if not session.messages:
        _start_conversation(session, spec, "")
    else:
        _answer_open_calls(session)
        parts = session.starting_messages.topic_parts_text
        text = spec.text + (f"\n\n## This topic's parts written so far\n{parts}" if parts else "")
        _append(session, HumanMessage(content=text))
    session.turns_start = len(session.messages)


def _render_attempt(spec: FollowUpSpec,
                    attempt: int,
                    max_attempts: int,
                    is_after_compaction: bool) -> str:
    where = (f"You are at {spec.position}. The conversation was compacted to what you see; you are not finished, and "
             f"the summary you wrote is below the follow-up. " if is_after_compaction else "")

    return f"{where}{_ATTEMPT_LINE.format(attempt=attempt, max_attempts=max_attempts)}"


def _looks_like_overflow(error: BaseException) -> bool:
    text = str(error).casefold()

    return any(words in text for words in _OVERFLOW_WORDS)


def _is_unreadable_reply(error: BaseException) -> bool:
    """Whether the host failed because the model's reply was no tool call it could read, and not because it is down."""

    text = str(error).casefold()

    return any(words in text for words in _UNREADABLE_REPLY_WORDS)


def _check_arguments(schema,
                     args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    try:
        return schema.model_validate(args).model_dump(), []
    except ValidationError as exc:
        return None, [{"code": "SUBMIT_ARGUMENTS", "detail": "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: "
                                                                        f"{error['msg']}" for error in exc.errors()[:6]),
                       "fix": "give every required field, with the type the tool's parameters name"}]


def _split_answer(job_kind: str,
                  slug: str,
                  parsed: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """The answer as the parts it is written and judged as: the final answer as its title and its summary, any other
    whole."""

    if job_kind == "final":
        return [(kind, part_slug, {key: parsed[key] for key in keys}) for kind, part_slug, keys in _FINAL_PARTS]

    return [(job_kind, slug, parsed)]


def _submit(session: WriteConversationState,
            spec: FollowUpSpec,
            args: dict[str, Any],
            reply: AIMessage) -> list[dict[str, str]]:
    """Write the answer as the calls path writes it, then judge it with the kind's validator from disk. Returns the
    findings; none means accepted."""

    tool = _SUBMIT_TOOLS[session.tool_set][spec.submit_tool]
    parsed, findings = _check_arguments(tool.schema, args)
    if parsed is None:
        return findings
    answers = _split_answer(tool.job_kind, spec.slug, parsed)
    ctx = session.ctx
    for job_kind, slug, answer in answers:
        report = _write_and_judge_keeping_passed(ctx, spec, job_kind, slug, answer, reply)
        findings += [{"code": finding.code, "detail": finding.detail, "fix": finding.fix} for finding in report.findings]
    if tool.job_kind == "final" and not findings:
        _add_worker_tags(ctx, parsed.get("tags") or [])

    return findings


def _add_worker_tags(ctx: RunContext,
                     tags: list[str]) -> None:
    """The final answer's tags added to the run folder's, at most `tags.workerTags`, each a near copy of an inventory
    tag written as the inventory's; never a reason to refuse the answer."""

    inventory = [tag for tag, _ in run_tags_service.list_tag_inventory([config.DOCUMENT_ROOT,
                                                                        config.WORKSPACE_ROOT / "knowledge"])]
    picked = run_tags_service.match_inventory_tags([str(tag) for tag in tags], inventory, config.settings.tags.worker_tags)
    if picked:
        held = run_tags_service.apply_tag_changes(ctx.paths.output_root, picked, [])
        ctx.log.info(f"  the worker's tags: {', '.join(picked)} (the run's tags now: {', '.join(held)})")


def _write_and_judge(ctx: RunContext,
                     spec: FollowUpSpec,
                     job_kind: str,
                     slug: str,
                     answer: dict[str, Any],
                     reply: AIMessage) -> tuple[AttemptRequest, Any]:
    """Write one answer as the calls path writes it, its call log first, then judge it with its kind's validator from
    disk. The request and the report."""

    call_log_service.write_call_log(call_log_service.build_call_log_path(ctx.paths, slug), {"response": {
        "outcome": call_outcome_vocabulary.CALL_COMPLETED, "parsed": answer, "text": message_content_utils.read_message_text(reply.content),
        "thinking": message_content_utils.read_reasoning(reply), "finishReason": "tool_calls"}})
    request = AttemptRequest(job_kind=job_kind, attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore, inputs={**spec.inputs, "slug": slug})
    chain_dispatch_service.write_record(request, answer)

    return request, validation_service.validate_or_report_crash(ctx, request)


def _write_and_judge_keeping_passed(ctx: RunContext,
                                    spec: FollowUpSpec,
                                    job_kind: str,
                                    slug: str,
                                    answer: dict[str, Any],
                                    reply: AIMessage) -> Any:
    """`_write_and_judge`, with each heading that passed on an earlier answer and that this one breaks put back as it
    passed and the answer judged again: a chapter is rewritten whole on every retry, and the model loses what it had
    right (a chapter blocked after three answers, each fixing one heading and dropping notes another had
    covered). An answer and a check of a draft are judged by it alike. The report."""

    passed = _read_passed_sections(ctx, spec, job_kind, slug)
    request, report = _write_and_judge(ctx, spec, job_kind, slug, answer, reply)
    broken = [section_slug for section_slug in passed if _is_named(report.findings, section_slug)]
    if not broken:
        return report
    written = {section_slug: document_file_service.build_section_path(ctx.paths, section_slug).read_text(encoding="utf-8")
               for section_slug in broken}
    for section_slug in broken:
        document_file_service.write_section(ctx.paths, section_slug, passed[section_slug])
    kept = validation_service.validate_or_report_crash(ctx, request)
    # A heading passed beside the old answer's other headings: put back beside the new ones, a note an old heading
    # covered can be covered by none, and the check named that note instead of the draft's own fault (38c3 talk 797
    # chapter 1, DEFECTS 546). A put-back that brings a finding the answer did not have is undone
    found = {(finding.code, finding.detail) for finding in report.findings}
    if any((finding.code, finding.detail) not in found for finding in kept.findings):
        for section_slug, text in written.items():
            document_file_service.write_section(ctx.paths, section_slug, text)
        return report

    return kept


def _read_notes_of_transcript_reads(session: WriteConversationState,
                                    spec: FollowUpSpec) -> tuple[str, list[dict[str, Any]]]:
    """The result that sends an unresolved answer made on the transcript alone back, and the reads the program made
    for it: the frames' notes of each time range the todo read the transcript over, and the notes holding each of its
    searched words. Told only to read them, a worker answered unresolved again on the transcript and was refused."""

    wanted = []
    for call in conversation_log_service.list_follow_up_calls(session.conversation_log_path, spec.slug):
        if call["name"] != review_actions.READ_TRANSCRIPT_TOOL:
            continue
        start, end, words = (str(call["args"].get(key) or "").strip() for key in ("start", "end", "words"))
        read = ({"name": review_actions.READ_MOMENTS_TOOL, "args": {"start": start, "end": end}} if start or end
                else {"name": review_actions.FIND_TOOL, "args": {"words": words}} if words else None)
        if read is not None and read not in wanted:
            wanted.append(read)
    texts = [_READERS[session.tool_set](session.reading, session.reading_state, read["name"], read["args"]).text
             for read in wanted]

    return (f"{_UNRESOLVED_WITH_NOTES}\n\n" + "\n\n".join(texts), wanted) if wanted else (_UNRESOLVED_BEFORE_NOTES, [])


def _count_summary_over(spec: FollowUpSpec,
                        args: dict[str, Any]) -> str | None:
    """The message that sends a final summary over its word limit back, unjudged; None when it is within it or the
    request names no limit."""

    max_words = int(spec.inputs.get("maxWords") or 0)
    words = sum(len(str(claim.get("text") or "").split()) for claim in args.get("summary") or []
                if isinstance(claim, dict))
    if not max_words or words <= max_words:
        return None

    return (f"Not judged yet: the summary has {words} words, more than its {max_words}; no attempt was spent. Keep "
            f"the {max_words} words for the most important points, in short sentences, and submit again: the "
            f"closing lists the rest.")


def _find_unsourced_summary(args: dict[str, Any]) -> str | None:
    """The message that sends a final summary back, unjudged, when a sentence names no entry id; None when every one
    does. The second Wikipedia run's summary wrote 9 such sentences and its one attempt blocked the run."""

    unsourced = [str(claim.get("text") or "") for claim in args.get("summary") or []
                 if isinstance(claim, dict) and not claim.get("entry_ids")]
    if not unsourced:
        return None
    listed = "; ".join(repr(" ".join(text.split()[:8]) + (" …" if len(text.split()) > 8 else "")) for text in unsourced)

    return (f"Not judged yet: {len(unsourced)} summary sentence(s) name no entry id ({listed}); no attempt was spent. "
            f"Give each the ids of the entries it rests on, and submit again.")


def _check_draft(session: WriteConversationState,
                 spec: FollowUpSpec,
                 args: dict[str, Any],
                 reply: AIMessage) -> str:
    """Carry out `check_chapter`: the draft is written and judged exactly as `submit_chapter` judges it, then every file
    that judging wrote is put back as it was, so a draft never stands in for an answer. Spends a call, never an attempt."""

    tool = _SUBMIT_TOOLS[session.tool_set][spec.submit_tool]
    failed = _CHECK_FAILED if spec.submit_tool == _CHECKED_SUBMIT else _FIRST_CHECK_FAILED
    parsed, findings = _check_arguments(tool.schema, args)
    if parsed is None:
        return failed.format(findings=conversation_log_service.render_findings(findings)) + f"\n\n{_REPORT_ROUTE}"
    ctx = session.ctx
    if spec.submit_tool == _CHECKED_SUBMIT:
        touched = [document_file_service.build_section_path(ctx.paths, section["slug"])
                   for section in spec.inputs.get("sections") or []]
        touched.append(document_file_service.build_chapter_submission_path(ctx.paths, spec.slug))
    else:
        touched = _FIRST_CHECKED_SUBMITS[spec.submit_tool](ctx.paths, spec.slug)
    answers = _split_answer(tool.job_kind, spec.slug, parsed)
    touched += [call_log_service.build_call_log_path(ctx.paths, slug) for _, slug, _ in answers]
    saved = {path: path.read_bytes() if path.is_file() else None for path in touched}
    try:
        reports = [_write_and_judge_keeping_passed(ctx, spec, job_kind, slug, answer, reply)
                   for job_kind, slug, answer in answers]
        report = reports[0]
        for other in reports[1:]:
            report.findings += other.findings
    finally:
        for path, content in saved.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
    # A chapter draft's finding names the block each quote of it stands in, the number `edit_chapter_draft` takes
    # (DEFECTS 593)
    draft = str(parsed.get("markdown") or "") if spec.submit_tool == _CHECKED_SUBMIT else ""
    findings = [{"code": finding.code, "fix": finding.fix,
                 "detail": section_markdown_utils.name_quoted_blocks(finding.detail, draft) if draft else finding.detail}
                for finding in report.findings]
    check_name = _CHECK_TOOL if spec.submit_tool == _CHECKED_SUBMIT else f"check of {spec.submit_tool}"
    session.ctx.log.info(f"  {check_name}: {len(findings)} finding(s)" if findings else f"  {check_name}: passes")
    lost = (_list_lost_ids(session, spec.slug, str(parsed.get("markdown") or ""), report.findings)
            if spec.submit_tool == _CHECKED_SUBMIT else [])
    if lost:
        findings.append({"code": "DOCUMENT_ENTRY_UNCOVERED",
                         "detail": f"an earlier draft of this chapter covered {', '.join(lost)}, and this one does not",
                         "fix": "put back the blocks of your earlier draft that cited them, with their ids in the "
                                "brackets, when you add what the other findings ask: a fix keeps what the last draft "
                                "had right"})

    # A failed check invites a report as a rejection does: a writer caught between a request and a check reworded a
    # slide's own text three times and never said the check was wrong
    if not findings:
        return _CHECK_PASSED
    result = failed.format(findings=conversation_log_service.render_findings(findings)) + f"\n\n{_REPORT_ROUTE}"
    if spec.submit_tool != _CHECKED_SUBMIT:
        return result
    # The draft is kept for `edit_chapter_draft`, and its blocks listed by number (DEFECTS 584)
    session.checked_drafts[spec.slug] = draft

    return f"{result}\n\n{_BLOCKS_LEAD.format(blocks=section_markdown_utils.render_numbered_blocks(draft))}"


def _list_lost_ids(session: WriteConversationState,
                   slug: str,
                   markdown: str,
                   findings: list) -> list[str]:
    """The uncovered note ids an earlier check of this follow-up's drafts cited: a rewrite made to fix one finding
    dropped notes the draft before it covered (lecture-9, chapter 7: 20 notes added, 17 others lost). Remembers this
    draft's ids for the next check."""

    earlier = session.checked_ids.get(slug, set())
    session.checked_ids[slug] = earlier | set(document_text_utils.list_cited_ids(markdown))
    uncovered = {entry_id for finding in findings if finding.code == "DOCUMENT_ENTRY_UNCOVERED"
                 for entry_id in re.findall(document_text_utils.ENTRY_ID_PATTERN, finding.detail)}

    return sorted(uncovered & earlier, key=lambda entry_id: [int(part) for part in re.findall(r"\d+", entry_id)])


def _is_named(findings: list,
              section_slug: str) -> bool:
    return any(f"{section_slug}:" in finding.detail for finding in findings)


def _read_passed_sections(ctx: RunContext,
                          spec: FollowUpSpec,
                          job_kind: str,
                          slug: str) -> dict[str, str]:
    """The sections of a chapter already on disk that pass their checks as they stand, `{slug: markdown}`: what an
    earlier answer got right. Empty for any other kind and before the chapter's first answer."""

    if job_kind != "chapter" or not call_log_service.build_call_log_path(ctx.paths, slug).is_file():
        return {}
    on_disk = {section["slug"]: document_file_service.build_section_path(ctx.paths, section["slug"])
               for section in spec.inputs.get("sections") or []}
    on_disk = {section_slug: path for section_slug, path in on_disk.items() if path.is_file()}
    if not on_disk:
        return {}
    request = AttemptRequest(job_kind=job_kind, attempt=1, repository=ctx.repository, module=ctx.module,
                             paths=ctx.paths, ignore=ctx.ignore, inputs={**spec.inputs, "slug": slug})
    report = validation_service.validate_or_report_crash(ctx, request)
    # A finding that names no section failed the chapter as a whole, and its sections were never judged one by one: none
    # of them passed (after an answer failed on its section count, a stale section from an older answer was
    # put back over the new one)
    if any(not any(_is_named([finding], section_slug) for section_slug in on_disk) for finding in report.findings):
        return {}

    return {section_slug: path.read_text(encoding="utf-8") for section_slug, path in on_disk.items()
            if not _is_named(report.findings, section_slug)}


def _record_report(session: WriteConversationState,
                   spec: FollowUpSpec,
                   args: dict[str, Any],
                   attempt: int) -> str:
    """Carry out `report_problem`: the report goes into the run folder's feedback file, filed under the attempt it is
    about, and onto the console in full."""

    kind, summary, detail = (str(args.get(key) or "") for key in ("kind", "summary", "detail"))
    where = f"{tool_set_vocabulary.PHASE_OF_TOOL_SET[session.tool_set]} · {spec.slug} · attempt {attempt}"
    outcome = worker_feedback_service.record_report(session.ctx.paths.output_root / output_contract.WORKER_FEEDBACK_FILE,
                                                    kind=kind, summary=summary, detail=detail, where=where)
    for line in outcome.console_lines:
        session.ctx.log.info(line)

    return outcome.reply


def _send_tool_result(session: WriteConversationState,
                      call: dict[str, Any],
                      text: str,
                      written_results: list[str]) -> None:
    """A tool's result: a tool message answering the call, or, for a call written as text, a line of a user message."""

    if call.get("id"):
        _append(session, ToolMessage(content=text, tool_call_id=call["id"]))
    else:
        written_results.append(f"{call['name']}: {text}")


# Asked after every rejected answer, when `budgets.askWhyAfterRejection` is on ("it should be
# clear that the agent is not asked to correct right now"): the why, alone, then the correction in a message of its own
_WHY_AFTER_REJECTION = (f"Do not correct your answer yet. First say why it was not accepted, so the instructions and the "
                        f"checks can be improved: call `{worker_feedback_vocabulary.FEEDBACK_TOOL}` once. If an "
                        f"instruction, the template or a check is at fault, quote the sentence and use kind "
                        f"`wrong_rejection`, `unclear_rejection`, `unclear_instruction`, `wrong_instruction`, "
                        f"`missing_instruction` or `contradiction`. If the instructions were clear and your answer did not "
                        f"follow them, use kind `other`: name the rule you did not follow and what led you away from it.")
# Past this, a reply in words to the question is an answer written out, not a report
_MAX_WORDS_REPORT_CHARS = 1500
# What a reply stopped as a loop keeps of its text in the conversation: enough to see where it began repeating
_LOOPED_KEPT_CHARS = 2_000
_LOOP_CHECKED = ("Your reply wrote the chapter as its text, then repeated its last lines until it was stopped. The "
                 "chapter up to the repeats was checked, not judged as your answer:\n{result}\nWrite the next draft as "
                 "the `markdown` argument of a `check_chapter` call, never as the text of your reply: a check that passes is "
                 "your answer.")
_LOOPED_REPLY_NOTE = "(A chapter written as the reply's text; it repeated its last lines and was stopped.)"
# The output limit of the turn that asks why: a report is a few hundred tokens, and one such turn looped in its
# reasoning for 134,000 characters and twelve minutes, then called no tool. Cut short, it is passed over.
_REPORT_TURN_MAX_TOKENS = 6000
_NEXT_ATTEMPT = "You will be asked for the correction in the next message; no answer is judged now."
_NO_ATTEMPT_LEFT = "No attempt is left: this step stops after your report, and no answer is judged now."
_CORRECT_NOW = "Now correct your answer: fix what the rejection above names and submit again through `{tool}`. {attempt}"


def _collect_report_after_rejection(session: WriteConversationState,
                                    spec: FollowUpSpec,
                                    findings: list[dict[str, str]],
                                    call: dict[str, Any],
                                    written_results: list[str],
                                    attempts: int,
                                    is_last: bool) -> None:
    """One turn after a rejected answer, offering only `report_problem`: the worker says whether the instruction, the
    template, a check or its own answer was at fault, and corrects nothing. A reply in words is recorded as its
    report; no reply, or a failed call, is passed over, since nothing may wait on a report."""

    # 1. The rejection is the submit's result, every other open call is closed, and the question follows
    _send_tool_result(session, call, conversation_log_service.render_rejection(findings), written_results)
    _answer_open_calls(session)
    question = f"{_WHY_AFTER_REJECTION} {_NO_ATTEMPT_LEFT if is_last else _NEXT_ATTEMPT}"
    _append(session, HumanMessage(content="\n".join([_TEXT_CALL_RESULTS_LEAD, *written_results, "",
                                                     question] if written_results else [question])))
    written_results.clear()

    # 2. One call with the feedback tool alone
    specs = [_build_tool_spec(worker_feedback_vocabulary.FEEDBACK_TOOL, feedback_actions.ReportProblem)]
    try:
        reply = chain_runner.invoke_reply(model=session.model.bind_tools(specs, max_tokens=_REPORT_TURN_MAX_TOKENS),
                                          messages=session.messages)
    except Exception as exc:  # noqa: BLE001 (a failed question loses only the report; the follow-up goes on)
        session.ctx.log.warning(f"  asked why after the rejection: the host failed: {type(exc).__name__}: {exc}")
        return
    said = message_content_utils.read_message_text(reply.content)
    calls = list(reply.tool_calls or []) or [c for c in [tool_call_text_utils.read_text_call(said)] if c]
    reports = [report for report in calls if report["name"] == worker_feedback_vocabulary.FEEDBACK_TOOL]
    _append_conversation_log_row(session, {"followUp": spec.slug, "askedWhy": True,
                                 "thinking": message_content_utils.read_reasoning(reply), "said": said,
                                 "calls": [{"name": report["name"], "args": report.get("args")} for report in calls]})
    _append(session, reply)

    # 3. Each report is recorded and answered; words with no report are recorded as one; any other call is closed
    for report in reports:
        result = _record_report(session, spec, report.get("args") or {}, attempts)
        _append_conversation_log_row(session, {"followUp": spec.slug, "tool": report["name"], "result": result})
        if report.get("id"):
            _append(session, ToolMessage(content=result, tool_call_id=report["id"]))
    _answer_open_calls(session)
    if not reports and said.strip():
        # An answer written out instead of a report is noted in one line, never copied whole (asked why, a
        # worker wrote its whole corrected chapter, and the feedback file held the chapter)
        is_answer = said.lstrip().startswith("#") or len(said) > _MAX_WORDS_REPORT_CHARS
        _record_report(session, spec, {
            "kind": "other",
            "summary": "replied with a new attempt instead of a report" if is_answer else " ".join(said.split())[:120],
            "detail": (f"Asked why its attempt was rejected, the worker wrote a new attempt of {len(said):,} characters "
                       f"instead of a report." if is_answer else said.strip())}, attempts)


def describe_asked_headings(spec: FollowUpSpec) -> str:
    """One line naming what a chapter follow-up asks: its place and its headings' own lines, which its answer writes
    and no other."""

    headings = [f"### {section.get('heading', '')} ({section.get('topicId', '')})" for section in spec.inputs.get("sections") or []]

    return (f"This follow-up asks for {spec.position} and only these headings: {'; '.join(headings)}. Answer through "
            f"`{spec.submit_tool}`.")


def build_existing_cuts_message(output_root,
                                store: dict,
                                sections: list[dict]) -> HumanMessage | None:
    """The cuts a chapter request lists as already made, as images, sent right after the request: after a resume a
    writer placed two cuts made in an earlier life without looking at them, and described them from memory, wrong in
    theme, line numbers and code. None when the request lists no cut whose file stands."""

    cuts = store.get("cuts") or {}
    ids = list(dict.fromkeys(match.group(0) for section in sections for text in section.get("existingCuts") or []
                             if (match := re.match(document_text_utils.CUT_ID_PATTERN, text))))
    shown = [cut_id for cut_id in ids if cut_id in cuts and (output_root / cuts[cut_id]["file"]).is_file()]
    if not shown:
        return None

    # Each image follows its own label: listed once and then sent as seven images, a writer counted them wrong and
    # described a cut of a schematic's connector rows as the relay drawing of the next cut (DEFECTS 231)
    labelled = [part for cut_id in shown
                for part in ({"type": "text", "text": f"{cut_id}, from {cuts[cut_id].get('frame', 'its frame')}:"},
                             *encode_tool_frames([output_root / cuts[cut_id]["file"]]))]

    return HumanMessage(content=[
        # Worded as part of the request above: "Describe a cut you place from what you see here" alone was taken for a
        # new task, and the writer answered with a description instead of the chapter
        {"type": "text", "text": "For the request above, the cuts it lists as already made, as they are now, each "
                                 "after its own id: " + ", ".join(shown) + ". Nothing new is asked here: answer the "
                                 "request above, and write each placed cut's description from what you see in its image."},
        *labelled])


def build_figures_message(figures: list) -> HumanMessage:
    """The figures a request names, as images, sent right after it: the review's critique read the document's text
    only, and a placed figure cut through its slide's left side passed it unseen."""

    labelled = [part for path in figures for part in ({"type": "text", "text": f"{path.name}:"}, *encode_tool_frames([path]))]

    return HumanMessage(content=[
        {"type": "text", "text": "For the request above, every figure the document places, in its order, each after its "
                                 "own name: " + ", ".join(path.name for path in figures) + ". Nothing new is asked here: "
                                 "answer the request above, and judge each figure from what you see in its image."},
        *labelled])


def encode_tool_frames(frames: list) -> list[dict[str, Any]]:
    """The frames and cuts the frame tools returned, as image parts, each narrower than phase 3's
    `framesProcessing.minSentFrameWidth` enlarged to it: sent at their own size, a 310-pixel cut of a 360p recording
    read to the writer as "tiny, blurry, nearly illegible", and it undid a cut that was readable."""

    return [frame_message_utils.encode_frame(path, config.settings.frames_processing.min_sent_frame_width) for path in frames]


def _record_blocked(session: WriteConversationState,
                    spec: FollowUpSpec,
                    attempts: int,
                    calls: int,
                    reason: str,
                    tool_block: str = "") -> FollowUpOutcome:
    """The block in the conversation log; `tool_block` is the refusal of a frame tool that stopped the run, kept in its
    own field so a resumed life is told it without searching the reason's prose."""

    row = {"followUp": spec.slug, "blocked": reason, "inputsHash": _compute_request_hash(spec.inputs)}
    if tool_block:
        row["toolBlock"] = tool_block
    _append_conversation_log_row(session, row)

    return FollowUpOutcome(is_accepted=False, attempts=attempts, calls=calls, reason=reason)


def _estimate_tools_tokens(specs: list[dict],
                           chars_per_token: float) -> int:
    """The tokens the tool definitions add to every prompt, estimated from their JSON."""

    return starting_messages_utils.estimate_text_tokens(json.dumps(specs), chars_per_token)


def render_rejection_lines(findings: list[dict],
                           attempts: int,
                           max_attempts: int) -> list[str]:
    """What the console prints under a submit line the program rejected ("i cant see rejections in
    logs and the reason"): the attempt, then one line per finding with its code and its reason."""

    return [f"  rejected, attempt {attempts} of {max_attempts}:",
            *[f"    {finding_text_utils.read_field(finding, 'code')}: "
              f"{' '.join(finding_text_utils.read_field(finding, 'detail').split())[:400]}" for finding in findings]]


def run_follow_up(session: WriteConversationState,
                  spec: FollowUpSpec) -> FollowUpOutcome:
    """Ask one follow-up and carry out the agent's calls until an answer is accepted, the attempts or the calls run
    out, or the host fails. It continues the conversation the earlier follow-ups left (R12)."""

    limits = session.limits
    # A follow-up starts with no remembered look: the result of the last one may be compacted out of its conversation
    illustration_action_service.clear_last_call(session.reading_state.image_store)
    # `budgets.chapterDryRuns`: a chapter's answer is checked first, and `check_chapter` is offered
    is_check_offered = spec.submit_tool == _CHECKED_SUBMIT and limits.can_dry_run_chapters
    specs = _build_tool_specs(session.tool_set, spec.submit_tool, is_check_offered, session.has_frames)
    tools_tokens = _estimate_tools_tokens(specs, limits.chars_per_token)
    # An OpenAI-compatible host is told a call is required: the final summary came back in words, as markdown or bare
    # JSON, on four real PDFs in a row, each a turn lost and one a rejection; LM Studio keeps the model's thinking
    # under "required" and returns the call (measured on qwen3.6-35b-a3b-mtp)
    bound = (session.model.bind_tools(specs, tool_choice="required")
             if isinstance(session.model, OpenAICompatibleChatModel) else session.model.bind_tools(specs))
    size_limit = starting_messages_utils.resolve_size_limit(limits.size_limit_tokens, limits.window_tokens,
                                                       limits.max_output_tokens)
    max_calls = spec.max_calls or limits.max_calls
    max_attempts = spec.max_attempts or limits.max_attempts
    attempts = calls = overflows = reads_unnoted = 0
    is_checked = is_notes_reminded = is_length_reminded = is_sources_reminded = False
    findings_text, pending_warning, warned_shares = "", "", set()
    earlier, earlier_args = _read_blocking_rejection(session, spec)
    if earlier_args is not None:
        findings = _write_and_judge_blocked_answer(session, spec, earlier_args)
        if not findings:
            session.ctx.log.info("  the attempt that blocked in an earlier life passes the checks as they are now: accepted")
            _append_conversation_log_row(session, {"followUp": spec.slug, "accepted": 0, "rejudged": True})
            return FollowUpOutcome(is_accepted=True, attempts=0, calls=0, reason="")
        judged, answer = earlier.split("\n\nThe answer it rejected:\n\n", 1)
        # The frame tool's refusal that blocked the step is no finding of the answer: it is kept beside the new findings
        tool_part = judged[judged.find(_TOOL_BLOCK_LEAD):] if _TOOL_BLOCK_LEAD in judged else ""
        earlier = (conversation_log_service.render_rejection(findings) + (f"\n\n{tool_part}" if tool_part else "")
                   + f"\n\nThe answer it rejected:\n\n{answer}")
    _open_follow_up(session, spec)
    _save_follow_up_view(session, spec)
    shown_cuts = build_existing_cuts_message(session.ctx.paths.output_root, session.reading_state.image_store,
                                             list(spec.inputs.get("sections") or []))
    if shown_cuts is not None:
        _append(session, shown_cuts)
    if spec.figures:
        _append(session, build_figures_message(list(spec.figures)))
    if earlier:
        _append(session, HumanMessage(content=f"{_EARLIER_REJECTION}\n\n{earlier}"))

    while calls < max_calls:
        # STEP 0: a stop the operator asked for ends the run here, between two turns, where nothing is half written: the
        # conversation loop read no signal at all, so the page's and the CLI's stop did nothing for the whole of phases
        # 4 and 5, the longest of the run
        if session.ctx.control_check is not None and session.ctx.control_check() == "stop":
            raise ControlStop(f"stopped before call {calls + 1} of {spec.slug}")
        # What the person wrote to this worker from the page reaches it here, at no cost to the follow-up's calls
        max_calls += _add_person_messages(session)
        # STEP 1: count the prompt; past the size limit the conversation is compacted, and starting messages past it block
        count = _count_prompt_tokens(session, tools_tokens)
        if count >= size_limit:
            reason = _write_compaction(session, spec, findings_text, attempts, calls, max_calls, count, size_limit, tools_tokens)
            if reason:
                return _record_blocked(session, spec, attempts, calls, reason)
            warned_shares = set()
            count = _count_prompt_tokens(session, tools_tokens)
            if count >= size_limit:
                return _record_blocked(session, spec, attempts, calls, f"{_STARTING_MESSAGES_OVER_SIZE_LIMIT}: the starting messages alone are about {count:,} tokens, over the size limit of {size_limit:,}")
        share = starting_messages_utils.decide_warning_share(count, size_limit, warned_shares) if spec.is_size_warned else None
        if share is not None:
            warned_shares.add(share)
            pending_warning = write_messages.render_warning(count, size_limit)

        # STEP 2: one call to the host; an overflow gets one compaction and one retry. Every call from here (this one,
        # a compaction's, the turn after a rejection) is a row of this follow-up in the run's call record, so the
        # phase's summary counts them
        sent = [message.id for message in session.messages]
        call_record_service.set_place(run_dir=session.ctx.run_dir,
                                      phase=tool_set_vocabulary.PHASE_OF_TOOL_SET[session.tool_set],
                                      slug=spec.slug,
                                      attempt=attempts + 1,
                                      window_tokens=limits.window_tokens)
        try:
            # Streamed, so a thinking that loops is ended where phase 3 ends one: a review todo thought 102,473
            # characters for 16 minutes, the same passage over and over from 48,600 on, and called no tool. Its
            # thinking so far goes to `thinking-live.md` beside the log: a chapter's call ran 33 minutes with nothing
            # on disk to tell a loop from a long draft (2026-10-23)
            live_path = session.conversation_log_path.with_name(THINKING_LIVE_NAME)
            live_path.parent.mkdir(parents=True, exist_ok=True)
            reply = chain_runner.read_streamed_reply(bound, session.messages, live_path)
        except Exception as exc:  # noqa: BLE001 (a failed host ends this follow-up, never the phase)
            if _looks_like_overflow(exc) and not overflows:
                overflows += 1
                reason = _write_compaction(session, spec, findings_text, attempts, calls, max_calls, session.reported_tokens, size_limit, tools_tokens)
                if reason:
                    return _record_blocked(session, spec, attempts, calls, reason)
                continue
            # A load LM Studio broke, or a host restarted under the call, blocked the follow-up in seconds, and the queue
            # ran its next job into the same host; the call waits for a working model and is asked again
            phase = tool_set_vocabulary.PHASE_OF_TOOL_SET[session.tool_set]
            # A reply the host could not read as a tool call is the model's turn gone wrong, not a host down: it spends a
            # call and the worker is asked again (a chapter part blocked at once on LM Studio's "does not match the
            # expected peg-native format", DEFECTS 722)
            if _is_unreadable_reply(exc):
                calls += 1
                session.ctx.log.warning(f"  {spec.slug}: the host could not read the reply as a tool call; asked again")
                _append(session, HumanMessage(content=_UNREADABLE_REPLY_NOTE.format(tool=spec.submit_tool)))
                continue
            if isinstance(session.model, OpenAICompatibleChatModel) and host_return_service.wait_for_lost_model(phase):
                session.ctx.log.warning(f"  {spec.slug}: the model host works again; the call is asked again")
                continue
            return _record_blocked(session, spec, attempts, calls, f"the host failed: {type(exc).__name__}: {exc}")
        usage = getattr(reply, "usage_metadata", None) or {}
        reported = int(usage.get("input_tokens") or 0)
        # A stream the loop test stopped brings no usage: read as 0, the conversation's size was taken to be 0 and the
        # row logged "prompt 0 tokens" (DEFECTS 380); the last count the host gave stays, and the estimate adds the rest
        is_counted = reported > 0
        if is_counted:
            session.reported_tokens, session.reported_messages = reported, len(session.messages)
        said = message_content_utils.read_message_text(reply.content)
        # A reply stopped as a loop is no answer: its text, read as the chapter, was the chapter about five times over
        # (the wiki's chapter 6), and a call cut off in it would be judged. It is kept short, with no call, and the
        # next turn is told to answer
        is_looped = (getattr(reply, "response_metadata", None) or {}).get("finish_reason") == finish_reason_utils.THINKING_LOOPED
        # The log keeps what the model said whole: cut to its first characters, a chapter stopped as a loop could not
        # be checked for the loop by anyone (DEFECTS 220)
        said_whole = said
        worker_message_service.record_reply(_build_person_messages_path(session), said)
        if is_looped:
            # Kept as one line, never its text: a chapter's looped text, kept at its first characters, was written again
            # as text, and looped again, six replies in a row, where 778 chapter answers sent through the tool across
            # 15 runs never looped (DEFECTS 234)
            said = _LOOPED_REPLY_NOTE if section_markdown_utils.is_chapter_markdown(said) else said[:_LOOPED_KEPT_CHARS]
            reply = AIMessage(content=said, additional_kwargs=dict(reply.additional_kwargs or {}),
                              response_metadata=dict(reply.response_metadata or {}), usage_metadata=reply.usage_metadata)
        tool_calls = [] if is_looped else (list(reply.tool_calls or [])
                                           or [call for call in [tool_call_text_utils.read_text_call(said)] if call])
        # Calls written as Python on the reply's first lines run first, in order, and the text after them is read as
        # the rest of the reply: a chapter's worker wrote three leave_out calls above its chapter, the chapter was
        # checked and the calls lost (froscon 3211 chapter 1, DEFECTS 290)
        rest = said
        if not is_looped and not tool_calls:
            tool_calls, rest = tool_call_text_utils.split_leading_calls(
                said, {str(each["function"]["name"]) for each in specs})
        # A chapter written out in the reply with no call is the answer on the wrong channel: it is judged as the answer,
        # as a call written as text is (five chapter answers of one run came as text, each a whole turn lost)
        if (not is_looped and not any(call["name"] in (_CHECKED_SUBMIT, _CHECK_TOOL, _EDIT_TOOL) for call in tool_calls)
                and spec.submit_tool == _CHECKED_SUBMIT and section_markdown_utils.is_chapter_markdown(rest)):
            tool_calls = [*tool_calls, {"name": _CHECKED_SUBMIT, "args": {"markdown": rest}, "id": ""}]
        # A chapter written out whole and then repeating its last lines until the stream stopped it lost the chapter:
        # two answers of one run, about 800 s each (DEFECTS 230). Its text up to the repeats goes to the dry check, never
        # judged as the answer, so a cut-off or repeated chapter spends nothing; the worker is told why
        is_loop_checked = False
        if is_looped and is_check_offered and not is_checked:
            # and is read by the chapter's contract, each listed heading once, so what ran on past it is never a block
            # (DEFECTS 527, 531: the circle of cutting by each shape it ran on in)
            listed_ids = set(_REQUESTED_HEADING_ID.findall(spec.text))
            once = section_markdown_utils.read_chapter_draft(chain_runner.drop_repeated_tail(said_whole), listed_ids or None)
            if once != said_whole and section_markdown_utils.is_chapter_markdown(once):
                tool_calls = [{"name": _CHECK_TOOL, "args": {"markdown": once}, "id": ""}]
                is_loop_checked = True
        # So is an answer written as the submit tool's bare arguments, one JSON object in the reply
        bare = None if tool_calls or is_looped else tool_call_text_utils.read_bare_arguments(said, spec.submit_tool)
        if bare is not None:
            tool_calls = [{"name": spec.submit_tool, "args": bare, "id": ""}]
        if reported:
            session.ctx.log.info(turn_line_utils.render_turn_line(
                [{"name": call["name"], "args": call.get("args") or {}} for call in tool_calls], reported, size_limit))
        _append_conversation_log_row(session, {"followUp": spec.slug, "sent": sent, "promptTokens": reported if is_counted else None,
                                     "thinking": message_content_utils.read_reasoning(reply), "said": said_whole,
                                     "calls": [{"name": call["name"], "args": call.get("args")} for call in tool_calls]})

        # STEP 3: a prompt the host reports at the size it would cut to is rebuilt once, as an attempt
        if reported and starting_messages_utils.is_prompt_truncation_suspected(reported, limits.window_tokens,
                                                                  limits.max_output_tokens):
            attempts += 1
            if attempts >= max_attempts:
                return _record_blocked(session, spec, attempts, calls, f"{_PROMPT_SUSPECTED_CUT}: the host counted {reported:,} prompt tokens")
            findings_text = (f"- {_PROMPT_SUSPECTED_CUT}: the last prompt may have lost its start; it was rebuilt.\n"
                             + _render_attempt(spec, attempts + 1, max_attempts, is_after_compaction=True))
            reason = _write_compaction(session, spec, findings_text, attempts, calls, max_calls, reported, size_limit, tools_tokens)
            if reason:
                return _record_blocked(session, spec, attempts, calls, reason)
            continue
        _append(session, reply)

        # STEP 4: a reply with no call judged nothing: it spends a call, never an attempt, and is told the tool. The call
        # budget ends a model that keeps replying in words (a chapter written out as text cost one of three attempts)
        if not tool_calls:
            calls += 1
            findings_text = ((f"- THINKING_LOOPED: your reply repeated the same passage and was stopped, so nothing in it "
                              f"was taken as your answer. Fix: what you have read is enough to decide; answer once through "
                              f"`{spec.submit_tool}` now.\n") if is_looped else
                             f"- NO_TOOL_CALL: your reply called no tool. Fix: answer through `{spec.submit_tool}`.\n") \
                + _render_attempt(spec, attempts + 1, max_attempts, is_after_compaction=False)
            _append(session, HumanMessage(content=findings_text))
            continue

        # STEP 5: carry out each call; the submit is judged, a report recorded, a read answered, anything else refused
        frames, written_results = [], []
        is_compacted_here = is_asked_why = False
        # A look made by one of these calls is seen only with their results, so a cut beside it waits (DEFECTS 363)
        if session.has_frames:
            if not session.reading_state.image_store:
                session.reading_state.image_store.update(illustration_action_service.build_empty_store([]))
            illustration_action_service.mark_looks_seen(session.reading_state.image_store)
        for position, call in enumerate(tool_calls):
            calls += 1
            name, args = call["name"], call.get("args") or {}
            if is_compacted_here:
                call = {**call, "id": None}
            # A draft that passes its check is the answer, judged at once: told to submit the same markdown, a writer
            # wrote notes that its chapter was accepted until it spent its 40 calls and blocked (DEFECTS 393). A draft
            # cut from a looped reply is never the answer, and stays a check
            pre_checked = None
            # An edit of the last checked draft is that draft, edited, sent to the check (DEFECTS 584)
            if name == _EDIT_TOOL and is_check_offered:
                edited, refused = _apply_draft_edits(session, spec.slug, args)
                if edited is None:
                    _send_tool_result(session, call, refused, written_results)
                    _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name, "result": refused[:_LOGGED_RESULT_CHARS]})
                    continue
                name, args, call = _CHECK_TOOL, {"markdown": edited}, {**call, "name": _CHECK_TOOL}
            if name == _CHECK_TOOL and is_check_offered and not is_loop_checked:
                pre_checked = _check_draft(session, spec, args, reply)
                if pre_checked == _CHECK_PASSED:
                    name, call, is_checked = spec.submit_tool, {**call, "name": spec.submit_tool}, True
            if name == spec.submit_tool and (is_check_offered or name in _FIRST_CHECKED_SUBMITS) and not is_checked:
                checked = _check_draft(session, spec, args, reply)
                # Only a draft that passed counts as checked: an answer sent after a failed check, with the finding
                # fixed and a new one in its place, is checked again rather than judged (a chapter's one
                # answer went on a second uncited id, with the check already spent on the first).
                is_checked = checked == _CHECK_PASSED
                if checked != _CHECK_PASSED:
                    result_text = _CHECKED_FIRST.format(result=checked) if is_check_offered else checked
                    _send_tool_result(session, call, result_text, written_results)
                    _append_conversation_log_row(session, {"followUp": spec.slug, "tool": _CHECK_TOOL, "result": result_text[:_LOGGED_RESULT_CHARS]})
                    continue
            # A review answer sent in the turn of its own reads was written before their results: a todo was answered
            # "unresolved" beside the find and the read that were to settle it. Not judged, no attempt spent
            if (name == spec.submit_tool and session.tool_set == tool_set_vocabulary.REVIEW_TOOL_SET
                    and any(other["name"] in _READ_TOOLS[session.tool_set] for other in tool_calls[:position])):
                _send_tool_result(session, call, _ANSWER_BEFORE_READS, written_results)
                _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name, "result": _ANSWER_BEFORE_READS})
                continue
            # An unresolved resolution with no frame's notes read in its own todo is sent back once, unjudged: three
            # todos running spent their one attempt on it, though the rule was in the instructions
            if (name == spec.submit_tool == "submit_resolution" and not is_notes_reminded
                    and str(args.get("status") or "") == "unresolved"
                    and not any(read.split(" ")[0] in review_actions.NOTES_READS
                                for read in review_file_service.list_follow_up_reads(session.ctx.paths, spec.slug))):
                is_notes_reminded = True
                reminder, program_reads = _read_notes_of_transcript_reads(session, spec)
                _send_tool_result(session, call, reminder, written_results)
                _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name,
                                                       "result": reminder[:_LOGGED_RESULT_CHARS],
                                                       conversation_log_service.PROGRAM_READS_KEY: program_reads})
                continue
            # A summary over its word limit is sent back once, unjudged, with its count: a manual's summary of 94
            # words against 59 spent its one attempt and blocked the run, its count plain arithmetic (pdf-05)
            over = _count_summary_over(spec, args) if name == spec.submit_tool == "submit_final" else None
            # and so is one with a sentence naming no entry id, a finding its check names exactly
            unsourced = _find_unsourced_summary(args) if name == spec.submit_tool == "submit_final" else None
            if unsourced is not None and not is_sources_reminded:
                is_sources_reminded = True
                _send_tool_result(session, call, unsourced, written_results)
                _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name, "result": unsourced})
                continue
            if over is not None and not is_length_reminded:
                is_length_reminded = True
                _send_tool_result(session, call, over, written_results)
                _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name, "result": over})
                continue
            if name == spec.submit_tool:
                attempts += 1
                findings = _submit(session, spec, args, reply)
                if not findings:
                    _send_tool_result(session, call, _ACCEPTED, written_results)
                    _append_conversation_log_row(session, {"followUp": spec.slug, "accepted": attempts})
                    return FollowUpOutcome(is_accepted=True, attempts=attempts, calls=calls, reason="")
                for line in render_rejection_lines(findings, attempts, max_attempts):
                    session.ctx.log.info(line)
                findings_text = conversation_log_service.render_findings(findings) + "\n" + _render_attempt(
                    spec, attempts + 1, max_attempts, is_after_compaction=False)
                result_text = f"{conversation_log_service.REJECTED_LEAD}\n{findings_text}\n\n{_REPORT_ROUTE}"
                if limits.should_ask_why_after_rejection:
                    # Asked why first, then, with an attempt left, asked to correct in a message of its own
                    _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name,
                                                 "result": conversation_log_service.render_rejection(findings)})
                    _collect_report_after_rejection(session, spec, findings, call, written_results, attempts,
                                                    is_last=attempts >= max_attempts)
                    if attempts < max_attempts:
                        _append(session, HumanMessage(content=_CORRECT_NOW.format(
                            tool=spec.submit_tool, attempt=_render_attempt(spec, attempts + 1, max_attempts, is_after_compaction=False))))
                        is_asked_why = True
                        break
                if attempts >= max_attempts:
                    # The rejection that blocks the step is logged with its findings, where the watch prints them for
                    # judging: with one attempt, every rejection is this one
                    if not limits.should_ask_why_after_rejection:
                        _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name,
                                                     "result": conversation_log_service.render_rejection(findings)})
                    return _record_blocked(session, spec, attempts, calls, conversation_log_service.render_rejected_reason(attempts, [finding["code"] for finding in findings]))
            elif name == worker_feedback_vocabulary.FEEDBACK_TOOL:
                result_text = _record_report(session, spec, args, attempts + 1)
            elif name == _CHECK_TOOL and is_check_offered:
                result_text = pre_checked if pre_checked is not None else _check_draft(session, spec, args, reply)
                is_checked = result_text == _CHECK_PASSED
                if is_loop_checked:
                    result_text = _LOOP_CHECKED.format(result=result_text)
            elif name in _READ_TOOLS[session.tool_set]:
                if _is_read_overflowing(session, name, args, tools_tokens, size_limit):
                    # The read's own call is counted already and not made yet: the handoff reports the calls before it
                    reason = _write_compaction(session, spec, findings_text, attempts, calls - 1, max_calls,
                                               _count_prompt_tokens(session, tools_tokens), size_limit, tools_tokens)
                    if reason:
                        return _record_blocked(session, spec, attempts, calls, reason)
                    is_compacted_here, call = True, {**call, "id": None}
                read = _READERS[session.tool_set](session.reading, session.reading_state, name, args)
                result_text = read.text
                if limits.should_stop_on_frame_tool_refusal and read.is_frame_tool_refused:
                    reason = f"`{name}` was refused and `budgets.failOnFrameCutFail` stops the run: {read.text}"
                    session.ctx.log.warning(f"  {spec.slug} blocked: {reason}")
                    _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name, "args": args,
                                                 "result": result_text[:_LOGGED_RESULT_CHARS]})
                    return _record_blocked(session, spec, attempts, calls, reason, tool_block=read.text)
                # Reads that note nothing are reasoned over again: a reviewer re-read the same minutes and re-derived
                # the same finding turn after turn, never calling `note`, on two todos running
                # A frame tool acts on a frame and reads nothing to note (each mask was answered with a reminder)
                if name not in write_actions.FRAME_TOOLS:
                    reads_unnoted = 0 if name == write_actions.NOTE_TOOL else reads_unnoted + 1
                if reads_unnoted >= _READS_BEFORE_NOTE_REMINDER and write_actions.NOTE_TOOL in _READ_TOOLS[session.tool_set]:
                    result_text, reads_unnoted = f"{result_text}\n\n{_NOTE_REMINDER.format(reads=reads_unnoted)}", 0
                # Where each needed frame stands, after every frame tool, so the answer is written from the program's
                # records and not from the worker's memory of ten frames
                if name in write_actions.FRAME_TOOLS and spec.inputs.get("sections"):
                    standing = document_validator.describe_open_needed_frames(
                        session.ctx.paths, list(spec.inputs["sections"]), spec.slug, session.reading_state.image_store)
                    result_text = f"{result_text}\n\n{standing}" if standing else result_text
                    # And what the follow-up asks: twice in a row after a resume a worker answered a later chapter's
                    # heading from its own memory of the run (DEFECTS 232)
                    result_text = f"{result_text}\n\n{describe_asked_headings(spec)}"
                if read.frame_path is not None and read.frame_path.is_file():
                    frames.append(read.frame_path)
            else:
                result_text = f"`{name}` is not offered in this follow-up; answer through `{spec.submit_tool}`."
            if pending_warning:
                result_text, pending_warning = f"{result_text}\n\n{pending_warning}", ""
            _send_tool_result(session, call, result_text, written_results)
            _append_conversation_log_row(session, {"followUp": spec.slug, "tool": name, "result": result_text[:_LOGGED_RESULT_CHARS]})
            if calls >= max_calls:
                break
        if is_asked_why:
            continue
        content: list[dict[str, Any]] = []
        if written_results:
            content.append({"type": "text", "text": _TEXT_CALL_RESULTS_LEAD + "\n" + "\n".join(written_results)})
        if frames:
            content += [{"type": "text", "text": "The frame you asked for:"},
                        *encode_tool_frames(frames)]
        if content:
            _append(session, HumanMessage(content=content))

    return _record_blocked(session, spec, attempts, calls, f"spent its {max_calls} calls without an accepted attempt")


def start_afresh(session: WriteConversationState) -> None:
    """Drop the conversation so the next follow-up starts from the starting messages: the plan, the notes and the follow-ups
    answered. Asked after the agenda, so the chapters' draft headings are read from the notes file and not answered
    from the moments still in the conversation."""

    session.messages = []
    session.reported_tokens = session.reported_messages = 0
