"""`TimingHandles` - what a unit of work was started with, so its performance line can be written when it ends.

Per unit, never serialised: a frame, a batch of frames, a phase-4 part, a phase or a whole session holds one from the
moment it starts to the moment `performance_log_service` writes its line.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TimingHandles"]


@dataclass(frozen=True)
class TimingHandles:
    phase: str                  # the phase the unit belongs to; "run" for a whole session
    slug: str | None            # the unit's own name in the call log: a frame, a batch, a part; None for a phase
    started_monotonic_s: float  # `time.monotonic()` when it started: what its duration is measured from
    started_at: str             # local wall time when it started, to the millisecond, for a reader
    started_epoch_s: float      # the same moment as epoch seconds, so a phase can be timed from its first unit
    first_attempt: int          # its calls in `calls.jsonl` are this attempt and every later one
    # the process's seconds waited out in pauses when it started, the pauses since then left out of its time; None for
    # a whole session, whose time stays the wall clock's
    paused_at_start_s: float | None = None
