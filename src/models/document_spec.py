"""What phase 4 hands the file service to assemble: the document's title, its executive summary and agenda, its
chapters (one per agenda point) and their headings, with the sections accepted for each, and what the program must
say in the closing section. The code's names keep the older words: a chapter's `topics` are its headings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["DocumentSpec", "DocumentChapterSpec", "DocumentTopicSpec"]


@dataclass(frozen=True)
class DocumentTopicSpec:
    title: str                                      # the heading's title, as the table of contents names it
    sections: list[dict[str, Any]]                  # its accepted section parts, in the order they were written


@dataclass(frozen=True)
class DocumentChapterSpec:
    title: str                                      # the chapter's title; empty when the meeting has no part
    topics: list[DocumentTopicSpec]


@dataclass(frozen=True)
class DocumentSpec:
    title: str
    chapters: list[DocumentChapterSpec]
    set_aside_ids: list[str] = field(default_factory=list)          # entries the outlines set aside, by id
    not_established: list[str] = field(default_factory=list)        # what the outlines left open
    is_arranged: bool = True                                        # False when the parts call was blocked
    summary: list[dict[str, Any]] = field(default_factory=list)     # the summary's sentences; empty when blocked
    agenda: list[dict[str, Any]] = field(default_factory=list)      # one line per part: {"title", "text"}
    not_covered: list[str] = field(default_factory=list)            # announced items no part covered
    parts: list[dict[str, Any]] = field(default_factory=list)       # the parts, with their first moment and start
    folds: list[dict[str, Any]] = field(default_factory=list)       # the short returns written in an earlier part
