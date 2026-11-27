"""The meeting's agenda: its own points, in the order they happened. REQUIREMENTS.md R9: a chapter of the document is
one agenda point. The model sees "points", the word people use for an agenda; the code keeps
its older name, parts, for the same thing.

The call sees the meeting as a numbered list of moments, and returns the moment each point starts at, so the
program can check the order and cut the entries at the point boundaries itself.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["AgendaPoint", "DocumentParts"]


class AgendaPoint(BaseModel):
    """One point of the meeting's agenda: a part of the meeting with one purpose."""

    title: str = Field(description="the meeting's own name for this point where one is shown, such as an agenda item or "
                                   "a section slide, in the document's language; otherwise what the point is about, in a few words")
    first_moment: int = Field(description="the number of the moment this point starts at; the first point starts at 1")
    covers_announced: list[str] = Field(default_factory=list,
                                        description="the items of `announced_agenda` this point covered, in their "
                                                    "announced wording; empty when it covered none")


class DocumentParts(BaseModel):
    """The points of the meeting's agenda, and the agenda its presenter announced."""

    announced_agenda: list[str] = Field(description="the items the presenter announced at the start, in the announced "
                                                    "wording and order; empty when no agenda was announced")
    points: list[AgendaPoint] = Field(description="the points of the meeting's agenda, in the order they happened")
    # REQUIREMENTS.md R13: the finished document is named `[event]-[speakers]-[date]` and opens with its topic,
    # event, speakers and date. Optional, so a meeting that names none of them leaves them empty rather than guessing.
    topic: str = Field(default="", description="what the meeting is about, its subject in three to eight words, such "
                                               "as the system, process or question it covers; not why it was held")
    event_name: str = Field(default="", description="the meeting's reason or event in two to six words, as the "
                                                    "meeting itself names it on a title card, an invitation or in its "
                                                    "opening words; otherwise its main purpose; empty when unclear")
    speakers: list[str] = Field(default_factory=list,
                                description="the names of the people who speak, as they are introduced or labelled "
                                            "on screen, main speaker first; empty when no name is shown or said")
