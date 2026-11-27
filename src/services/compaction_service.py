"""The compaction of a conversation, one prompt for each worker.

A worker is one kind of follow-up, named by its submit tool. When the conversation passes its size limit the model writes a summary of its
own work with its worker's prompt, and the conversation goes on from the starting messages, the follow-up and ONE handoff message with three
parts: a measured block the PROGRAM writes from what it counted or stored, the model's summary, and a check the program writes after it read
the summary. After it come the follow-up's last turns word for word, up to 10,000 tokens, and the summary covers
only what is before them. The model is never asked for a count, a list of reads or a cut: only for what the program cannot know.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections import defaultdict
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

import config as app_config
from agent_client import finish_reason_utils
from chains import chain_runner
from models.compaction_request import CompactionRequest
from models.follow_up_spec import FollowUpSpec
from models.write_conversation_state import WriteConversationState
from prompts import compaction_prompt_loader
from services import conversation_log_service
from services import document_file_service
from services import review_file_service
from services import write_reading_service
from utils import document_text_utils
from utils import message_content_utils
from utils import starting_messages_utils
from utils import tool_call_text_utils

__all__ = ["pick_worker", "find_tail_start", "render_history", "compute_allowance", "compute_measured_values", "check_summary",
           "build_handoff", "write_summary", "WORKER_CAPS", "MIN_ALLOWANCE", "TAIL_TOKENS", "LIVE_SUMMARY_NAME"]

# The file beside the conversation log a compaction's summary streams into, as `thinking-live.md` does for a turn
LIVE_SUMMARY_NAME = "compaction-live.md"

# What ONE summary may hold, in tokens, by worker: each is also bounded by the room left in the conversation
WORKER_CAPS = {"executive_summary_draft": 1_500, "parts": 2_500, "draft_headings": 2_500, "chapter": 9_000, "summary": 3_000,
               "review_critique": 7_000, "review_resolution": 3_500}
# After a compaction the conversation may hold at most this share of the size limit (a proposal, not measured)
TARGET_SHARE = 0.5
THINKING_TURNS = 3
THINKING_CHARS = 6_000
SHORT_HISTORY_TURNS = 6
# Under this many tokens there is no room for a summary
MIN_ALLOWANCE = 500
# The last turns kept word for word after the handoff, at most this many tokens
TAIL_TOKENS = 10_000
_CUT_ID = re.compile(rf"\b{document_text_utils.CUT_ID_PATTERN}\b")


def pick_worker(submit_tool: str) -> str:
    """The worker of a follow-up, from its submit tool. An unknown tool raises: no generic prompt exists."""

    return compaction_prompt_loader.WORKER_OF_SUBMIT_TOOL[submit_tool]


def find_tail_start(messages: list[BaseMessage],
                    turns_start: int,
                    chars_per_token: float,
                    frame_tokens: int) -> int:
    """Where the kept tail begins: the newest messages from `turns_start` on that fit in TAIL_TOKENS, moved forward to the first reply
    of the model, so a tool result never opens the tail without its call. `len(messages)` when no whole turn fits."""

    start, tokens = len(messages), 0
    while start > turns_start:
        tokens += starting_messages_utils.estimate_prompt_tokens([messages[start - 1]], chars_per_token, frame_tokens, 0)
        if tokens > TAIL_TOKENS:
            break
        start -= 1

    return next((position for position in range(start, len(messages)) if isinstance(messages[position], AIMessage)), len(messages))


def _render_content_part(part: str | dict) -> str:
    """One part of a message's content: a string as it is, a text part's text, any other part as "[a frame]"."""

    if isinstance(part, str):
        return part

    return part.get("text", "") if part.get("type") == "text" else "[a frame]"


def _render_content(content: Any) -> str:
    """A message's content as text: a list joins its parts (anything that is neither a string nor a dict is left out)."""

    if not isinstance(content, list):
        return str(content)

    return " ".join(_render_content_part(part) for part in content if isinstance(part, (str, dict)))


