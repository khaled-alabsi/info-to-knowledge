"""One answer of `worker-feedback-answers.md`: what the person watching the run decided about one worker report."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WorkerFeedbackAnswerItem"]


@dataclass
class WorkerFeedbackAnswerItem:
    """`when` names the report it answers by that report's time; `times` is the report's count the answer was written
    at, so a report raised again after it is open until it is answered again; `text` is the decision in plain words."""

    when: str
    times: int
    text: str
