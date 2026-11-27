"""The one answer phase 4's call of a direct-draft run submits: the document's title, summary and agenda, and the
changes to its headings. The program builds the table of contents and puts the draft, the figures and these together.
Each schema's docstring is the description the model reads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["HeadingChange", "AgendaLine", "SubmitOutline"]


class HeadingChange(BaseModel):
    """One change to the draft's headings."""

    heading: str = Field(description="the id of the heading the outline shows, such as H03")
    action: Literal["rename", "merge_into_previous", "move_after"] = Field(
        description="rename: give the heading a new title. merge_into_previous: drop this heading and put its text at "
                    "the end of the section before it. move_after: move this heading with its subsections to follow "
                    "the heading named in `after`")
    title: str = Field(default="", description="rename: the new title, without the # marks")
    after: str = Field(default="", description="move_after: the id of the heading it goes after, such as H05")


class AgendaLine(BaseModel):
    """One line of the agenda: a chapter and what it covers."""

    heading: str = Field(description="the id of a chapter's heading (a `##` heading), such as H02")
    text: str = Field(description="one sentence saying what the chapter covers")


class SubmitOutline(BaseModel):
    """Submit the document's title, its summary, its agenda and the changes to its headings. Do this once, when every
    picture you want is cut; it ends your work."""

    title: str = Field(description="what the meeting was about, in a few words")
    summary: list[str] = Field(description="the conclusion first, as one sentence, then at most five more points, one "
                                           "sentence each")
    agenda: list[AgendaLine] = Field(description="one line for each chapter, in the order the chapters come after your "
                                                 "changes")
    changes: list[HeadingChange] = Field(default_factory=list, description="the heading changes, applied in this order; "
                                                                          "none when the headings are right")
