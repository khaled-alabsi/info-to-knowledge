"""What a frame suggested showing a reader, read from a record of either shape. REQUIREMENTS.md R10.

Since 2026-09-28 a record carries `screenshots`, a list: one entry per part of the screen worth showing, each with
its `part`, its `why` and its `importance`. Before that it carried three parallel fields (`screenshot`,
`screenshot_why`, `screenshot_part`) that could only ever name one part and could contradict each other, and every
run before that date wrote them. Both shapes are read here, in one place, so no later step has to know there were
two - and a run resumed across the change stays readable.
"""

from __future__ import annotations

import re
from typing import Any

from schemas import frame_record

__all__ = ["read_suggestions", "render_suggestion_lines", "is_written_as_block", "list_needed_slugs",
           "list_importance_words"]


def _read_text(mapping: dict[str, Any],
               key: str) -> str:
    """`mapping[key]` as text, stripped; empty when it is missing or empty."""

    return str(mapping.get(key) or "").strip()


def read_suggestions(record: dict[str, Any]) -> list[dict[str, str]]:
    """This record's suggestions as `{"part", "why", "importance"}`, newest shape first, an older record's three
    fields folded into one entry. An importance the vocabulary does not hold is dropped: phase 5 reads these words."""

    if record.get("screenshots"):
        entries = [{"part": _read_text(entry, "part"), "why": _read_text(entry, "why"),
                    "importance": _read_text(entry, "importance")}
                   for entry in record["screenshots"] if isinstance(entry, dict)]

        return [entry for entry in entries if entry["importance"] in list_importance_words()]
    importance = _read_text(record, "screenshot")
    if importance not in list_importance_words():
        return []

    return [{"part": _read_text(record, "screenshot_part"), "why": _read_text(record, "screenshot_why"),
             "importance": importance}]


def render_suggestion_lines(records: list[dict[str, Any]]) -> list[str]:
    """One line per suggestion, `frame-012 needed: why (which part)`, the form phase 4's chapter requests and phase
    5's conversations are shown."""

    lines = []
    for record in records:
        for entry in read_suggestions(record):
            part = f" ({entry['part']})" if entry["part"] else ""
            line = f"{record['slug']} {entry['importance']}: {entry['why']}{part}"
            if is_written_as_block(record):
                line += (" — its screen is code its notes hold whole as a markdown block: write that block in your text "
                         "and `leave_out` this frame as written, never a cut of it")
            lines.append(line)

    return lines


# A screen of code or a terminal (DEFECTS 583): its notes' fenced block holds every line a picture would, readable,
# searchable and copyable; chapter 1 of Lecture 7 cut five pictures of code and a todo list it was told were needed
_CODE_SCREEN_KIND = re.compile(r"\b(code editor|editor|terminal|console|shell)\b", re.IGNORECASE)


def is_written_as_block(record: dict[str, Any]) -> bool:
    """Whether `record`'s screen is code or a terminal its notes hold whole as a fenced markdown block: written as that
    block, never placed as a picture."""

    return bool(_CODE_SCREEN_KIND.search(str(record.get("screen_kind") or ""))
                and "```" in str(record.get("as_markdown") or ""))


def list_needed_slugs(records: list[dict[str, Any]]) -> list[str]:
    """The frames with at least one suggestion a reader cannot do without, a screen its notes hold as a code block
    left out: it is written as that block (DEFECTS 583)."""

    return [record["slug"] for record in records if not is_written_as_block(record)
            and any(entry["importance"] == frame_record.SCREENSHOT_NEEDED for entry in read_suggestions(record))]


def list_importance_words() -> tuple[str, ...]:
    """The importance words, from the schema, so a check and a prompt never spell them separately."""

    return frame_record.SCREENSHOT_IMPORTANCE_VALUES
