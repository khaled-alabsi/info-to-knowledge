"""What a batch's follow-ups came to: the account and mood after the critique and its repair, or the questions left
open and the ones answered; and the slug of the follow-up that was not accepted, empty when every one was."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["BatchFollowUpResult"]


@dataclass
class BatchFollowUpResult:
    blocked_call_slug: str = ""
    summary: str = ""
    atmosphere: str = ""
    open_questions: list[str] = field(default_factory=list)
    answered_questions: list[str] = field(default_factory=list)
