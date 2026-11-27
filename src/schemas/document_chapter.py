"""The text of the headings of one chapter, or of one part of a long chapter (past `document.chapterEntries` notes), in
the scaffold's order, in ONE markdown that the program splits at each heading's own line (an array of sections was
merged into one item three times in a row, so the reply is one text). REQUIREMENTS.md R12."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["DocumentChapter"]


class DocumentChapter(BaseModel):
    """Every heading of one chapter, written."""

    markdown: str = Field(description="the whole chapter in markdown: for each heading, in the order the follow-up lists "
                                      "them, first the heading's own line exactly as listed, such as "
                                      "`### Refund limits (T<chapter>.<heading>)`, then that heading's text without any other heading "
                                      "of level 3 or above")
