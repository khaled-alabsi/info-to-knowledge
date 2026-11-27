"""Which phases one `run` line asked for, as data. Built once, by the run command, from the flags."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PhaseRequest"]


@dataclass(frozen=True)
class PhaseRequest:
    first: int                                  # the first phase number to RUN
    last: int                                   # the last phase number to run, inclusive
    selected_slugs: tuple[str, ...] = ()        # empty means every item of the item phase
