"""The parts of a section's text the text check treats apart: where each figure's caption and alt text stand, and the
blocks copied from the screen as they stand, which keep the screen's own words."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["TextCheckContext"]


@dataclass(frozen=True)
class TextCheckContext:
    places: dict[str, str] = field(default_factory=dict)    # a caption or alt text -> the figure it belongs to
    screen_blocks: list[str] = field(default_factory=list)  # blocks copied from the screen, left out of the banned words
    note_times: frozenset[str] = frozenset()                 # clock times the notes hold, which are their content
