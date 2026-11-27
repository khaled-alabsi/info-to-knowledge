"""Reading one chat reply, and rendering one prompt as text. Pure functions, no I/O.

Hosts spell the same fact differently, so each fact is read in ONE function that checks the known
spellings in a fixed order and returns the first that is present. A reader that knows one spelling
reports "nothing there" against every other host, which looks exactly like the fact being absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from utils import message_content_utils

# Read for the type hints alone: imported at run time, it brought LangChain into `i2k mcp`'s start
if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

__all__ = ["read_token_usage", "render_message_as_text", "render_prompt_as_text"]

_NANOSECONDS = 1_000_000_000


def _read_seconds(metadata: dict[str, Any],
                  key: str) -> float | None:
    value = metadata.get(key)

    return round(value / _NANOSECONDS, 3) if isinstance(value, (int, float)) else None


def read_token_usage(message: BaseMessage) -> dict[str, Any]:
    """Prompt and output tokens, and where the time went. `None` where the host reported nothing."""

    usage = getattr(message, "usage_metadata", None) or {}
    metadata = getattr(message, "response_metadata", None) or {}
    prompt_tokens = usage.get("input_tokens", metadata.get("prompt_eval_count"))
    output_tokens = usage.get("output_tokens", metadata.get("eval_count"))

    return {"prompt_tokens": prompt_tokens, "output_tokens": output_tokens,
            "prompt_duration_s": _read_seconds(metadata, "prompt_eval_duration"),
            "output_duration_s": _read_seconds(metadata, "eval_duration")}


def render_message_as_text(message: BaseMessage) -> str:
    """One message as the text a reader can check. An image is named by its size, never pasted."""

    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url", ""))
            parts.append(f"[image: {len(url)} characters of data URI]")
        else:
            # Every other block as the program reads what a model said, one rule for both
            parts.append(message_content_utils.read_message_text([part]))

    return "\n\n".join(parts)


def render_prompt_as_text(messages: list[BaseMessage]) -> tuple[str, str]:
    """The system text and the human text of one call, for the attempt log and the dry run."""

    system_text = "\n\n".join(render_message_as_text(m) for m in messages if m.type == "system")
    human_text = "\n\n".join(render_message_as_text(m) for m in messages if m.type != "system")

    return system_text, human_text