# A streamed reply is an `AIMessageChunk`, its type "AIMessageChunk", not "ai": compared by type, the model's own turns
# were written "AIMESSAGECHUNK:" and never "YOU:", their thinking left out and the short history never shortened, and a
# chapter's summary call wrote the dialogue on, with tool results nobody returned (DEFECTS 632)
def _is_model_turn(message: BaseMessage) -> bool:
    return isinstance(message, AIMessage)


def _render_message(message: BaseMessage,
                    is_thinking_shown: bool) -> str:
    role = "YOU" if _is_model_turn(message) else {"human": "USER", "tool": "TOOL RESULT"}.get(message.type, message.type.upper())
    text = _render_content(message.content)
    thought = message_content_utils.read_reasoning(message)[:THINKING_CHARS] if is_thinking_shown and _is_model_turn(message) else ""
    calls = "; ".join(f"called {call['name']} {json.dumps(call.get('args') or {}, ensure_ascii=False)[:300]}"
                      for call in getattr(message, "tool_calls", None) or [])

    return "\n".join(part for part in (f"{role}: {text}", f"YOUR THINKING: {thought}" if thought else "", calls) if part)


def render_history(messages: list[BaseMessage],
                   is_short: bool = False) -> str:
    """The conversation as the text a summary is written from: its words and calls, and the thinking of the last 3 turns (the decisions of
    a model sit in its thinking, never in its reply text). A short history is the last 6 turns only."""

    body = [message for message in messages if message.type != "system"]
    ai_positions = [position for position, message in enumerate(body) if _is_model_turn(message)]
    if is_short and len(ai_positions) > SHORT_HISTORY_TURNS:
        body = body[ai_positions[-SHORT_HISTORY_TURNS]:]
        ai_positions = [position for position, message in enumerate(body) if _is_model_turn(message)]
    thinking = set(ai_positions[-THINKING_TURNS:])

    return "\n\n".join(_render_message(message, position in thinking) for position, message in enumerate(body))


def compute_allowance(request: CompactionRequest,
                      measured_text: str) -> int:
    """Tokens a summary may hold: the room left under half the size limit after the starting messages, the follow-up, the findings, the
    measured block and the kept tail, bounded by the worker's own cap."""

    session = request.session
    starting = dataclasses.replace(session.starting_messages, follow_up_text=request.spec.text, findings_text=request.findings_text)
    kept = starting_messages_utils.build_starting_messages(starting) + session.messages[request.tail_start:]
    used = starting_messages_utils.estimate_prompt_tokens(kept, session.limits.chars_per_token, session.limits.frame_tokens, request.tools_tokens)

    return min(WORKER_CAPS[request.worker], int(request.size_limit_tokens * TARGET_SHARE) - used
               - starting_messages_utils.estimate_text_tokens(measured_text, session.limits.chars_per_token))


def _read_last_submission(session: WriteConversationState,
                          spec: FollowUpSpec) -> str:
    """The last text this follow-up submitted, word for word, from the conversation log (it is written before it is judged)."""

    submits = [call for call in conversation_log_service.list_follow_up_calls(session.conversation_log_path, spec.slug)
               if call["name"] == spec.submit_tool]
    if not submits:
        return ""
    args = submits[-1]["args"]

    return str(args["markdown"]) if isinstance(args.get("markdown"), str) else json.dumps(args, ensure_ascii=False)


def _read_frame_table(session: WriteConversationState) -> str:
    """Every look the image tools made, and every cut still in use, from the frame store."""

    store = document_file_service.read_image_store(session.ctx.paths)
    lines = [f"looked at {frame}" for frame in sorted(store.get("looks") or {})]
    # Only the cuts still in use: a cut undone, or set aside by a chapter reset, cannot be placed, and handed the whole
    # store a worker mapped every heading of its chapter onto seven cuts a reset had set aside
    lines += [f"{cut_id}: cut from {cut.get('frame', '?')}" + (f", file {cut['file']}" if cut.get("file") else "")
              for cut_id, cut in sorted(document_file_service.list_cuts_in_use(store).items())]

    return "\n".join(f"  - {line}" for line in lines)


