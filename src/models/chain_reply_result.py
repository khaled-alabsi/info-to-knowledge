"""What one chain call's model reply held, before the client keeps what it needs of it.

`chains/chain_runner.py` fills it for a structured call and for a markdown call alike;
`services/chain_dispatch_service.py` reads it into a `ChainCallResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import BaseMessage

__all__ = ["ChainReplyResult", "MarkdownReader", "TOOL_CALL_CHANNEL", "TEXT_CHANNEL", "MARKDOWN_CHANNEL",
           "THINKING_CHANNEL", "NO_CHANNEL"]

# Which spelling a structured reply came in: a real function call, the same call written into the reply's text, or a
# markdown document the program reads back.
TOOL_CALL_CHANNEL = "tool_call"
TEXT_CHANNEL = "text"
MARKDOWN_CHANNEL = "markdown"
# ... or a markdown document written as the last fenced block of the thinking, the answer left empty
THINKING_CHANNEL = "thinking"
NO_CHANNEL = ""

# Reads a reply written in markdown: its text -> the structured reply, or None, and why it could not be read
MarkdownReader = Callable[[str], tuple[dict[str, Any] | None, str]]


@dataclass(frozen=True)
class ChainReplyResult:
    raw: BaseMessage                             # the model's whole reply, kept to log how it ended
    parsed: Any                                  # the structured reply, or None when nothing could be read
    parsing_error: str = ""                      # why nothing was read, in words; empty with a structured reply
    reply_channel: str = NO_CHANNEL              # one of the channels above; NO_CHANNEL with no structured reply
