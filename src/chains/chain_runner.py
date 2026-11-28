"""One model call of a chain pipeline: a structured reply or a markdown one, with the raw reply kept beside it.

`include_raw=True` is the point. A bare structured call hands back the parsed object or `None` and
throws the reply away, so a model that spent its whole reply thinking leaves nothing to log and
nothing to tell it apart from a model that chose to reply with nothing. With the raw reply in hand
the caller records how it ended, what it thought and what it cost.

A HOST DELIVERS A FUNCTION CALL IN TWO SPELLINGS, and both are read here, in one place. The usual
one is the reply's `tool_calls`. The other is the same call serialised into the reply's TEXT:
`{"name": "<schema>", "arguments": {...}}`. Measured on a local thinking model behind Ollama: a
complete, correct call arrived as text, nothing read it, and a good record was rejected at the cost
of a whole attempt. Only that exact shape, under the schema's own name, validated by the schema, is
a reply. Anything that merely resembles it is not.

A structured call can also return NO reply without raising. That case is named here, where the call
is made, so the reason reaches the call log in words and a validator rejects the attempt.

A MARKDOWN CALL asks for no structure at all: the model replies in text, in the document shape its instructions
teach, and a reader the caller passes turns the text into the parsed reply or says, in words, why it could not. Phase 3's
frame notes are written this way: a document a person reads, and the
program reads back. It is STREAMED and the pieces joined into one reply: a host closes a request that sends nothing
for the client's timeout, and an unstreamed reply sends nothing until it ends, so a follow-up still thinking after an
hour was closed, its thinking lost, and the client asked it again from nothing, unseen. Streamed, it runs to its
output limit and ends cut there, which the caller compacts (`thinking_compaction_service`).

Imports nothing from `graph/`: both calls work with no graph constructed.
"""

from __future__ import annotations

import collections
import re
import time

import json
from collections.abc import Callable
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableBinding
from pydantic import BaseModel, ValidationError

from models import chain_reply_result
from agent_client import finish_reason_utils
from services import call_record_service
from services import control_signal_service
from services import run_activity_service
from utils import message_content_utils

__all__ = ["invoke_structured_chain", "invoke_markdown_chain", "read_streamed_reply", "invoke_reply", "is_thinking_looping",
           "LOOP_NOTE"]

_NOTHING_PARSED = "the model returned no structured reply, and the client raised nothing"
# How a reply that ended of itself names its end
_FINISHED = "stop"


def _parse_call_written_as_text(text: str,
                                schema: type[BaseModel]) -> BaseModel | None:
    """The schema's object, when `text` is exactly one call to it in the host's own spelling."""

    try:
        payload = json.loads(text.strip())
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("name") != schema.__name__:
        return None
    if not isinstance(payload.get("arguments"), dict):
        return None
    try:
        return schema.model_validate(payload["arguments"])
    except ValidationError:
        return None


def _keep_bound_options(model: BaseChatModel) -> BaseChatModel:
    """`model` with the call options it was bound with (`reasoning_effort`, the sampling) set on a copy of the chat
    model itself, when its class holds each as a field: wrapped for structured output, a bound model loses them, and a
    tag call asked to think nothing thought 289 tokens of a 325-token answer and took minutes a run."""

    if not isinstance(model, RunnableBinding) or not isinstance(model.bound, BaseChatModel):
        return model
    fields = type(model.bound).model_fields
    if not all(key in fields for key in model.kwargs):
        return model

    return model.bound.model_copy(update=dict(model.kwargs))


def _wait_out_pause() -> None:
    """A pause asked for the run this call belongs to (its own Pause, or the queue's) waited out before the call
    starts: every model call of every phase and workflow passes here, and a call already sent cannot be paused."""

    place = call_record_service.read_place()
    if place is not None:
        control_signal_service.check(place["run_dir"])
        # the run's page says the run now waits on the model, not on its own work
        step = f" for {place['slug']}" if place.get("slug") else ""
        run_activity_service.write_activity(place["run_dir"], f"waiting for the model's answer{step}")