def _describe_chapter(inputs: dict[str, Any]) -> str:
    """The chapter a follow-up is about, as the measured block names it: its number, and its title when it has one."""

    label = f"chapter {int(inputs.get('chapter') or 0)} of {inputs.get('chapterCount', '?')}"
    title = inputs.get("chapterTitle") or inputs.get("title")

    return f"{label}, {title}" if title else label


def compute_measured_values(request: CompactionRequest) -> dict[str, str]:
    """Every value of the measured block, worked out by the program. A value the program cannot know says so, never a guess."""

    worker, session, spec, findings_text = request.worker, request.session, request.spec, request.findings_text
    tokens, size_limit = request.tokens, request.size_limit_tokens
    reads = conversation_log_service.list_follow_up_reads(session.conversation_log_path, spec.slug)
    inputs = spec.inputs
    values: dict[str, str] = {
        "attempt": str(request.attempts + 1), "max_attempts": str(spec.max_attempts or session.limits.max_attempts), "calls": str(request.calls),
        "max_calls": str(request.max_calls),
        "reads_list_or_none": "; ".join(reads) if reads else "none", "tokens": f"{tokens:,}", "size_limit": f"{size_limit:,}",
        "reads_used": str(session.reading_state.reads_used), "read_budget": str(session.reading.read_budget)}
    if worker == "parts":
        moments = inputs.get("moments") or []
        # The last moment's start: the recording runs on for that moment's length
        seconds = float(moments[-1]["start"]) if moments else 0.0
        values.update({"moments": str(len(moments)), "batches": str(len(inputs.get("batches") or [])),
                       "duration": f"about {round(seconds / 60)} minutes"})
    elif worker == "draft_headings":
        chapter = int(inputs.get("chapter") or 0)
        read_tokens = write_reading_service.estimate_chapter_read_tokens(session.reading, chapter, session.limits.chars_per_token)
        values.update({"chapter_label": _describe_chapter(inputs), "n": str(chapter),
                       "yes_or_no": "yes" if chapter in session.reading_state.chapters_read else "no",
                       "chapter_read_tokens": f"{read_tokens:,}",
                       "fits_or_does_not_fit": "fits" if tokens + read_tokens < size_limit else "does not fit"})
    elif worker == "chapter":
        table = _read_frame_table(session)
        last = _read_last_submission(session, spec)
        values.update({"chapter_label": _describe_chapter(inputs), "headings": str(len(inputs.get("sections") or [])),
                       "frame_table_or_none": table or "  none", "last_submission_or_none": last or "none (nothing submitted yet)",
                       "findings_or_none": findings_text.strip() or "none"})
    elif worker == "summary":
        document = app_config.settings.document
        sections_dir = document_file_service.build_sections_dir(session.ctx.paths)
        words = sum(len(path.read_text(encoding="utf-8").split()) for path in sections_dir.glob("*.md")) if sections_dir.is_dir() else 0
        values.update({"document_words": f"{words:,}", "chapters": str(len(inputs.get("parts") or [])),
                       "summary_max_words": str(document.summary_max_words), "summary_min_words": str(document.summary_min_words)})
    elif worker == "review_critique":
        values["max_todos"] = str(inputs.get("maxTodos", "not known"))
    elif worker == "review_resolution":
        store = review_file_service.read_store(session.ctx.paths)
        todos = store.get("todos") or []
        answered = [f"{todo['id']} {todo.get('status', '')}" for todo in todos if todo.get("status") not in (None, "", "pending")]
        frame_reads = [read for read in reads if read.startswith(("read_moments", "read_frame_record"))]
        values.update({"todo_id": spec.position, "total": str(len(todos)), "answered_lines": "; ".join(answered) or "none",
                       "yes_or_no": "yes" if frame_reads else "no"})

    return values


# What a check prints for a section the summary did not write: a count of other text would read as measured
_NOT_WRITTEN = "not written"


