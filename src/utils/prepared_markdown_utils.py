"""A preparation step's answer for phase 3 of an audio recording: items,
each with the sections its step names, each section ending with the paragraphs it rests on. Pure text work: the
reader of the model's answer and the file's own rendering, which the program reads back.

    # Each item in detail

    ## Login page redesign

    ### Estimate
    The team estimates it at 5 points.
    [S0041, S0052]

A paragraph is named by its first line's id (`S0041` for `## S0041-S0044 · Speaker A`); a range `S0041-S0060` names
every paragraph that starts in it.
"""

from __future__ import annotations

import re
from typing import Any

from models import output_contract

__all__ = ["parse_prepared", "render_prepared", "list_cited_tokens", "is_not_said", "resolve_cited_paragraphs"]

_ITEM = re.compile(r"^##\s+(?!#)(.+?)\s*$")
_SECTION = re.compile(r"^###\s+(?!#)(.+?)\s*$")
_ID_BRACKET = re.compile(r"\[\s*S\d{4,}(?:\s*[-–]\s*S\d{4,})?(?:\s*,\s*S\d{4,}(?:\s*[-–]\s*S\d{4,})?)*\s*,?\s*\]")
_TOKEN = re.compile(r"(S\d{4,})(?:\s*[-–]\s*(S\d{4,}))?")


def _read_line_number(line_id: str) -> int:
    return int(line_id[1:])


def is_not_said(text: str) -> bool:
    """Whether a section says only that the recording does not say it, the one text that needs no paragraph id."""

    return text.strip().rstrip(".").casefold() == output_contract.PREPARED_NOT_SAID.rstrip(".").casefold()


def resolve_cited_paragraphs(cited: list[tuple[str, str]],
                             paragraphs: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """`(paragraph ids, unknown ids)` of what a section cites: a line id names the paragraph holding it, a range every
    paragraph starting in it; an id no paragraph of `paragraphs` holds is unknown."""

    found: list[str] = []
    unknown: list[str] = []
    for first, last in cited:
        low, high = _read_line_number(first), _read_line_number(last)
        if first == last:
            holding = [paragraph["first"] for paragraph in paragraphs
                       if _read_line_number(paragraph["first"]) <= low <= _read_line_number(paragraph["last"])]
        else:
            holding = [paragraph["first"] for paragraph in paragraphs
                       if low <= _read_line_number(paragraph["first"]) <= high]
        if holding:
            found += [paragraph_id for paragraph_id in holding if paragraph_id not in found]
        else:
            unknown.append(first if first == last else f"{first}-{last}")

    return found, unknown


def list_cited_tokens(text: str) -> list[tuple[str, str]]:
    """Every `(first, last)` the brackets of ids in `text` name, a single id as `(id, id)`, in order."""

    return [(first, last or first) for bracket in _ID_BRACKET.findall(text) for first, last in _TOKEN.findall(bracket)]


def _close_section(section: dict[str, Any]) -> dict[str, Any]:
    body = "\n".join(section.pop("lines")).strip()

    return {**section, "text": _ID_BRACKET.sub("", body).strip(), "cited": list_cited_tokens(body)}


def parse_prepared(text: str) -> tuple[dict[str, Any] | None, str]:
    """`({"items": [{"title", "sections": [{"name", "text", "cited"}]}]}, "")` from the answer or the file, `cited`
    the `(first, last)` id ranges its brackets name and `text` the section without them; `(None, why)` when it holds
    no item or text stands outside a section."""

    items: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        item, section = _ITEM.match(stripped), _SECTION.match(stripped)
        if item:
            items.append({"title": item.group(1), "sections": []})
        elif section and items:
            items[-1]["sections"].append({"name": section.group(1), "lines": []})
        elif stripped.startswith("```") or not stripped:
            if items and items[-1]["sections"]:
                items[-1]["sections"][-1]["lines"].append(line)
        elif items and items[-1]["sections"]:
            items[-1]["sections"][-1]["lines"].append(line)
        elif stripped.startswith("# "):
            continue
        elif items:
            return None, (f'line {number} stands under "## {items[-1]["title"]}" before its first "### <section>" '
                          "heading")
        else:
            return None, f'line {number} stands before the first "## <item>" heading'
    if not items:
        return None, 'no "## <item>" heading'

    return {"items": [{"title": item["title"], "sections": [_close_section(section) for section in item["sections"]]}
                      for item in items]}, ""


def render_prepared(step_title: str,
                    parsed: dict[str, Any]) -> str:
    """The file's text: the step's title, then each item with its sections, each section's ids on its last line."""

    lines = [f"# {step_title}", ""]
    for item in parsed["items"]:
        lines += [f"## {item['title']}", ""]
        for section in item["sections"]:
            cited = ", ".join(first if first == last else f"{first}-{last}" for first, last in section["cited"])
            lines += [f"### {section['name']}", section["text"], *([f"[{cited}]"] if cited else []), ""]

    return "\n".join(lines).rstrip() + "\n"