def invoke_structured_chain(model: BaseChatModel,
                            schema: type[BaseModel],
                            method: str,
                            messages: list[BaseMessage]) -> chain_reply_result.ChainReplyResult:
    """One structured call's reply. `reply_channel` says which spelling the reply came in, and is empty when
    there was no reply."""

    _wait_out_pause()
    # The method is the model's setting, never the library default: which of the three a host handles
    # correctly is a measurement, and on a local model the default is the one that fails.

    started_epoch_s, started, raw = time.time(), time.monotonic(), None
    # a call that raised is a row too, with no tokens
    try:
        reply = _keep_bound_options(model).with_structured_output(schema, method=method, include_raw=True).invoke(messages)
        raw = reply.get("raw")
    finally:
        call_record_service.record_call(reply=raw,
                                        started_epoch_s=started_epoch_s,
                                        duration_s=time.monotonic() - started,
                                        model=model)
    parsed = reply.get("parsed")
    if parsed is not None:
        return chain_reply_result.ChainReplyResult(raw=raw, parsed=parsed,
                                                   reply_channel=chain_reply_result.TOOL_CALL_CHANNEL)

    written_as_text = _parse_call_written_as_text(message_content_utils.read_message_text(raw.content), schema)
    if written_as_text is not None:
        return chain_reply_result.ChainReplyResult(raw=raw, parsed=written_as_text,
                                                   reply_channel=chain_reply_result.TEXT_CHANNEL)

    library_error = reply.get("parsing_error")

    return chain_reply_result.ChainReplyResult(raw=raw, parsed=None,
                                               parsing_error=str(library_error) if library_error else _NOTHING_PARSED)


# A thinking that writes the same passage again and again is a loop, and nothing it adds is new: its last
# `_LOOP_WINDOW` characters met `_LOOP_REPEATS` times within its last `_LOOP_SPAN` (a critique once streamed for over
# two hours where its batch's other critiques took 3 to 8 minutes). Six, not four: a batch of three frames of one slide
# drafts that slide's outline once per frame and once more while reading it, four honest copies, and was ended; the
# loops measured repeated eight times
# The repeats of a loop are its whole passage back to back, the text between two of them the same each time; honest
# drafts of pages that share a block (a wiki's chrome outlined on every page) have each page's own text between the
# copies, and a batch of 8 such pages was ended as a loop (pdf-04). So many repeats are a loop whatever lies between.
_LOOP_WINDOW = 400
_LOOP_REPEATS = 6
_LOOP_SPAN = 40_000
_LOOP_ANY_REPEATS = 18
# A thinking that goes through the same checklist again and again writes the same line each round, while the text
# between differs: a critique wrote "Let's look for \"A label on the screen nobody quoted\"" 42 times in 84,000
# characters before the passage test ended it (DEFECTS 340). Over the public runs' phase 3 thinking that ran to its
# end, a line of `_LOOP_LINE_CHARS` or more stood at most 25 times (an open-questions step), a critique's at most 13;
# an outline's entry, opening with "- ", is drafted once per frame that shows it and is never counted
_LOOP_LINE_CHARS = 30
_LOOP_LINE_REPEATS = 30
# Whatever shape its repeats take, a text whose recent part says nothing new is a loop: of the word runs of
# `_OLD_GRAM_WORDS` words in its last `_OLD_WINDOW` characters, `_OLD_SHARE` or more stood earlier in it. A chapter
# drafted 11 times with two closing sentences taking turns ran 30 minutes, past every shape above (DEFECTS 361). Over
# the public runs' 1,108 thinkings, answers and tool arguments of 12,000 characters or more
# (measured once, offline): 28 of the 30 loops the shapes stop reach 0.97, and of
# those never stopped only 3 do, all three repeating their own drafts or checks; an honest critique peaked at 0.96
_OLD_GRAM_WORDS = 6
_OLD_WINDOW = 12_000
_OLD_SHARE = 0.97
# A tool call's arguments, where a chapter is written, say nothing new from a lower share: over the public runs' 809
# chapter drafts, 5 of the 7 that ran on (a paragraph repeated, the request's own lines copied) had 0.54 to 1.0 of their
# recent text written earlier in them, and of the other 802 none passed 0.41 (DEFECTS 599,
# temp/defect-599/measure_repeat_share.py); a run-on draft took 25 minutes and was refused all the same
_ARGUMENTS_OLD_SHARE = 0.5
# Fewer word runs than this in the window are too few to judge (a window of long tokens: a table, a hash)
_OLD_LEAST_GRAMS = 200
# How much new thinking is streamed between two writes of the live file and two loop checks
_LIVE_EVERY_CHARS = 2_000
# A line this long written twice in one answer is a repeat, never meant
_REPEATED_LINE_CHARS = 20
LOOP_NOTE = "\n\n[stopped by the program: the thinking repeated the same passage]\n"
# Where the live file's answer so far starts, under the thinking
_LIVE_ANSWER_HEADING = "\n\n[the answer so far]\n"
_LIST_COUNTER = re.compile(r"(?m)^(\s*)\d+\.(?=\s)")


def _count_most_repeated_line(thinking: str) -> int:
    lines = [line.strip() for line in _LIST_COUNTER.sub(r"\g<1>0.", thinking).splitlines()]

    # An outline's entry ("- side panel: left") is drafted once per frame that shows it: only reasoning lines count
    return max(collections.Counter(line for line in lines if len(line) >= _LOOP_LINE_CHARS
                                   and not line.startswith("- ")).values(), default=0)