def _read_summary_section(summary: str,
                          heading: str) -> str | None:
    """The text under the summary's `## <heading>` line, up to its next `## ` line; None when it has no such line."""

    match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", summary)
    if match is None:
        return None
    rest = summary[match.end():]
    following = re.search(r"(?m)^## ", rest)

    return rest[:following.start()] if following else rest


def check_summary(request: CompactionRequest,
                  summary: str) -> dict[str, str]:
    """What the program can count in a summary once it is written: the lines it prints under `<check>`. It informs, it never blocks."""

    worker, session, spec = request.worker, request.session, request.spec
    values: dict[str, str] = {"check_lines_or_none": "none"}
    if worker == "chapter":
        sections = spec.inputs.get("sections") or []
        found_ids = set(document_text_utils.find_entry_ids(summary))
        listed = {entry_id for section in sections for entry_id in section.get("entryIds") or []}
        per_heading_lines = [f"{section.get('heading', '')}: {', '.join(sorted(set(section.get('requiredIds') or []) - found_ids)) or 'none'}"
                   for section in sections]
        store_cuts = set(document_file_service.list_cuts_in_use(document_file_service.read_image_store(session.ctx.paths)))
        per_heading = "\n".join(f"  not written in your summary, {line}" for line in per_heading_lines) or "  none"
        values.update({"per_heading_missing_and_foreign_ids": per_heading,
            "unknown_ids_or_none": ", ".join(sorted(found_ids - listed)) or "none",
            "unknown_cuts_or_none": ", ".join(sorted(set(_CUT_ID.findall(summary)) - store_cuts)) or "none"})
    elif worker == "summary":
        block = _read_summary_section(summary, "TITLE AND SUMMARY SO FAR")
        agenda = _read_summary_section(summary, "AGENDA SO FAR")
        values.update({"summary_words": _NOT_WRITTEN if block is None else str(len(block.split())),
                       "agenda_lines": _NOT_WRITTEN if agenda is None else str(sum(1 for line in agenda.splitlines() if line.strip())),
                       "other_check_lines_or_none": "none"})
    elif worker == "review_critique":
        todos = _read_summary_section(summary, "TODOS SO FAR")
        todo_count = _NOT_WRITTEN if todos is None else str(len(_TODO_KIND_LINE.findall(todos)))
        values.update({"n_todos": todo_count,
                       "missing_quotes_or_none": "not checked: the program does not cut the quoted sentences out of a summary"})
    elif worker == "review_resolution":
        values["unknown_ids_or_none"] = "not checked"
    elif worker == "draft_headings":
        values["check_lines_or_none"] = "not checked"
    # How much the summary holds, for every worker: a conversation of tens of thousands of tokens once came back as a
    # summary of 32 characters and nothing said so. It is counted and printed, never refused: what a
    # summary has to hold is the worker's judgement, not a number.
    values["summary_characters"] = f"{len(summary.strip()):,}"

    return values


def build_handoff(worker: str,
                  measured: dict[str, str],
                  summary: str,
                  checks: dict[str, str],
                  has_tail: bool = False) -> str:
    """The message a conversation gets right after a compaction: what happened, the measured block, the summary and the check, and, when
    turns were kept, one line saying they follow."""

    # A placeholder a worker's measured values and checks leave unfilled is told to the model as "not known"
    values: dict[str, str] = defaultdict(lambda: "not known")
    values.update(measured)
    values.update(checks)
    values["summary"] = summary
    handoff = compaction_prompt_loader.read_handoff(worker).format_map(values)

    return handoff + (f"\n{compaction_prompt_loader.read_tail_line()}" if has_tail else "")


