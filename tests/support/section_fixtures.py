"""A section for a test, written as the markdown the writing agent answers with.

A test states a section by its parts, as a dict: `paragraphs` and `diagrams` (each with its `entry_ids`), `decisions`
and `action_items` (each `{text, entry_ids}`), `not_established` (sentences, which rest on the first paragraph's entries, or `{text, entry_ids}`), and `images`
(each `{cut, after_paragraph, caption, alt_text}`). `render_section` lays them out the way an agent would: the
paragraphs in order (one a decision or an action item repeats is marked there instead), each frame after the
paragraph it names, the diagrams, then the other marked decisions, action items and open points.
"""

from __future__ import annotations

from typing import Any

__all__ = ["render_section", "build_section_answer"]


def _render_ids(entry_ids: list[str]) -> str:
    return f" [{', '.join(entry_ids)}]" if entry_ids else ""


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold().rstrip(".")


def render_section(section: dict[str, Any]) -> str:
    blocks: list[str] = []
    images_after = {int(image.get("after_paragraph") or 0): image for image in section.get("images") or []}
    # A decision or an action item that repeats a paragraph is marked where the paragraph stands, never said twice.
    marks = {_normalise(claim["text"]): (mark, claim) for field_name, mark in (("decisions", "Decision"),
                                                                             ("action_items", "Action"))
             for claim in section.get(field_name) or []}
    placed: set[str] = set()
    for number, paragraph in enumerate(section.get("paragraphs") or [], start=1):
        key = _normalise(paragraph["text"])
        if key in marks:
            mark, claim = marks[key]
            entry_ids = list(dict.fromkeys([*(paragraph.get("entry_ids") or []), *(claim.get("entry_ids") or [])]))
            blocks.append(f"> **{mark}:** {paragraph['text']}{_render_ids(entry_ids)}")
            placed.add(key)
        else:
            blocks.append(f"{paragraph['text']}{_render_ids(paragraph.get('entry_ids') or [])}")
        image = images_after.get(number)
        if image:
            blocks.append(f"![{image.get('alt_text', '')}]({image['cut']} \"{image.get('caption', '')}\")"
                          + (f"\n[{', '.join(image['entry_ids'])}]" if image.get("entry_ids") else ""))
    for diagram in section.get("diagrams") or []:
        blocks.append(f"```mermaid\n{diagram['mermaid'].strip()}\n```"
                      + (f"\n[{', '.join(diagram['entry_ids'])}]" if diagram.get("entry_ids") else ""))
    for field_name, mark in (("decisions", "Decision"), ("action_items", "Action"), ("not_established", "Open")):
        for claim in section.get(field_name) or []:
            if isinstance(claim, dict) and _normalise(claim["text"]) in placed:
                continue
            # An open point stated as a bare sentence rests on the entries of the section's first paragraph.
            first_ids = ((section.get("paragraphs") or [{}])[0]).get("entry_ids") or []
            claim = claim if isinstance(claim, dict) else {"text": claim, "entry_ids": first_ids}
            blocks.append(f"> **{mark}:** {claim['text']}{_render_ids(claim.get('entry_ids') or [])}")

    return "\n\n".join(blocks) + "\n"


def build_section_answer(section: dict[str, Any]) -> dict[str, str]:
    """The `submit_section` answer, or one item of a chapter's `sections`, for `section`."""

    return {"markdown": render_section(section)}
