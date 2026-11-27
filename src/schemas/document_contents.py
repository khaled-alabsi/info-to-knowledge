"""The title of the facts document. REQUIREMENTS.md R9: the chapters are the meeting's own parts, found by the
parts call; the contents call only states in one line what the document establishes.

The call sees every part's and every topic's title, never the entries, so a meeting of any length fits in one call.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["DocumentContents"]


class DocumentContents(BaseModel):
    """The document's title."""

    title: str = Field(description="one line stating the main thing the video establishes about its subject, in the document's language")
