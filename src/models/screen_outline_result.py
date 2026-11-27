"""What `frame_markdown_utils.read_screen_parts` gives: a frame's screen outline read into parts, and its shape problems."""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["ScreenOutlineResult", "ScreenPartItem"]


class ScreenPartItem(NamedTuple):
    """One part of a screen outline: how deep it is indented, and its text after the `- `."""

    depth: int
    text: str


class ScreenOutlineResult(NamedTuple):
    """`parts` the outline's parts; `problems` every line that breaks the outline's shape."""

    parts: list[ScreenPartItem]
    problems: list[str]
