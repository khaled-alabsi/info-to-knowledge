"""One section of the facts document, written as markdown the writer lays out itself: paragraphs, lists, tables,
diagrams and frames in the order the text needs, each block ending with the ids of the notes it rests on, in
brackets, which is what lets a program check that no claim is unsourced and no entry was dropped. REQUIREMENTS.md R3.
The program reads it with `utils/section_markdown_utils.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["DocumentSection"]


class DocumentSection(BaseModel):
    """One section of a document for a reader who watched nothing."""

    markdown: str = Field(description="the section's text in markdown, without its heading: paragraphs, lists, tables, "
                                      "```mermaid diagrams, frames and #### subheadings in the order the text needs, "
                                      "each block ending with the ids of the entries it rests on in brackets, such as "
                                      "[F<frame>.<note>, S<line>]; decisions, action items and open points marked where they "
                                      "are said, `> **Decision:** ...`, `> **Action:** ...`, `> **Open:** ...`")
