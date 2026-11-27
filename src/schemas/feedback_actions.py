"""The tool every conversation follow-up offers besides its reading tools and its one submit tool: `report_problem`.

The class's docstring is the description the model reads.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["ReportProblem"]


class ReportProblem(BaseModel):
    """Tell the people building this program about a problem with its checks, instructions or tools. Use it the moment
    a rejection looks wrong, an instruction is unclear, or a tool cannot do what you need. It never replaces your
    answer: report, then carry on with the follow-up. Never use it to say you are done."""

    kind: str = Field(description="one of the kinds listed in your instructions, such as wrong_rejection or "
                                  "unclear_instruction")
    summary: str = Field(description="one line: what went wrong, in plain words")
    detail: str = Field(default="", description="what you were trying to do, what you tried, the sentence or finding "
                                                "at fault, and what would have helped")
