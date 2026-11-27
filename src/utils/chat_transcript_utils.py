# <!-- sync: seed/utils.chat_transcript_utils v1 -->
"""The attempt's message list, rendered as a chat log a human can read top to bottom.

`messages.jsonl` is evidence: one JSON object per turn, complete, and unreadable while a run is in
flight. This is the same bytes as a Markdown conversation - thinking, answer, tool call, tool result,
in order - so watching an attempt means opening one file in an editor rather than piping a growing
JSONL through a formatter.

Pure: messages in, Markdown out. It is rewritten wholesale after every graph step, so it must not
depend on anything that only exists once an attempt has finished.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from agent_client import finish_reason_utils
from utils import message_content_utils

__all__ = ["render_transcript"]


# A tool result is the one part that is routinely enormous - a file read, a grep over a repository.
# The full bytes are in `messages.jsonl`; this file is for reading, so it keeps the head and says how
# much it dropped. Thinking and answers are never clipped: they are what the reader came for.
_MAX_RESULT_CHARS = 1500
_MAX_ARGS_CHARS = 800


def _clip(text: str,
          limit: int) -> str:
    if len(text) <= limit:
        return text

    return f"{text[:limit]}\n[... {len(text) - limit} more characters - full text in messages.jsonl]"


def _find_backtick_runs(text: str) -> list[str]:
    runs, current = [], ""
    for character in text:
        if character == "`":
            current += character
        elif current:
            runs.append(current)
            current = ""
    if current:
        runs.append(current)

    return runs


def _wrap_in_fence(text: str,
                   language: str = '') -> str:
    """A fenced block whose fence is longer than any run of backticks inside the text it wraps."""

    ticks = "`" * max(3, max((len(run) for run in _find_backtick_runs(text)), default=0) + 1)

    return f"{ticks}{language}\n{text}\n{ticks}"


def _render_assistant(message: AIMessage) -> list[str]:
    blocks = ["### 🤖 Assistant"]
    thinking = message_content_utils.read_reasoning(message)
    if thinking:
        blocks.append("**Thinking**\n\n> " + thinking.replace("\n", "\n> "))
    if finish_reason_utils.is_reply_cut_at_output_limit(message):
        blocks.append("**Cut at the provider's output cap** - nothing after this point was generated.")
    answer = message_content_utils.read_message_text(message.content).strip()
    if answer:
        blocks.append(f"**Response**\n\n{answer}")
    calls = getattr(message, "tool_calls", None) or []
    for call in calls:
        args = _clip(str(call.get("args")), _MAX_ARGS_CHARS)
        blocks.append(f"**Tool call — `{call.get('name')}`**\n\n{_wrap_in_fence(args)}")
    if not thinking and not answer and not calls:
        blocks.append("_(empty message)_")

    return blocks


def _render_turn(message: BaseMessage) -> list[str]:
    if isinstance(message, AIMessage):
        return _render_assistant(message)
    text = message_content_utils.read_message_text(getattr(message, "content", None))
    if isinstance(message, ToolMessage):
        name = getattr(message, "name", None) or "tool"
        failed = getattr(message, "status", None) == "error"
        heading = f"### 🔧 Tool result — `{name}`" + (" _(error)_" if failed else "")

        return [heading, _wrap_in_fence(_clip(text, _MAX_RESULT_CHARS))]
    if isinstance(message, HumanMessage):
        return ["### 👤 Human", text]
    if isinstance(message, SystemMessage):
        return ["### ⚙️ System", text]

    return [f"### {type(message).__name__}", text]


def render_transcript(messages: list[BaseMessage],
                      title: str = '',
                      is_full_history: bool = False) -> str:
    """The conversation as Markdown - what the worker sees now, or with `is_full_history` every turn
    the attempt ever had. Called after every step, so it is never final."""

    subtitle = (f"_{len(messages)} messages · every turn this attempt ever had, compactions included · "
                f"rewritten after every step_" if is_full_history else
                f"_{len(messages)} messages · rewritten after every step while the attempt runs_")
    out = [f"# Transcript — {title}" if title else "# Transcript", subtitle]
    for index, message in enumerate(messages, start=1):
        out.append("---")
        out.append(f"**turn {index}**")
        out.extend(_render_turn(message))

    return "\n\n".join(out).rstrip() + "\n"
