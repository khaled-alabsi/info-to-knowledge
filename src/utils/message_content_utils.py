# <!-- sync: seed/utils.message_content_utils v1 -->
"""A chat message's two halves: what the model thought, and what it said.

Every provider spells thinking differently, and a reader that knows one spelling logs an empty
message against the next host. Measured: `ChatOllama(reasoning=True)` puts it in
`additional_kwargs["reasoning_content"]`; an OpenAI-compatible host returns `reasoning_content` on
the message (see `services/openai_compatible_client.py`); others send `reasoning`, sometimes wrapped
as `{"content": ...}`, sometimes only in `response_metadata`; a v1 content list carries it as a
typed `thinking` or `reasoning` block beside the `text` blocks.

So the spellings live HERE, in one pure module, and both readers of a message - the JSONL evidence
and the Markdown transcript - ask the same question of it. A second copy of this list is a second
provider to support twice.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

# Read for the type hints alone: imported at run time, it brought LangChain into `i2k mcp`'s start
if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

__all__ = ["read_reasoning", "read_message_text"]


# Checked in order, in `additional_kwargs` first and then `response_metadata`.
_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")
_REASONING_BLOCK_TYPES = ("reasoning", "thinking", "reasoning_content")
_TEXT_BLOCK_TYPES = ("text", "output_text")


def _unwrap_text(field: Any) -> str:
    """A provider field that is a string here, `{"content": ...}` there, and a list somewhere else."""

    if isinstance(field, str):
        return field.strip()
    if isinstance(field, dict):
        return _unwrap_text(field.get("content") or field.get("text") or field.get("reasoning"))
    if isinstance(field, list):
        return "\n\n".join(part for part in (_unwrap_text(item) for item in field) if part)

    return ""


def _read_blocks(content: Any,
                 types: tuple[str, ...]) -> str:
    """The blocks of the given kinds in a v1 content list, in order, as one string."""

    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str) and "text" in types:
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in types:
            parts.append(_unwrap_text(block.get(block.get("type")) or block))

    return "\n\n".join(part for part in parts if part).strip()


_THINK_BLOCK = re.compile(r"\A\s*<think>(.*?)</think>\s*", re.DOTALL)


_THINK_CLOSE = "</think>"


def split_think_block(text: str) -> tuple[str, str]:
    """`(thinking, answer)` for a host that left the reasoning inline as `<think>…</think>`, or that let its end run
    into the text: a text holding a closing tag with no opening one is thinking up to its last closing tag (DEFECTS
    370: a chapter's draft and planning, 13,269 characters ending in the tag, were kept as said text; 21 of 1,279
    public phase 4 and 5 turns did)."""

    match = _THINK_BLOCK.match(text)
    if match is not None:
        return match.group(1).strip(), text[match.end():]
    if _THINK_CLOSE in text and "<think>" not in text:
        end = text.rindex(_THINK_CLOSE)
        return text[:end].strip(), text[end + len(_THINK_CLOSE):].lstrip()

    return "", text


def read_message_text(content: Any) -> str:
    """What the model SAID - a plain string, or the text blocks of a content list."""

    # A block of a kind this does not know is kept as `[its-type]` rather than dropped: a message that
    # was only an image must not read as an empty turn.

    if isinstance(content, str):
        return split_think_block(content)[1]
    if not isinstance(content, list):
        return "" if content is None else str(content)
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in _TEXT_BLOCK_TYPES:
            parts.append(_unwrap_text(block.get("text") or block))
        elif isinstance(block, dict) and block.get("type") not in _REASONING_BLOCK_TYPES:
            parts.append(f"[{block.get('type', 'block')}]")

    return "\n\n".join(part for part in parts if part)


def read_reasoning(message: BaseMessage) -> str:
    """What the model THOUGHT, whatever this provider called it. Empty when it separated nothing."""

    for source in (getattr(message, "additional_kwargs", None) or {},
                   getattr(message, "response_metadata", None) or {}):
        for key in _REASONING_KEYS:
            text = _unwrap_text(source.get(key))
            if text:
                return text

    content = getattr(message, "content", None)
    if isinstance(content, str):
        return split_think_block(content)[0]

    return _read_blocks(content, _REASONING_BLOCK_TYPES)
