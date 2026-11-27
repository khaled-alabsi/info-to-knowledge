"""What `document_audit_utils.repair_text` gives: the repaired document, and one line per repair."""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["TextRepairResult"]


class TextRepairResult(NamedTuple):
    """`text` the document with its text defects repaired; `repairs` one line per repair, for the critique to read."""

    text: str
    repairs: list[str]
