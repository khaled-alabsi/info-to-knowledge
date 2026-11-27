"""The takeaway of a recording as markdown: a title, then the facts the people agreed on
and the points they left open, each one line that ends with the transcript lines it rests on.

```
# <title>

## Agreed
- The release moves to 3 November. [S0140]

## Not settled
- It is not known whether the build runs nightly or weekly: one asked for nightly, another for weekly. [S0012, S0015]
```

Phase 4's one call of a takeaway run writes it; the program reads it back here, judges it
(`validators/takeaway_validator.py`) and renders the reader's copy without the ids.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["AGREED_HEADING", "NOT_SETTLED_HEADING", "SECTION_KEYS", "parse_takeaway", "render_takeaway"]

AGREED_HEADING = "Agreed"
NOT_SETTLED_HEADING = "Not settled"
# heading -> the key its lines are kept under, in the order the document lists them
SECTION_KEYS = {AGREED_HEADING: "agreed", NOT_SETTLED_HEADING: "not_settled"}
_IDS = re.compile(r"\s*\[(S\d{4}(?:\s*,\s*S\d{4})*)\]\s*$")


def _read_item(text: str) -> dict[str, Any]:
    """`{"text", "ids"}` of one list line: the ids in its closing brackets, an empty list when it has none."""

    ids = _IDS.search(text)
    if ids is None:
        return {"text": text.strip(), "ids": []}

    return {"text": text[:ids.start()].strip(), "ids": [part.strip() for part in ids.group(1).split(",")]}


def parse_takeaway(text: str) -> tuple[dict[str, Any] | None, str]:
    """`({"title", "agreed", "not_settled"}, "")` from the answer or the file, each list of `{"text", "ids"}`;
    `(None, why)` when a section heading is missing, or text stands outside a list line."""

    parsed: dict[str, Any] = {"title": "", **{key: [] for key in SECTION_KEYS.values()}}
    seen: list[str] = []
    section = ""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        if stripped.startswith("# ") and not parsed["title"] and not seen:
            parsed["title"] = stripped[2:].strip()
        elif stripped.startswith("## "):
            heading = stripped[3:].strip()
            if heading not in SECTION_KEYS:
                return None, (f'line {number}: "## {heading}" is no section of the takeaway: the sections are '
                              + " and ".join(f'"## {name}"' for name in SECTION_KEYS))
            section = SECTION_KEYS[heading]
            seen.append(heading)
        elif stripped.startswith("- ") and section:
            parsed[section].append(_read_item(stripped[2:]))
        else:
            return None, f"line {number} is neither a heading nor a list line under one"
    missing = [heading for heading in SECTION_KEYS if heading not in seen]
    if missing:
        return None, "no " + " and no ".join(f'"## {heading}"' for heading in missing) + " section"

    return parsed, ""


def render_takeaway(parsed: dict[str, Any],
                    is_cited: bool) -> str:
    """The takeaway's text: the title, then each section with its lines, with their ids in brackets when `is_cited`
    (the program's copy) or without them (the reader's). An empty section says so in one line."""

    blocks = [f"# {parsed['title']}"] if parsed.get("title") else []
    for heading, key in SECTION_KEYS.items():
        lines = [f"- {item['text']}" + (f" [{', '.join(item['ids'])}]" if is_cited and item["ids"] else "")
                 for item in parsed.get(key) or []]
        blocks.append(f"## {heading}\n\n" + ("\n".join(lines) if lines else "Nothing."))

    return "\n\n".join(blocks) + "\n"
