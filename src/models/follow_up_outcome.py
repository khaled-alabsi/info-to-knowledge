"""How one follow-up of phase 4's or 5's conversation ended. REQUIREMENTS.md R12."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FollowUpOutcome"]


@dataclass(frozen=True)
class FollowUpOutcome:
    is_accepted: bool                               # an attempt passed its validator
    attempts: int                                   # attempts made, the accepted one included
    calls: int                                      # tool calls made
    reason: str                                     # why it was blocked; empty when accepted
