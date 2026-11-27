"""The finished document's name, `[meeting reason or event]-[speakers]-[date]`. REQUIREMENTS.md R13. Pure functions.

The agent decides the event and the speakers; the program decides the date and writes every part in one form a file
name can safely hold on every system: lower case, ASCII, words joined by hyphens. A part nobody established is left
out, never guessed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date

__all__ = ["build_slug", "build_document_name", "find_date_in_name", "render_front_matter", "EVENT_WORDS",
           "MAX_SPEAKERS"]

# The event keeps its first six words and at most three speakers are named, so the name stays readable in a folder
# listing.
EVENT_WORDS = 6
MAX_SPEAKERS = 3
_GERMAN_LETTERS = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
_NOT_A_WORD = re.compile(r"[^a-z0-9]+")
# `20260303`, `2026-03-03` and `2026_03_03`, not inside a longer run of digits.
_DATE = re.compile(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?!\d)")


def _format_iso_date(year: str,
                     month: str,
                     day: str) -> str:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return ""


def build_slug(text: str) -> str:
    """`text` as lower-case ASCII words joined by hyphens: umlauts written out, other accents dropped."""

    written_out = "".join(_GERMAN_LETTERS.get(letter, letter) for letter in text)
    ascii_text = unicodedata.normalize("NFKD", written_out).encode("ascii", "ignore").decode("ascii")

    return _NOT_A_WORD.sub("-", ascii_text.casefold()).strip("-")


def build_document_name(event: str,
                        speakers: list[str],
                        meeting_date: str,
                        fallback: str) -> str:
    """The event's first words, up to three speakers and the date, each part slugified, empty parts left out. With no
    event, `fallback` (the document's title) stands in for it."""

    event_part = "-".join(build_slug(event.strip() or fallback).split("-")[:EVENT_WORDS])
    # A blank speaker, or one whose name slugs to nothing, takes no named speaker's place
    speaker_parts = [part for part in (build_slug(speaker) for speaker in speakers) if part][:MAX_SPEAKERS]

    return "-".join(part for part in [event_part, *speaker_parts, meeting_date] if part)


def find_date_in_name(name: str) -> str:
    """The first real calendar date written in `name`, as `YYYY-MM-DD`, or an empty string."""

    for match in _DATE.finditer(name):
        found = _format_iso_date(*match.groups())
        if found:
            return found

    return ""


def render_front_matter(topic: str,
                        event: str,
                        speakers: list[str],
                        meeting_date: str,
                        tags: list[str] = (),
                        part: str = "") -> str:
    """The document's metadata as YAML front matter: topic, event, speakers, date and the run's tags, always all five, an empty value
    for one nobody established, and `part`, the part of the recording processed ("12:30 to 1:05:00 of 1:32:10"), only
    when the run processed a part. Every string is written JSON-quoted, which YAML reads as a
    string, so a colon or a quote in a name stays data; the date, always `find_date_in_name`'s `YYYY-MM-DD` or empty, is
    left bare, so YAML reads it as a date."""

    lines = [f"topic: {json.dumps(topic.strip(), ensure_ascii=False)}",
             f"event: {json.dumps(event.strip(), ensure_ascii=False)}",
             f"speakers: {json.dumps([speaker.strip() for speaker in speakers if speaker.strip()], ensure_ascii=False)}",
             f"date: {meeting_date if meeting_date else json.dumps('')}",
             *([f"part: {json.dumps(part, ensure_ascii=False)}"] if part else []),
             f"tags: {json.dumps(list(tags), ensure_ascii=False)}"]

    return "---\n" + "\n".join(lines) + "\n---\n\n"