def _is_recent_text_old(text: str,
                        old_share: float = _OLD_SHARE) -> bool:
    """Whether `old_share` or more of the word runs ending in the last `_OLD_WINDOW` characters stood earlier in the
    text."""

    if len(text) < _OLD_WINDOW:
        return False
    seen, old, recent = set(), 0, 0
    words = [(match.group(0).lower(), match.end()) for match in re.finditer(r"\S+", _LIST_COUNTER.sub(r"\g<1>0.", text))]
    window_start = len(text) - _OLD_WINDOW
    for index in range(len(words) - _OLD_GRAM_WORDS + 1):
        gram = tuple(word for word, _ in words[index:index + _OLD_GRAM_WORDS])
        if words[index + _OLD_GRAM_WORDS - 1][1] > window_start:
            recent += 1
            old += gram in seen
        seen.add(gram)

    return recent >= _OLD_LEAST_GRAMS and old >= old_share * recent


def is_thinking_looping(thinking: str) -> bool:
    """Whether the thinking's last passage stands in its recent thinking `_LOOP_REPEATS` times or more, one of its
    lines stands in it `_LOOP_LINE_REPEATS` times or more, or its recent text says nothing new."""

    # A list's counter at a line's start is compared as one: a critique counted its passes ("859. Let's look at frame 79 again", "860. …") and was
    # never ended, each repeat differing from the last in its number (39c3 Photon Science talk, 332,000 characters)
    if _count_most_repeated_line(thinking) >= _LOOP_LINE_REPEATS or _is_recent_text_old(thinking):
        return True
    recent = _LIST_COUNTER.sub(r"\g<1>0.", thinking[-_LOOP_SPAN:])
    tail = recent[-_LOOP_WINDOW:]
    if len(tail) < _LOOP_WINDOW or recent.count(tail) < _LOOP_REPEATS:
        return False
    starts = [index for index in range(len(recent) - _LOOP_WINDOW + 1) if recent.startswith(tail, index)]
    between = {recent[first:second] for first, second in zip(starts[-_LOOP_REPEATS:], starts[-_LOOP_REPEATS + 1:])}

    return len(between) == 1 or len(starts) >= _LOOP_ANY_REPEATS


def drop_repeated_tail(text: str) -> str:
    """`text` up to the end of the first copy of the passage it ends repeating, or `text` itself when its last
    `_LOOP_WINDOW` characters stand in it only once, each line of `_REPEATED_LINE_CHARS` or more kept once: a chapter
    written whole, then its set-aside lines over and over, keeps the chapter once (DEFECTS 230)."""

    tail = text[-_LOOP_WINDOW:]
    starts = [index for index in range(len(text) - _LOOP_WINDOW + 1) if text.startswith(tail, index)]
    if len(tail) < _LOOP_WINDOW or len(starts) < 2:
        return text
    period = starts[1] - starts[0]
    # Back to where the text starts repeating with that period, a character at a time, then one copy past it
    begin = len(text) - period
    while begin > 0 and text[begin - 1] == text[begin - 1 + period]:
        begin -= 1

    # Lines written twice before the repeating became exact go too: no answer needs one long line twice
    kept, seen = [], set()
    for line in text[:begin + period].rstrip().split("\n"):
        if len(line.strip()) >= _REPEATED_LINE_CHARS and line.strip() in seen:
            continue
        seen.add(line.strip())
        kept.append(line)

    return "\n".join(kept).rstrip()


def _stream_reply(model: BaseChatModel,
                  messages: list[BaseMessage],
                  live_path: Path | None,
                  on_first_piece: Callable[[], None] | None) -> BaseMessage:
    """The streamed reply `read_streamed_reply` describes, before it is recorded."""

    reply = None
    checked_chars = checked_text_chars = 0
    for piece in model.stream(messages):
        # the host's first piece ends its reading of the prompt: a caller says from then on that the model writes
        if reply is None and on_first_piece is not None:
            on_first_piece()
        reply = piece if reply is None else reply + piece
        # The answer's own text loops too: a chapter wrote its draft about 70 times over, 100,675 characters in 20
        # minutes with under 2,000 of thinking, and ran to its output limit unseen (pdf-figures-05-manual)
        # and so do a tool call's arguments, where a chapter's answer is written, streamed apart from the text (the
        # wiki's chapter 8)
        arguments = "".join(str(chunk.get("args") or "") for chunk in getattr(reply, "tool_call_chunks", None) or [])
        text = message_content_utils.read_message_text(reply.content) + arguments
        if len(text) - checked_text_chars >= _LIVE_EVERY_CHARS:
            checked_text_chars = len(text)
            # The answer is written under the thinking as it comes: a chapter's answer streamed 25 minutes after its
            # thinking ended, and the live file showed nothing of it (DEFECTS 365)
            if live_path is not None:
                live_path.write_text(f"{message_content_utils.read_reasoning(reply) or ''}{_LIVE_ANSWER_HEADING}{text}",
                                     encoding="utf-8")
            if is_thinking_looping(text) or _is_recent_text_old(arguments, _ARGUMENTS_OLD_SHARE):
                finish_reason_utils.mark_thinking_looped(reply)
                break
        thinking = message_content_utils.read_reasoning(reply) or ""
        if len(thinking) - checked_chars < _LIVE_EVERY_CHARS:
            continue
        checked_chars = len(thinking)
        if live_path is not None:
            live_path.write_text(thinking, encoding="utf-8")
        if is_thinking_looping(thinking):
            finish_reason_utils.mark_thinking_looped(reply)
            if live_path is not None:
                live_path.write_text(thinking + LOOP_NOTE, encoding="utf-8")
            break

    return reply if reply is not None else model.invoke(messages)


