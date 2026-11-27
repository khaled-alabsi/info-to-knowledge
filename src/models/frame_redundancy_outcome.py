"""One frame's decision in the redundancy filter, after the program checked what the model wrote for it."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["KEEP", "DISCARD", "REPLACE_PREVIOUS", "DECISIONS", "FrameRedundancyOutcome"]

KEEP = "KEEP"
DISCARD = "DISCARD"
REPLACE_PREVIOUS = "REPLACE_PREVIOUS"
DECISIONS = (KEEP, DISCARD, REPLACE_PREVIOUS)


@dataclass(frozen=True)
class FrameRedundancyOutcome:
    """`covered_by`: frames of the call said to hold all of a discarded frame's knowledge. `replaces`: earlier frames
    of the call this frame holds all of. `kept_reason`: why the program kept the frame against what the model wrote, or
    None when it kept or removed it as written."""

    frame_id: str
    decision: str
    reason: str = ""
    covered_by: tuple[str, ...] = ()
    replaces: tuple[str, ...] = ()
    kept_reason: str | None = None
