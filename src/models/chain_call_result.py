"""What one chain call produced, as process facts only.

The chain loop's counterpart of the agent loop's run result: `outcome` says how the CALL ended, never
whether the record is good. A validator answers that, from the call log and the record on disk, after
the call has returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models import chain_reply_result

__all__ = ["ChainCallResult"]


@dataclass
class ChainCallResult:
    outcome: str                                # a `call_outcome_vocabulary` word
    detail: str = ""                            # the exception, when the call raised
    duration_s: float = 0.0
    parsed: dict[str, Any] | None = None        # the structured reply, or None when nothing parsed
    text: str = ""                              # what the model SAID, outside any tool call
    thinking: str = ""                          # what it thought, when the client kept it
    finish_reason: str = ""                     # `stop`, `length`, ... as the host reported it
    parsing_error: str = ""
    # a `chain_reply_result` channel: which spelling the structured reply came in
    reply_channel: str = chain_reply_result.NO_CHANNEL
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    prompt_duration_s: float | None = None
    output_duration_s: float | None = None

    def _build_process_facts(self) -> dict[str, Any]:
        """The facts both the run store and the call log keep about how the call went."""

        return {"outcome": self.outcome, "detail": self.detail, "durationS": round(self.duration_s, 1),
                "finishReason": self.finish_reason, "promptTokens": self.prompt_tokens,
                "outputTokens": self.output_tokens}

    def as_dict(self) -> dict[str, Any]:
        """The process facts the run store keeps per attempt. The bodies stay in the call log."""

        # One chain call is one model call and no tool call
        return {**self._build_process_facts(), "modelCalls": 1, "toolCalls": 0}

    def as_call_log(self) -> dict[str, Any]:
        """Everything the host returned that the client kept, for the call log a validator reads."""

        return {**self._build_process_facts(), "parsed": self.parsed, "text": self.text, "thinking": self.thinking,
                "parsingError": self.parsing_error, "replyChannel": self.reply_channel,
                "promptDurationS": self.prompt_duration_s, "outputDurationS": self.output_duration_s}