def read_streamed_reply(model: BaseChatModel,
                        messages: list[BaseMessage],
                        live_path: Path | None = None,
                        on_first_piece: Callable[[], None] | None = None) -> BaseMessage:
    """The reply streamed and its pieces added into one message: text, thinking, how it ended and its token counts
    join as they would in one unstreamed reply. A model that cannot stream yields its whole reply as one piece. With
    `live_path`, the thinking so far is written there as it comes, so a monitor sees during the call what it is
    doing. Every `_LIVE_EVERY_CHARS` new characters of thinking the live file is written and the loop check runs; a
    thinking that loops is ended there and reported cut at the output limit, which the caller compacts. The answer's
    text, its tool calls' arguments with it, is checked the same way, every `_LIVE_EVERY_CHARS` new characters of
    it. `on_first_piece` is called once, when the host sends its first piece: before it, the host reads the prompt.
    The call is one row of the run's call record."""

    _wait_out_pause()
    started_epoch_s, started, reply = time.time(), time.monotonic(), None
    # a call that raised is a row too, with no tokens
    try:
        reply = _stream_reply(model=model,
                              messages=messages,
                              live_path=live_path,
                              on_first_piece=on_first_piece)
    finally:
        call_record_service.record_call(reply=reply,
                                        started_epoch_s=started_epoch_s,
                                        duration_s=time.monotonic() - started,
                                        model=model)

    return reply


def invoke_reply(model: BaseChatModel,
                 messages: list[BaseMessage]) -> BaseMessage:
    """The model's whole reply to `messages`, unstreamed; the call is one row of the run's call record."""

    _wait_out_pause()
    started_epoch_s, started, reply = time.time(), time.monotonic(), None
    # a call that raised is a row too, with no tokens
    try:
        reply = model.invoke(messages)
    finally:
        call_record_service.record_call(reply=reply,
                                        started_epoch_s=started_epoch_s,
                                        duration_s=time.monotonic() - started,
                                        model=model)

    return reply


def _read_answer_in_thinking(thinking: str) -> str:
    """The document a thinking ends with, written as a fenced block: from the last fence line followed by a `#`
    heading to the closing fence that ends the thinking, the code blocks nested in it kept; empty when the thinking
    does not end that way."""

    lines = thinking.rstrip().splitlines()
    if not lines or lines[-1].strip() != "```":
        return ""
    openers = [index for index, line in enumerate(lines[:-2]) if line.strip().startswith("```")
               and lines[index + 1].startswith("#")]

    return "\n".join(lines[openers[-1] + 1:-1]).strip() if openers else ""


def invoke_markdown_chain(model: BaseChatModel,
                          read_reply: chain_reply_result.MarkdownReader,
                          messages: list[BaseMessage],
                          live_path: Path | None = None) -> chain_reply_result.ChainReplyResult:
    """One call replied to in markdown. `read_reply` turns the reply's text into the parsed reply, or into None and the
    reason it could not."""

    raw = read_streamed_reply(model, messages, live_path)
    text = message_content_utils.read_message_text(raw.content)
    parsed, error = read_reply(text)
    channel = chain_reply_result.MARKDOWN_CHANNEL if parsed is not None else chain_reply_result.NO_CHANNEL
    # A reply that ended of itself with no answer, its document written as a fenced block ending the thinking: phase 3
    # blocked a batch of ten frames on a whole, right answer it wrote there (froscon 3211, DEFECTS 211)
    if parsed is None and not text.strip() and finish_reason_utils.read_finish_reason(raw) == _FINISHED:
        block = _read_answer_in_thinking(message_content_utils.read_reasoning(raw) or "")
        if block:
            in_thinking, _ = read_reply(block)
            if in_thinking is not None:
                parsed, error, channel = in_thinking, "", chain_reply_result.THINKING_CHANNEL

    return chain_reply_result.ChainReplyResult(raw=raw, parsed=parsed, parsing_error=error, reply_channel=channel)
