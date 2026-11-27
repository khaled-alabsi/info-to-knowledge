"""Every branch of every routing function. Pure functions of state, so each is one line.

Five numbered phases. The graph routes on the queue AND on which phases the run was asked for, and
a failed phase that a later one is built on stops the run.
"""

from support import harness

from graph import routing
from models import output_contract

TRANSCRIBE, FRAMES_PREPARING, FRAMES_PROCESSING, FINALIZE = (
    output_contract.PHASE_NUMBERS[name] for name in ("transcribe", "frames_preparing", "frames_processing", "finalize"))


def _build_state(**changes) -> dict:
    state = {"phases": {"transcribe": "complete", "frames_preparing": "complete"},
             "queue": ["frame-000", "frame-001"], "cursor": 0, "is_drained": False,
             "first_phase": TRANSCRIBE, "last_phase": FINALIZE}
    state.update(changes)

    return state


def test_a_failed_transcription_stops_the_run():
    harness.check("a complete phase 1 goes on to the frames", routing.route_after_transcribe(_build_state()) == "frames_preparing")
    harness.check("a failed phase 1 stops the run",
                  routing.route_after_transcribe(_build_state(phases={"transcribe": "failed"})) == "failed")
    harness.check("a phase 1 that was never recorded stops the run",
                  routing.route_after_transcribe(_build_state(phases={})) == "failed")


def test_a_failed_frame_list_stops_the_run():
    harness.check("a failed phase 2 stops the run",
                  routing.route_after_frames_preparing(_build_state(phases={"frames_preparing": "failed"})) == "failed")


def test_a_queue_with_frames_enters_the_loop():
    harness.check("frames and a selected frame phase enter the loop",
                  routing.route_after_frames_preparing(_build_state()) == "frames_processing")
    harness.check("the loop continues while the cursor is inside the queue",
                  routing.route_after_frames_processing(_build_state(cursor=1)) == "frames_processing")


def test_both_edges_into_the_loop_agree_on_an_empty_queue():
    empty = _build_state(queue=[])
    harness.check("an empty queue skips the loop on the way in", routing.route_after_frames_preparing(empty) == "finalize")
    harness.check("and on the way round", routing.route_after_frames_processing(empty) == "finalize")


def test_a_spent_queue_goes_on_to_the_document_file_and_a_drained_one_ends_the_run():
    """A drain (or a stop) between batches once sent phase 4 to write the document from half the meeting's notes."""

    harness.check("a cursor past the queue goes on to phase 4", routing.route_after_frames_processing(_build_state(cursor=2)) == "finalize")
    harness.check("a drained run with frames still pending ends: every later phase is built on the frames' notes",
                  routing.route_after_frames_processing(_build_state(is_drained=True)) == "done")


def test_a_run_told_to_stop_early_leaves_the_graph():
    harness.check("`--to 2` leaves after the frame list",
                  routing.route_after_frames_preparing(_build_state(last_phase=FRAMES_PREPARING)) == "done")
    harness.check("`--phase 3` leaves after the loop",
                  routing.route_after_frames_processing(_build_state(cursor=2, first_phase=FRAMES_PROCESSING, last_phase=FRAMES_PROCESSING)) == "done")


def test_a_run_that_starts_after_the_loop_never_enters_it():
    only_finalize = _build_state(first_phase=FINALIZE)
    harness.check("`--phase 4` goes straight to the facts file",
                  routing.route_after_frames_preparing(only_finalize) == "finalize")
    harness.check("and the loop-back edge agrees", routing.route_after_frames_processing(only_finalize) == "finalize")


def test_a_blocked_frame_stops_the_run_instead_of_writing_a_document_with_a_hole():
    """The owner, 2026-09-28: "the run should stop on blocking, not continue to next step". Phases 4, 5 and 6 are all
    built on the frames' notes, so one frame without notes ends the run; the cause is fixed and the run resumed."""

    harness.check("a blocked frame ends the run, with frames still in the queue",
                  routing.route_after_frames_processing(_build_state(blocked=["frame-004"])) == "done")
    harness.check("even once the queue is walked, phase 4 does not run",
                  routing.route_after_frames_processing(_build_state(cursor=2, blocked=["frame-004"])) == "done")
    harness.check("and a drained loop with a blocked frame stops too",
                  routing.route_after_frames_processing(_build_state(is_drained=True, blocked=["frame-004"])) == "done")
    harness.check("with nothing blocked it goes on as before",
                  routing.route_after_frames_processing(_build_state(cursor=2, blocked=[])) == "finalize")
