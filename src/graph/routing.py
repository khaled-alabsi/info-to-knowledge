"""Every conditional edge. Pure functions of state, so each is a one-line unit test.

The return annotations are the documentation. Keep them exact.

Six numbered phases: transcribe, frames_preparing (capture and crop the frames; for an audio recording,
speech_structuring turns the transcript into paragraphs), frames_processing (the frame loop; for an audio recording,
prepare_notes, the profile's preparation steps), finalize (the document, written from the merged notes it builds in
memory, its images placed by its chapters, then published as the folder a reader opens), review (a critic's todos,
resolved from the sources, R16) and critique_collection (every batch's critique, collected into one rule set). The
graph routes on two things: what the queue holds, and which phases this run was asked for.

`done` is the edge out of the graph for a run told to stop before the last phase, and for one ended by a blocked
frame, a drain, a blocked preparation or a blocked phase 4. `failed` is the edge out when phase 1 or 2 did not
complete (failed or blocked: every later phase is built on their files) and when phase 4 failed.
"""

from __future__ import annotations

from typing import Literal

from graph.graph_state import RunState
from models import output_contract

FRAMES_PROCESSING_PHASE_NUMBER = output_contract.PHASE_NUMBERS["frames_processing"]
FINALIZE_PHASE_NUMBER = output_contract.PHASE_NUMBERS["finalize"]
REVIEW_PHASE_NUMBER = output_contract.PHASE_NUMBERS["review"]
CRITIQUE_COLLECTION_PHASE_NUMBER = output_contract.PHASE_NUMBERS[output_contract.CRITIQUE_COLLECTION_PHASE]
DOCUMENT_RESHAPING_PHASE_NUMBER = output_contract.PHASE_NUMBERS[output_contract.DOCUMENT_RESHAPING_PHASE]
LAST_PHASE_NUMBER = max(output_contract.PHASE_FILES)


def _read_phase_status(state: RunState,
                       phase: str) -> str | None:
    """The status the run recorded for `phase`, None while it has none."""

    return state.get("phases", {}).get(phase)


def _has_frames_left(state: RunState) -> bool:
    # The edge INTO the loop carries the same emptiness check as the loop-back edge. An empty queue is
    # the normal state of a dry run and of a resume where every frame is terminal, and entering the
    # loop node without a guaranteed frame is an IndexError.
    return state.get("cursor", 0) < len(state.get("queue", []))


def _route_to_document_reshaping(state: RunState) -> Literal["document_reshaping", "done"]:
    return "document_reshaping" if is_phase_selected(state, DOCUMENT_RESHAPING_PHASE_NUMBER) else "done"


def _route_to_critique_collection(state: RunState) -> Literal["critique_collection", "document_reshaping", "done"]:
    if is_phase_selected(state, CRITIQUE_COLLECTION_PHASE_NUMBER):
        return "critique_collection"

    return _route_to_document_reshaping(state)


def route_after_critique_collection(state: RunState) -> Literal["document_reshaping", "done"]:
    """Phase 7 edits the published document whatever phase 6 did; it holds itself without one, and is off unless the
    run asked for it."""

    return _route_to_document_reshaping(state)


def _route_past_the_document(state: RunState) -> Literal["review", "critique_collection", "document_reshaping", "done"]:
    if is_phase_selected(state, REVIEW_PHASE_NUMBER):
        return "review"

    return _route_to_critique_collection(state)


def _route_past_frames_processing(state: RunState) -> Literal["finalize", "review", "critique_collection", "document_reshaping",
                                                              "done"]:
    if is_phase_selected(state, FINALIZE_PHASE_NUMBER):
        return "finalize"

    return _route_past_the_document(state)


def is_phase_selected(state: RunState,
                      number: int) -> bool:
    """Whether this run was asked for phase `number`, from `--phase`, `--from` and `--to`. Read by the routes and by
    the first two phase nodes."""

    return state.get("first_phase", 1) <= number <= state.get("last_phase", LAST_PHASE_NUMBER)


