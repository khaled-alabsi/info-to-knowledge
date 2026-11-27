"""One shape for "why this was rejected", for the two readers of a finding: the operator and the model.

Every phase of this playbook judges what a call returned and either asks again or blocks. Until 2026-09-28 the
console printed only that it was asking again (`16 of 20 accepted → retried together (frames 1, 4, 16, 18)`), so
the operator watching a run could not see WHAT was wrong without opening each attempt's `validation.json` - and a
frame rejected three times for the same thing looked like a slow model rather than a check to fix (the owner: "i
want to see in the console log, the issues that some step rejected for").

The two forms differ in their reader, and in nothing else: the console gets the code and the detail, the model gets
the fix as well, because it is the one that has to act on it. A finding reaches here as the report's own object or
as the plain dict the attempt loop carries.
"""

from __future__ import annotations

from typing import Any

__all__ = ["read_field", "render_for_console", "render_for_model", "render_retry_section", "render_warnings_for_console"]

# How many findings a line names before it says how many are left. A line an operator reads while a run scrolls past
# carries the first two; the rest are in the attempt's `validation.json`.
_CONSOLE_FINDINGS = 2


def read_field(finding: Any,
               name: str) -> str:
    """One field of a finding, whether it arrived as the report's object or as a dict."""

    value = finding.get(name) if isinstance(finding, dict) else getattr(finding, name, "")

    return str(value or "").strip()


def _render_one(finding: Any,
                is_fix_included: bool) -> str:
    head = ": ".join(part for part in (read_field(finding, "code"), read_field(finding, "detail")) if part)
    fix = read_field(finding, "fix") if is_fix_included else ""

    return " ".join(part for part in (head, fix) if part)


def _join_first(rendered: list[str]) -> str:
    """The first findings of a console line, joined, and how many more there are."""

    line = "; ".join(rendered[:_CONSOLE_FINDINGS])
    left = len(rendered) - _CONSOLE_FINDINGS

    return f"{line} (+{left} more)" if left > 0 else line


def render_for_console(findings: list[Any]) -> str:
    """`CODE: what was wrong; CODE: what was wrong (+2 more)` - what the operator watching the run needs."""

    rendered = [_render_one(finding, is_fix_included=False) for finding in findings]

    return _join_first([text for text in rendered if text])


def render_warnings_for_console(findings: list[Any]) -> str:
    """`what was wrong; what was wrong (+2 more)` for findings that did not reject the item: the console line already says
    it was accepted with a warning, and a check's code name means nothing to the operator (it stays in the report)."""

    # A warning with no detail is named by its code, and one with neither is left out of the line
    named = [read_field(finding, "detail") or read_field(finding, "code") for finding in findings]

    return _join_first([text for text in named if text])


def render_retry_section(findings: list | None) -> str:
    """What the last attempt was rejected for, in the validator's own words, so a retry learns why; empty for none."""

    if not findings:
        return ""

    return "\n\nYour last answer was not accepted:\n" + render_for_model(findings)


def render_for_model(findings: list[Any]) -> str:
    """Every finding, each with its fix: the retry message quotes this, so the model is told all it must change, never
    only the console's first two."""

    rendered = [_render_one(finding, is_fix_included=True) for finding in findings]

    return "; ".join(text for text in rendered if text)
