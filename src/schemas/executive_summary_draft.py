"""The first reply of phase 4's conversation: a working title and an executive summary draft. REQUIREMENTS.md R12.

Context only. It tells the agent what the document is written toward; no section is judged against it, it is never
published, and the final summary is written from the sections. It carries no entry ids because the moments it is
written from carry none.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["ExecutiveSummaryDraft"]


class ExecutiveSummaryDraft(BaseModel):
    """What the document about this meeting will conclude, written before any of it."""

    working_title: str = Field(description="one line stating the main thing the meeting establishes about its subject")
    sentences: list[str] = Field(description="three to eight sentences: the main conclusion first, then the most "
                                             "important points in descending order of weight, and what is still open")
