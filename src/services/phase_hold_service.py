"""Why a phase that works on the whole run does not start: an earlier phase blocked or failed, or a frame blocked.

Phase 5 reviews the document and phase 6 learns from the critiques; both are held while the run has a known hole,
since a critique of a document with a hole finds that hole, and a rule learned from it teaches it.
"""

from __future__ import annotations

from models import output_contract
from services.run_store_service import RunStore

__all__ = ["list_hold_reasons"]


def list_hold_reasons(store: RunStore,
                      phase_number: int) -> list[str]:
    """Every phase before `phase_number` the store records as blocked or failed, and the frames phase 3 blocked, each
    as one reason in words. Empty when the phase may start."""

    reasons = []
    for name, number in sorted(output_contract.PHASE_NUMBERS.items(), key=lambda pair: pair[1]):
        status = store.get_phase(name)
        if number < phase_number and status in ("blocked", "failed"):
            reasons.append(f"phase {number} ({name}) {status}")
    blocked_frames = store.count_by_status().get("blocked", 0)
    if blocked_frames:
        frames_phase = output_contract.PHASE_NUMBERS[output_contract.FRAMES_PROCESSING_PHASE]
        reasons.append(f"{blocked_frames} frame(s) blocked in phase {frames_phase}")

    return reasons
