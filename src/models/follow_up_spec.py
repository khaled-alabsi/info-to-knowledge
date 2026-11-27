"""One follow-up of phase 4's or 5's conversation: what it asks, the submit tool it is replied through, and what the
writer and validator of its reply read. REQUIREMENTS.md R12."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["FollowUpSpec"]


@dataclass(frozen=True)
class FollowUpSpec:
    submit_tool: str                                # the one submit tool offered, a key of the session's tool set's `SUBMIT_TOOLS` (`write_actions` or `review_actions`)
    slug: str                                       # the reply's slug: its call log and its file
    inputs: dict                                    # what the reply's writer and validator read, as the calls path
    text: str                                       # the follow-up message: its data and its ask
    position: str                                   # where the agent is, for the note after a compaction: a chapter's slug, or the review's todo id
    is_size_warned: bool = True                     # False: no size warning is sent (the draft headings)
    max_calls: int | None = None                    # this follow-up's own tool-call limit; the conversation's when None
    max_attempts: int | None = None                 # this follow-up's own limit of rejected attempts; the conversation's when None
    figures: tuple[Path, ...] = ()                  # image paths sent as one message right after the request, in order
