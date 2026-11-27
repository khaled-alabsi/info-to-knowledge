"""One entry of `worker-feedback.md`: one problem a worker reported, with how many times it was reported."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WorkerFeedbackReport"]


@dataclass
class WorkerFeedbackReport:
    """`kind` from `worker_feedback_vocabulary.FEEDBACK_KINDS`; `when` the first report's time and `where` its step."""

    kind: str
    summary: str
    when: str = ""
    where: str = ""
    times: int = 1
    detail: str = ""
