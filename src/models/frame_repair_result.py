"""What a batch's frames came to after the repair its settings allow: the frames written and blocked, and the batch's
account and mood when a repair call wrote them again (empty otherwise)."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["FrameRepairResult"]


@dataclass
class FrameRepairResult:
    written: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    summary: str = ""
    atmosphere: str = ""
