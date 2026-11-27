"""The tags one call gives a finished run: the same field phase 4's final answer
holds, asked of a run whose phase 4 ended before it had one."""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.document_final import DocumentFinal

__all__ = ["RunTagsAnswer"]


class RunTagsAnswer(BaseModel):
    """The run's tags, up to the settings' `tags.workerTags`."""

    tags: list[str] = Field(description=DocumentFinal.model_fields["tags"].description)