def _write_summary_text(request: CompactionRequest,
                        allowance: int,
                        is_short: bool,
                        addition: str) -> str:
    """One call to the model with the worker's summary prompt: the history as text, the allowance in tokens, and `addition` after it."""

    text = compaction_prompt_loader.read_summary_prompt(request.worker).format(
        allowance=f"{allowance:,}", messages=render_history(request.session.messages[:request.tail_start], is_short))
    # Streamed with the loop check, its text so far on disk: unstreamed, a chapter's compaction ran about 27 minutes
    # with nothing to see and nothing to end it (DEFECTS 366). A summary stopped as a loop is kept up to its repeats
    live_path = request.session.conversation_log_path.with_name(LIVE_SUMMARY_NAME)
    live_path.parent.mkdir(parents=True, exist_ok=True)
    reply = chain_runner.read_streamed_reply(request.session.model, [HumanMessage(content=text + addition)], live_path)
    summary = message_content_utils.read_message_text(reply.content).strip()

    return chain_runner.drop_repeated_tail(summary) if finish_reason_utils.is_reply_cut_at_output_limit(reply) else summary


# A line of a review critique's summary that opens a todo, by its kind
_TODO_KIND_LINE = re.compile(r"(?im)^\W*(?:kind:?\s*)?(gap|open_question|contradiction|unsupported)\b")
# A reply that is only a tool call, in the tag form a model writes when it skips the tool channel: no summary. A
# conversation of 141,875 tokens was compacted onto `<call:read_notes>{"chapter": 9}</call>` and judged ok.
_CALL_TAG = re.compile(r"^\s*<\|?(?:call|tool_call|function)\b[^>]*>.*$", re.DOTALL | re.IGNORECASE)


# A line of the history as `render_history` writes a call: a model that echoes the history writes these back. A chapter
# of 136,099 tokens was compacted onto `called look {"frame": "frame-006"}` and judged ok
_RENDERED_CALL = re.compile(r"^\s*called \w+ [\[{]")
# A line as `render_history` writes a tool's result: only the program writes one, so a reply holding it wrote the
# conversation on instead of summarising it (a chapter's summary held a look and a cut-010 nobody made, DEFECTS 632;
# over the 47 compactions of all runs it names the 2 such summaries and no other)
_RENDERED_RESULT = re.compile(r"(?m)^\s*TOOL RESULT:")


def _is_summary(text: str) -> bool:
    """Whether a reply holds a summary at all: some text, not one tool call written as text, not only lines of calls
    copied from the history, and no tool result, which only the program writes."""

    lines = [line for line in text.splitlines() if line.strip()]

    return (bool(text) and not _CALL_TAG.match(text) and tool_call_text_utils.read_text_call(text) is None
            and not all(_RENDERED_CALL.match(line) for line in lines) and not _RENDERED_RESULT.search(text))


def write_summary(request: CompactionRequest,
                  allowance: int) -> tuple[str, str]:
    """The model's summary of its own work, and how it came out: `ok`, `retried`, `over allowance` or `empty`. An empty reply is asked
    once more with a shorter history; a second empty reply leaves the follow-up on the measured block alone. A summary over its allowance
    is asked once more for a shorter one. A reply that is only a tool call written as text counts as empty: the model
    replied to the history instead of summarising it."""

    # STEP 1: asked on the full history
    summary = _write_summary_text(request, allowance, is_short=False, addition="")
    outcome, is_short = "ok", False

    # STEP 2: asked again on the short history when that gave no summary
    if not _is_summary(summary):
        # Every later ask keeps to the short history that did
        is_short = True
        summary, outcome = _write_summary_text(request, allowance, is_short, addition=""), "retried"
        if not _is_summary(summary):
            return "", "empty"

    # STEP 3: asked for a shorter text when the summary is over its allowance
    chars_per_token = request.session.limits.chars_per_token
    first_tokens = starting_messages_utils.estimate_text_tokens(summary, chars_per_token)
    if first_tokens > allowance:
        write_shorter = compaction_prompt_loader.read_write_shorter().format(first_tokens=f"{first_tokens:,}",
                                                                      allowance=f"{allowance:,}",
                                                                      summary=summary)
        shorter = _write_summary_text(request, allowance, is_short, f"\n\n{write_shorter}")
        # A shorter reply that is no summary (a tool call written as text) never replaces the first one
        summary = shorter if _is_summary(shorter) else summary
        outcome = ("over allowance"
                   if starting_messages_utils.estimate_text_tokens(summary, chars_per_token) > allowance else "retried")

    return summary, outcome
