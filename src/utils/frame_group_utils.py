"""The similarity recheck's calls, plain code: the lists of frames that look alike, read straight from a table of
which frame looks like which, and each list cut into calls of a set size."""

from __future__ import annotations

import numpy

__all__ = ["list_alike_groups", "split_into_calls"]


def list_alike_groups(alike: numpy.ndarray) -> list[list[int]]:
    """The lists of frames that look alike, in time order: `alike[i, j]` says frame i looks like frame j, and a frame
    joins the list of every frame it looks like (so a list can run on through frames that each look like the next).
    A frame that looks like no other is in no list."""

    seen: set[int] = set()
    groups = []
    for start in range(alike.shape[0]):
        if start in seen or not alike[start].any():
            continue
        group, waiting = set(), [start]
        while waiting:
            frame = waiting.pop()
            if frame in group:
                continue
            group.add(frame)
            waiting.extend(int(other) for other in numpy.flatnonzero(alike[frame]) if other not in group)
        seen |= group
        groups.append(sorted(group))

    return groups


def split_into_calls(groups: list[list[int]],
                     size: int) -> list[list[int]]:
    """Each list as one call when it fits, small lists joined into one call while they fit; a list longer than `size`
    in calls of `size` frames, each starting half a call after the one before, so every frame is shown beside its
    neighbours and no call holds one frame alone."""

    calls: list[list[int]] = []
    step = max(1, size // 2)
    for group in groups:
        if len(group) > size:
            starts = list(range(0, len(group) - size, step)) + [len(group) - size]
            calls.extend(group[start:start + size] for start in starts)
        elif calls and len(calls[-1]) + len(group) <= size:
            calls[-1] = sorted(calls[-1] + group)
        else:
            calls.append(list(group))

    return calls


def count_distinct_frames(calls: list[list[int]]) -> int:
    """How many frames `calls` show, each once however many overlapping calls hold it."""

    return len({position for call in calls for position in call})
