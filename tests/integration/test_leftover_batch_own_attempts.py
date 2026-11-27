"""Made data through phase 3's real loop, under one attempt a step (zero repair), in the shape 38c3-797 blocked on
(DEFECTS 541): a batch of frames 0 to 2 blocked on frame 1 and left 0 and 2 accepted with no follow-up; the resume asked
frame 1 with the new frame 3 as one call, frames 1 to 3, whose critique spent that name's one attempt; frame 2, part of
that call's range, was then finished under the same name, found its budget spent and blocked with no call. Now part of
a range is finished under its own frames' name, and every frame is written."""

from support import fakes
from support import fixtures as fx
from support import frame_batch_fixtures
from support import harness


def _frames(*records):
    return fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                             frames=list(records))


def test_frames_left_from_a_blocked_batch_get_follow_ups_of_their_own_defect():
    # The critique of frames left from a blocked batch was charged to a later call's name and blocked the run unasked
    record = frame_batch_fixtures.RECORD
    model = fakes.scripted(
        _frames({**record, "frame": 0}, {**frame_batch_fixtures.EMPTY_RECORD, "frame": 1}, {**record, "frame": 2}),
        _frames({**record, "frame": 1}, {**record, "frame": 3}), fakes.critique([]), fakes.nothing_open(),
        fakes.critique([]), fakes.nothing_open(), fakes.critique([]), fakes.nothing_open())
    ctx = frame_batch_fixtures.load_run_context(model, count=4)
    with fx.critique_switches(is_critique_on=True, is_critique_repair_on=False), fx.max_attempts(1):
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001", "frame-002"], frames_per_call=3,
                                                   repair_calls=0, single_frame_attempts=0)
        harness.check("the first batch blocked on frame 1 and left frames 0 and 2 accepted",
                      [ctx.store.get_item(f"frame-00{index}")["status"] for index in range(3)]
                      == ["written", "blocked", "written"], "")
        ctx.store.set_item_status("frame-001", "pending")
        frame_batch_fixtures.run_frames_processing(ctx, ["frame-001", "frame-003"], frames_per_call=3,
                                                   repair_calls=0, single_frame_attempts=0)
    statuses = [ctx.store.get_item(f"frame-00{index}")["status"] for index in range(4)]
    harness.check("every frame is written and phase 3 is complete",
                  statuses == ["written"] * 4 and ctx.store.get_phase("frames_processing") == "complete",
                  f"{statuses} {ctx.store.get_phase('frames_processing')}")
