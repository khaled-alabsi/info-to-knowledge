"""Which headings a document is written with, and every language's form of one, for the checks that find them in a
document of any language (`models/document_heading_spec.py`)."""

from __future__ import annotations

from dataclasses import fields

from models.document_heading_spec import BY_LANGUAGE, ENGLISH, DocumentHeadingSpec

__all__ = ["list_closing_headings", "list_every", "list_repeat_exempt_headings", "select_headings"]


def select_headings(language: str | None) -> DocumentHeadingSpec:
    """The headings a document in `language` is written with; English for a language with none of its own."""

    return BY_LANGUAGE.get((language or "en").split("-")[0].lower(), ENGLISH)


def list_every(field_name: str) -> tuple[str, ...]:
    """One field's text in every language, English first, for a check that finds it in a document of any language."""

    if field_name not in {field.name for field in fields(DocumentHeadingSpec)}:
        raise ValueError(f"no document heading named {field_name}")

    return tuple(dict.fromkeys(getattr(headings, field_name) for headings in BY_LANGUAGE.values()))


def list_closing_headings() -> tuple[str, ...]:
    """Every title the closing section can have, in every language."""

    return tuple(dict.fromkeys(title for headings in BY_LANGUAGE.values() for title in headings.list_closing()))


def list_repeat_exempt_headings() -> tuple[str, ...]:
    """The sections a claim may be said again in, in every language: the summary, the agenda, the contents and the
    closing."""

    return tuple(dict.fromkeys(title for headings in BY_LANGUAGE.values() for title in
                               (headings.executive_summary, headings.agenda, headings.contents, *headings.list_closing())))
