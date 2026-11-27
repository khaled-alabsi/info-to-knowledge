"""One chain call as the dispatch service makes it: which phase's model, which kind of job, and how the call is sized,
streamed and thought through."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["NO_THINKING", "ChainCallSpec"]

# The reasoning effort that asks a model to think nothing
NO_THINKING = "none"


@dataclass(frozen=True)
class ChainCallSpec:
    """`window_tokens`: the loaded window a follow-up's output room is computed from (None: no room is set).
    `live_path`: where a streamed call writes its thinking while it runs. `reasoning_effort`: sent when given; "none"
    asks the model to think nothing (`NO_THINKING`)."""

    phase: str
    job_kind: str
    window_tokens: int | None = None
    live_path: Path | None = None
    reasoning_effort: str | None = None
