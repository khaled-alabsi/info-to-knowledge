"""The edits `edit_chapter_draft` makes to a chapter part's last checked draft, block by block: a part rewritten whole
for each finding of `check_chapter` lost what its draft before had right, 70 of 565 checks over the public runs
naming such a loss (DEFECTS 584). A block is a paragraph, a list, a table, a quote line or a fenced code block with the
ids line under it, numbered as the check's result lists them."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["ChapterBlockEdit", "ChapterDraftEdits"]


class ChapterBlockEdit(BaseModel):
    """One change to one numbered block of the last checked draft."""

    action: Literal["replace", "remove", "add_after", "cite"] = Field(
        description="replace the block, remove it, add a new block after it, or cite: add note ids to the block's "
                    "brackets, its text and the ids it cites kept as they are")
    block: int = Field(description="the number of the block, as the check's result lists the blocks; 0 with add_after "
                                   "adds before the first block")
    markdown: str = Field(default="", description="the block as it should read, its ids in its brackets (for replace "
                                                  "and add_after; empty for remove and cite)")
    ids: list[str] = Field(default_factory=list, description="for cite only: the note ids to add, each like F12.3 "
                                                              "or S0415")


class ChapterDraftEdits(BaseModel):
    """Changes to the last draft `check_chapter` judged, each block named by its number; the edited draft is checked
    again at once, and judged as your answer when it passes. Every block you do not name stays as it was."""

    edits: list[ChapterBlockEdit] = Field(description="the edits, each naming one block of the last checked draft")
