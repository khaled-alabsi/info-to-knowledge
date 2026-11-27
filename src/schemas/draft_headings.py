"""The draft headings of one chapter, found before the table of contents: what the presenter set out to do in the
chapter (one agenda point), and the headings it would get, in order, each with the entry it starts at. REQUIREMENTS.md
R12. The agent reads the chapter's part of the notes file first, whole, in one read; the table of contents is made from the
draft headings of every chapter.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["DraftHeading", "DraftHeadings"]


class DraftHeading(BaseModel):
    title: str = Field(description="the subject in two to eight words, specific enough to find it again")
    first_entry_id: str = Field(description="the id where this subject starts, as the notes show it, such as F<frame>.<note> or "
                                            "S<line>")
    what: str = Field(description="one sentence: what this subject establishes, for a reader who missed it")


class DraftHeadings(BaseModel):
    """The draft headings of one chapter, read from its notes."""

    purpose: str = Field(description="one sentence: what the presenter set out to do in this chapter")
    headings: list[DraftHeading] = Field(description="the subjects this chapter dealt with, in the order they came, each "
                                                     "a subject a reader would look up; short asides and single "
                                                     "questions stay in the subject around them")
