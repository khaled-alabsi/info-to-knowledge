"""The starting messages of phase 4's conversation and the arithmetic that decides when to compact. REQUIREMENTS.md
R12. Pure functions.

The conversation is compacted: at the size limit the model writes a summary of its own work with its worker's prompt
(`agent-context/compaction/conversation/`), and the program rebuilds these starting messages from disk beside it, with a
measured block of what it counted. A summary keeps what reads as important and drops the bookkeeping, so the program
writes the bookkeeping itself.

The count the warning shows and the count that starts the compaction are one number, system message and tools
included: a compaction once started a whole system prompt late because the two counts had drifted apart.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from models.starting_messages_spec import StartingMessagesSpec

__all__ = ["build_starting_messages", "estimate_text_tokens", "estimate_prompt_tokens", "decide_warning_share", "resolve_size_limit",
           "is_prompt_truncation_suspected", "WARNING_SHARES"]

# The shares of the size limit at which the worker is warned that the conversation will be compacted
WARNING_SHARES = (0.6, 0.8)


def _render_section(heading: str,
                    lines: list[str]) -> str:
    return f"## {heading}\n" + "\n".join(lines) if lines else ""


def _compute_prompt_room(window: int,
                         max_tokens: int) -> int:
    """What the window leaves for the prompt once the answer's limit is kept free."""

    return window - max_tokens


def _count_characters_and_frames(message: BaseMessage) -> tuple[int, int]:
    """The characters of a message's text and of the tool calls it makes, and the number of frames it carries."""

    has_calls = isinstance(message, AIMessage) and bool(message.tool_calls)
    calls = len(json.dumps(message.tool_calls, ensure_ascii=False)) if has_calls else 0
    if isinstance(message.content, str):
        return len(message.content) + calls, 0
    characters, frames = calls, 0
    for part in message.content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            frames += 1
        elif isinstance(part, dict):
            characters += len(str(part.get("text", "")))
        else:
            characters += len(str(part))

    return characters, frames


def build_starting_messages(spec: StartingMessagesSpec) -> list[BaseMessage]:
    """The system message, one message with everything that stays across follow-ups, then the follow-up itself with
    any findings after it. Every message has an id derived from its place, so two rebuilds compare equal."""

    standing = [spec.plan_text,
                _render_section("Your notes", [f"- {note}" for note in spec.notes]),
                _render_section("Follow-ups answered so far", spec.done_lines),
                _render_section("This topic's parts written so far",
                                [spec.topic_parts_text] if spec.topic_parts_text else [])]
    rejection = f"\n\n## Your last answer was rejected\n{spec.findings_text}" if spec.findings_text else ""
    follow_up = spec.follow_up_text + rejection

    return [SystemMessage(content=spec.system_text, id="starting_messages-system"),
            HumanMessage(content="\n\n".join(part for part in standing if part), id="starting_messages-standing"),
            HumanMessage(content=follow_up, id="starting_messages-follow-up")]


def estimate_text_tokens(text: str,
                         chars_per_token: float) -> int:
    """About how many tokens `text` is, at the measured number of characters to a token."""

    return round(len(text) / chars_per_token)


def estimate_prompt_tokens(messages: list[BaseMessage],
                           chars_per_token: float,
                           frame_tokens: int,
                           tools_tokens: int) -> int:
    """The whole prompt as it will be sent: its text at the measured ratio, a fixed cost per frame, and the tools."""

    characters = frames = 0
    for message in messages:
        message_characters, message_frames = _count_characters_and_frames(message)
        characters += message_characters
        frames += message_frames

    return round(characters / chars_per_token) + frames * frame_tokens + tools_tokens


def decide_warning_share(count: int,
                         size_limit: int,
                         warned_shares: set[float]) -> float | None:
    """The highest share of the size limit `count` has reached above every share already warned of in this follow-up,
    or None: a count that jumps past two shares is warned of once, at the higher."""

    highest_warned = max(warned_shares, default=0.0)
    reached = [share for share in WARNING_SHARES if count >= share * size_limit and share > highest_warned]

    return max(reached) if reached else None


def resolve_size_limit(configured: int,
                       window: int,
                       max_tokens: int) -> int:
    """The configured size_limit, never above what the window leaves for the prompt once the answer's limit is kept
    free, and never below 1."""

    return max(1, min(configured, _compute_prompt_room(window, max_tokens)))


def is_prompt_truncation_suspected(reported: int,
                                   window: int,
                                   max_tokens: int) -> bool:
    """True when the host reports a prompt at the size it would have truncated it to: the program never sends that much
    on purpose, so the estimate was far too low and the start of the prompt may be gone."""

    return reported >= _compute_prompt_room(window, max_tokens)
