"""The tags a frame's record carries for phase 4's lookups, kept clean by the program. REQUIREMENTS.md R12.
Pure functions.

A tag is either a kind of facts, from a closed list, or a subject: a system, tool, component or term the moment
establishes something about, open and reused. A tag that names an activity, or one past the limit, is dropped and
named, never a reason to reject a record: rejecting would repeat a group call of several frames for a defect the
program can remove itself.
"""

from __future__ import annotations

from typing import Any

from schemas import frame_record

__all__ = ["clean_tags", "normalise_tag", "build_tag_spellings", "list_subject_tags", "list_tags_so_far",
           "GENERIC_TAGS", "MAX_TAGS", "MAX_TAG_WORDS"]

# Words that name an activity or the whole meeting, never a subject a reader looks up.
GENERIC_TAGS = frozenset({"discussion", "demo", "demonstration", "overview", "introduction", "meeting",
                          "presentation", "questions", "summary", "agenda", "other", "misc", "general", "welcome"})
# The instruction asks for three to ten; the rest is room for the kinds.
MAX_TAGS = 12
# A tag is looked up, so it is a name, never a sentence.
MAX_TAG_WORDS = 6
# A listed tag costs its text and a bullet.
_BULLET_CHARS = 3


def _format_tag_spelling(text: str) -> str:
    """A tag as written: spaces collapsed, trailing punctuation removed, case kept."""

    return " ".join(str(text).split()).strip(" .,:;!?")


def normalise_tag(text: str) -> str:
    """A tag as compared: its spelling as written, case ignored."""

    return _format_tag_spelling(text).casefold()


def clean_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """The tags kept, a kind in lower case and a subject in its own spelling, and each one dropped, named `tag 'x'`."""

    kept: list[str] = []
    dropped: list[str] = []
    for tag in tags:
        normalised = normalise_tag(tag)
        if normalised and normalised in (normalise_tag(existing) for existing in kept):
            continue
        is_dropped = (not normalised or normalised in GENERIC_TAGS or len(normalised.split()) > MAX_TAG_WORDS
                      or len(kept) >= MAX_TAGS)
        if is_dropped:
            dropped.append(f"tag '{str(tag).strip()}'")
        else:
            kept.append(normalised if normalised in frame_record.KIND_TAGS else _format_tag_spelling(tag))

    return kept, dropped


def list_subject_tags(tags: list[str]) -> list[str]:
    """The tags that name a subject, not a kind of facts."""

    return [tag for tag in tags if normalise_tag(tag) not in frame_record.KIND_TAGS]


def build_tag_spellings(tags_in_order: list[str]) -> dict[str, str]:
    """Each normalised tag -> the spelling the document uses: its first. A form ending in "s" joins the same form
    without it when both occur."""

    spellings: dict[str, str] = {}
    for tag in tags_in_order:
        spellings.setdefault(normalise_tag(tag), _format_tag_spelling(tag))
    for normalised in list(spellings):
        if normalised.endswith("s") and normalised[:-1] in spellings:
            spellings[normalised] = spellings[normalised[:-1]]

    return spellings


def list_tags_so_far(records: list[dict[str, Any]],
                     max_chars: int) -> list[str]:
    """The distinct subject tags of `records`, most used first, then in the order first used, as many whole tags as
    fit in `max_chars`. The kinds are left out: the instruction lists them already."""

    ordered = [tag for record in sorted(records, key=lambda record: record.get("index", 0))
               for tag in list_subject_tags(clean_tags(record.get("tags") or [])[0])]
    spellings = build_tag_spellings(ordered)
    counts: dict[str, int] = {}
    for tag in ordered:
        spelling = spellings[normalise_tag(tag)]
        counts[spelling] = counts.get(spelling, 0) + 1
    listed: list[str] = []
    used = 0
    for tag in sorted(counts, key=lambda tag: -counts[tag]):
        if used + len(tag) + _BULLET_CHARS > max_chars:
            break
        listed.append(tag)
        used += len(tag) + _BULLET_CHARS

    return listed
