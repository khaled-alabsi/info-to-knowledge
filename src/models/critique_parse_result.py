"""What `critique_markdown_utils.parse_critique` gives: the critique read, or None and why it could not be."""

from __future__ import annotations

from typing import Any, NamedTuple

__all__ = ["CritiqueParseResult"]


class CritiqueParseResult(NamedTuple):
    """`critique` is `{"is_nothing_to_criticise": bool, "rules": [...]}`, or None when the text holds no critique; `why`
    says why it could not be read, "" when it was."""

    critique: dict[str, Any] | None
    why: str
