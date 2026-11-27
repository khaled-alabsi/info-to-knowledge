"""One turn of a conversation as one console line: what the agent did, and how big the conversation is."""

from __future__ import annotations

import itertools

__all__ = ["render_turn_line"]


# The arguments a turn line names, in this order: enough to say what a read was, never a whole payload.
_SHOWN_ARGUMENTS = ("chapter", "chunk", "frame", "tag", "words", "start", "end")
# From this share of the size limit on, the line also says how close the size limit is.
_NEAR_SIZE_LIMIT = 0.8


def _render_target(args: dict) -> str:
    """What one call worked on: a cut by its id alone, a read by its named arguments."""

    parts = [str(args["cut"])] if "cut" in args else []

    return " ".join(parts + [f"{key} {args[key]}" for key in _SHOWN_ARGUMENTS if key in args])


def _group_by_tool(calls: list[dict]) -> list[tuple[str, list[dict]]]:
    """Consecutive calls of one tool, together, in the order they were made."""

    return [(name, list(group)) for name, group in itertools.groupby(calls, key=lambda call: call.get("name", ""))]


def _render_tool_calls(name: str,
                       targets: list[str]) -> str:
    """One tool named once, then each target, a repeat counted: `mask cut-046, cut-047 ×2, cut-040` (the owner,
    2026-09-30: "mask; mask; mask; mask" is not a nice log)."""

    counted = [(target, len(list(group))) for target, group in itertools.groupby(targets)]
    shown = ", ".join(f"{target} ×{count}" if count > 1 else target for target, count in counted if target)

    return f"{name} {shown}" if shown else name


def render_turn_line(calls: list[dict],
                     tokens: int,
                     size_limit: int | None) -> str:
    """One turn of a conversation as the console shows it: what the agent did, then how big the conversation is (the
    owner, 2026-09-28: a line must say what happened, not only a size). `  read_notes chapter 2 chunk 3 · 69,617 tok`;
    ` · 91% of the 120,000 size limit` is added only near the size limit; a turn with no tool call says so."""

    did = "; ".join(_render_tool_calls(name, [_render_target(call.get("args") or {}) for call in group])
                    for name, group in _group_by_tool(calls)) or "answered with no tool call"
    near = (f" · {int(tokens * 100 / size_limit)}% of the {size_limit:,} size limit"
            if size_limit and tokens >= size_limit * _NEAR_SIZE_LIMIT else "")

    return f"  {did} · {tokens:,} tok{near}"
