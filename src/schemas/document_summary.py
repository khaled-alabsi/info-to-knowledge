"""The two sections above the first chapter: the executive summary and the agenda. REQUIREMENTS.md R11.

Written last, from the agenda points and from what the sections already say, so every sentence can cite the entries the
sections rest on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["DocumentClaim", "AgendaLine", "DocumentSummary"]


class DocumentClaim(BaseModel):
    """One sentence of the executive summary, and its entries."""

    text: str = Field(description="one sentence of plain words, in the document's language")
    entry_ids: list[str] = Field(description="the ids of the entries it rests on; at least one")


class AgendaLine(BaseModel):
    """What one point of the agenda did."""

    part_number: int = Field(description="the number of the agenda point this line is about")
    text: str = Field(description="what the point did, starting with a verb such as Introduces, Walks through, "
                                  "Demonstrates, Discusses or Answers questions on; it names the announced items the "
                                  "point covered, in their announced wording")


class DocumentSummary(BaseModel):
    """The executive summary and the agenda."""

    summary: list[DocumentClaim] = Field(description="the executive summary, one or two sentences per item, the most "
                                                      "important first: the main conclusion, then the points in "
                                                      "descending order of weight, then one sentence counting the rest; "
                                                      "every item, that last one included, names the ids of the "
                                                      "entries it rests on")
    agenda: list[AgendaLine] = Field(description="one line per agenda point, in the agenda's order; empty when you "
                                                 "were given no points")
