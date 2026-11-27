"""The part of the input a run processes: a time window of a video or an audio recording, or a
span of a PDF's pages. A range narrows what phases 1 and 2 read and never renumbers: times stay on the recording's own
clock and pages keep the PDF's own numbers."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["InputRangeSpec", "PartWindowSpec"]


@dataclass(frozen=True)
class InputRangeSpec:
    start_s: float | None = None    # where a recording's processing starts, in seconds; None: at its start
    end_s: float | None = None      # where it ends, in seconds; None: at its end
    first_page: int | None = None   # a PDF's first page processed, from 1; None for a time range
    last_page: int | None = None    # its last page processed, from 1; None for a time range

    @property
    def is_pages(self) -> bool:
        return self.first_page is not None


@dataclass(frozen=True)
class PartWindowSpec:
    part_index: int                 # which part of the recording, in reading order, from 0
    start_s: float                  # where the window starts inside the part, in seconds of the part
    end_s: float                    # where it ends inside the part
    offset_s: float                 # where the window's start stands on the meeting clock
