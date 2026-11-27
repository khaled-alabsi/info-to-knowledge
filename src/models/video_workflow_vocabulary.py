"""The video workflow's profiles: the path a video run takes from phase 3 on.

`quality` is the workflow phases 3 to 6 always ran: batches of frames, checks, critique, repair, open questions, the
document conversation, the review and the critique collection. `fast-processing` describes each frame from its image alone, a few
frames to a session, with no check; one call of the document model writes the document from those descriptions;
phases 5 and 6 are off.
"""

from __future__ import annotations

from models import output_contract

__all__ = ["QUALITY", "FAST_PROCESSING", "WORKFLOWS", "WORKFLOW_TITLES", "WORKFLOW_DESCRIPTIONS", "FAST_OFF_PHASES"]

QUALITY = "quality"
FAST_PROCESSING = "fast-processing"
WORKFLOWS = (QUALITY, FAST_PROCESSING)
# How the page and the header name each one
WORKFLOW_TITLES = {QUALITY: "quality", FAST_PROCESSING: "fast processing"}
WORKFLOW_DESCRIPTIONS = {
    QUALITY: "frames read in checked batches with critique and repair, the document written chapter by chapter, reviewed",
    FAST_PROCESSING: "each frame described from its image alone, no check; one call writes the document; no review",
}
# The phases a fast-processing run records `off`: it has no critic, the same two an audio recording leaves out
FAST_OFF_PHASES = output_contract.AUDIO_OFF_PHASES
