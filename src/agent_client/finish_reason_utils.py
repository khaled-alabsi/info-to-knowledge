# <!-- sync: seed/agent_client.finish_reason_utils v1 -->
"""How a provider says a turn ended, read in one place for every caller.

Ollama says `done_reason`, an OpenAI-compatible host says `finish_reason`, and both spell a reply the
output limit ended as `length`. The keys are checked in a fixed order and the first with a value wins,
so a host that adds a spelling is added here once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Read for the type hints alone: imported at run time, it brought LangChain into every module that reads a finish
# reason, `i2k mcp`'s start among them (DEFECTS 663)
if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

__all__ = ["FINISH_KEYS", "CUT_AT_OUTPUT_LIMIT", "THINKING_LOOPED", "read_finish_reason",
           "is_finish_reason_cut_at_output_limit", "is_reply_cut_at_output_limit", "mark_thinking_looped"]

FINISH_KEYS = ("done_reason", "finish_reason")
CUT_AT_OUTPUT_LIMIT = "length"
# The program, not the host, ended a streamed reply whose thinking repeated itself: handled as a cut, named as a loop
THINKING_LOOPED = "thinking_looped"


def read_finish_reason(message: BaseMessage) -> str:
    """How the host says this reply ended. Empty when it did not say."""

    metadata = message.response_metadata or {}
    for key in FINISH_KEYS:
        if metadata.get(key):
            return str(metadata[key])

    return ""


def mark_thinking_looped(message: BaseMessage) -> None:
    """The reply ends as a loop under every key `read_finish_reason` looks at, so no earlier key can hide it."""

    for key in FINISH_KEYS:
        message.response_metadata[key] = THINKING_LOOPED


def is_finish_reason_cut_at_output_limit(finish_reason: str | None) -> bool:
    """Whether a finish reason already read from a reply or a call log says the output limit ended it."""

    return finish_reason in (CUT_AT_OUTPUT_LIMIT, THINKING_LOOPED)


def is_reply_cut_at_output_limit(message: BaseMessage) -> bool:
    """Whether the provider ended this turn at its output limit - so nothing after it was generated."""

    # A thinking model spends the limit on its reasoning first, so a cut turn has no reply text and no tool
    # call - which is also exactly what a worker that chose to stop looks like. The two are told
    # apart only by this field, and a caller that does not read it reports a completion of nothing.
    return is_finish_reason_cut_at_output_limit(read_finish_reason(message))
