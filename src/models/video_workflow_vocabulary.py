"""The video workflow's profiles: the path a video run takes from phase 3 on.

`quality` is the workflow phases 3 to 6 always ran: batches of frames, checks, critique, repair, open questions, the
document conversation, the review and the critique collection. `direct-draft` is the Fast speed's workflow: the same
models, the frames written straight into a draft already in chapters, eight to a call, then one small call that titles
and summarises it; phases 5 and 6 are off. `fast-processing` is the earlier fast workflow, kept for the runs made with
it: each frame described from its image alone by a small model, one call of the document model writes the document from
those descriptions; phases 5 and 6 are off.
"""

from __future__ import annotations

from models import output_contract

__all__ = ["QUALITY", "FAST_PROCESSING", "DIRECT_DRAFT", "WORKFLOWS", "WORKFLOW_TITLES", "WORKFLOW_DESCRIPTIONS",
           "FAST_OFF_PHASES", "NO_CRITIC_WORKFLOWS"]

QUALITY = "quality"
FAST_PROCESSING = "fast-processing"
DIRECT_DRAFT = "direct-draft"
WORKFLOWS = (QUALITY, DIRECT_DRAFT, FAST_PROCESSING)
# How the page and the header name each one
WORKFLOW_TITLES = {QUALITY: "quality", DIRECT_DRAFT: "direct draft", FAST_PROCESSING: "fast processing"}
WORKFLOW_DESCRIPTIONS = {
    QUALITY: "frames read in checked batches with critique and repair, the document written chapter by chapter, reviewed",
    DIRECT_DRAFT: "frames written straight into a draft in chapters, eight to a call, no check; one small call titles "
                  "and summarises it; no review",
    FAST_PROCESSING: "each frame described from its image alone, no check; one call writes the document; no review",
}
# The phases a run of a workflow with no critic records `off`: the same two an audio recording leaves out
FAST_OFF_PHASES = output_contract.AUDIO_OFF_PHASES
# The workflows that have no review and no critique collection
NO_CRITIC_WORKFLOWS = (DIRECT_DRAFT, FAST_PROCESSING)
