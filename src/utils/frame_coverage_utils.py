"""Which frames the redundancy filter removes, from the checked decisions of every call: `removed` maps a frame id to
the frames said to hold all of its knowledge. A removed frame stands only when a chain of its covering frames reaches
a frame that stays (F2 inside F3 inside F5 puts F2 inside F5); every other removed frame is kept, so no knowledge is
lost because its last copy was removed too."""

from __future__ import annotations

from models.frame_redundancy_outcome import DISCARD, REPLACE_PREVIOUS, FrameRedundancyOutcome

__all__ = ["apply_decisions", "list_standing_removals"]


def apply_decisions(removed: dict[str, list[str]],
                    decisions: list[FrameRedundancyOutcome]) -> None:
    """Each frame's own DISCARD first, then the replacements, so a later frame replacing one kept in the same call
    wins: it states that it holds all of that frame."""

    for decision in decisions:
        if decision.decision == DISCARD:
            removed[decision.frame_id] = list(decision.covered_by)
    for decision in decisions:
        if decision.decision == REPLACE_PREVIOUS:
            removed.pop(decision.frame_id, None)
            for replaced_id in decision.replaces:
                removed[replaced_id] = [decision.frame_id]


def _list_safe_ids(removed: dict[str, list[str]],
                   kept_ids: set[str]) -> set[str]:
    """The removed ids whose covering chain reaches a kept frame."""

    safe_ids: set[str] = set()
    is_growing = True
    while is_growing:
        is_growing = False
        for frame_id, coverer_ids in removed.items():
            if frame_id not in safe_ids and any(i in kept_ids or i in safe_ids for i in coverer_ids):
                safe_ids.add(frame_id)
                is_growing = True

    return safe_ids


def list_standing_removals(removed: dict[str, list[str]],
                           all_ids: list[str]) -> dict[str, list[str]]:
    """`removed` without every frame whose covering chain reaches no frame that stays: a cycle (F1 inside F2, F2 inside
    F1) keeps both."""

    standing = dict(removed)
    while True:
        kept_ids = set(all_ids) - set(standing)
        unsafe_ids = set(standing) - _list_safe_ids(standing, kept_ids)
        if not unsafe_ids:
            break
        for frame_id in unsafe_ids:
            del standing[frame_id]

    return standing