def route_after_review(state: RunState) -> Literal["critique_collection", "document_reshaping", "done"]:
    """Phase 6 learns from the critiques phase 3 left, so the route always reaches it when it is asked for. Phase 6
    then holds itself when an earlier phase blocked or failed, phase 5 included (`phase_hold_service`)."""

    return _route_to_critique_collection(state)


def route_after_transcribe(state: RunState) -> Literal["frames_preparing", "speech_structuring", "failed"]:
    """Phase 1's failure stops the run: the frames are given their speech from its file."""

    if _read_phase_status(state, "transcribe") != "complete":
        return "failed"

    # An audio recording has no frames to take: its phase 2 structures the speech
    return "speech_structuring" if state.get("is_audio") else "frames_preparing"


def route_after_frames_preparing(state: RunState) -> Literal["frames_processing", "finalize", "review", "critique_collection",
                                                             "document_reshaping", "done", "failed"]:
    """Phase 2's failure stops the run: every later phase is built on the frame list."""

    if _read_phase_status(state, "frames_preparing") != "complete":
        return "failed"
    # A batch finished on the way in, its follow-ups left by an earlier pass, blocked: phase 3 stops the run there
    if state.get("blocked"):
        return "done"
    if is_phase_selected(state, FRAMES_PROCESSING_PHASE_NUMBER) and _has_frames_left(state):
        return "frames_processing"

    return _route_past_frames_processing(state)


def route_after_speech_structuring(state: RunState) -> Literal["frames_processing", "prepare_notes", "finalize", "review",
                                                               "critique_collection", "document_reshaping", "done",
                                                               "failed"]:
    """Phase 2 of an audio recording: its failure stops the run. A text-only PDF's figures are frames, read by the frame
    loop. When its ingesting profile declares preparation steps, phase 2 leaves
    phase 3 open and phase 3 prepares the notes; otherwise phase 3 is complete with nothing to read."""

    if _read_phase_status(state, "frames_preparing") != "complete":
        return "failed"
    if is_phase_selected(state, FRAMES_PROCESSING_PHASE_NUMBER) and _has_frames_left(state):
        return "frames_processing"
    if is_phase_selected(state, FRAMES_PROCESSING_PHASE_NUMBER) and _read_phase_status(state, "frames_processing") != "complete":
        return "prepare_notes"

    return _route_past_frames_processing(state)


def route_after_prepare_notes(state: RunState) -> Literal["finalize", "review", "critique_collection", "document_reshaping",
                                                          "done"]:
    """Phase 3 of an audio recording: a blocked or failed preparation ends the run, since phase 4 writes from it."""

    if _read_phase_status(state, "frames_processing") != "complete":
        return "done"

    return _route_past_frames_processing(state)


def route_after_frames_processing(state: RunState) -> Literal["frames_processing", "finalize", "review", "critique_collection",
                                                              "document_reshaping", "done"]:
    """The frame loop, guarded as `route_after_frames_preparing` is. A BLOCKED frame ends the run (the owner, 2026-09-28):
    every later phase is built on the frames' notes."""

    if state.get("blocked"):
        return "done"
    # A drain or a stop between batches ends the run as a block does: phase 4 never writes from part of the notes
    if state.get("is_drained"):
        return "done"
    if is_phase_selected(state, FRAMES_PROCESSING_PHASE_NUMBER) and _has_frames_left(state):
        return "frames_processing"

    return _route_past_frames_processing(state)


def route_after_finalize(state: RunState) -> Literal["review", "critique_collection", "document_reshaping", "done", "failed"]:
    """Phase 4's failure or block stops the run (the owner, 2026-09-29): the review works on the document it
    publishes, and a blocked step publishes none."""

    status = _read_phase_status(state, "finalize")
    if status == "failed":
        return "failed"
    if status == "blocked":
        return "done"

    return _route_past_the_document(state)
