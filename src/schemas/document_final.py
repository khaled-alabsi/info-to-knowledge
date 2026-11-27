"""The last reply of phase 4's conversation: the final title, executive summary and agenda. REQUIREMENTS.md R12 and
R11. The summary and the agenda are the calls path's summary call's fields (`DocumentSummary`), described as it describes them; the title joins
them because it is written from the same finished sections."""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.document_summary import AgendaLine, DocumentClaim, DocumentSummary
from schemas.executive_summary_draft import ExecutiveSummaryDraft

__all__ = ["DocumentFinal"]


class DocumentFinal(BaseModel):
    """The document's title, its executive summary and its agenda, written after every section."""

    title: str = Field(description=ExecutiveSummaryDraft.model_fields["working_title"].description)
    summary: list[DocumentClaim] = Field(description=DocumentSummary.model_fields["summary"].description)
    agenda: list[AgendaLine] = Field(description=DocumentSummary.model_fields["agenda"].description)
    tags: list[str] = Field(default_factory=list,
                            description="Up to 3 tags naming what the recording is about, each taken from the tag "
                                        "inventory listed in this follow-up, or a new short tag only when none of "
                                        "them fits; never two that mean the same.")
