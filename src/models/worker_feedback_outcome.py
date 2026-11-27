"""What carrying out one `report_problem` gave: the sentence the worker reads back, and the console's lines."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["WorkerFeedbackOutcome"]


@dataclass
class WorkerFeedbackOutcome:
    """`console_lines` is empty when the report was refused: a report the file does not keep is never printed."""

    reply: str
    console_lines: list[str] = field(default_factory=list)
