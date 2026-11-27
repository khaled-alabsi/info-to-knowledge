"""A unit of work paused by the owner between its start and its end (DEFECTS 334): release test 1's fix call of frames
82 to 84 waited out a 54-minute pause and its console line read "58:16 (one frame takes 1165 s on average)". Made
data through the real path: a run context, the run's real `control.json` written to pause, the check the run makes
(`control_signal_service.check`, as `run_command` hands it to phase 3), and the unit's real console line; only the
clock is faked, so the test waits for nothing."""

import time as real_time

from support import harness

from cases.test_performance_log import _load_run_context
from services import control_signal_service
from services import performance_log_service


def test_a_paused_fix_call_s_console_line_shows_its_work_not_the_pause_defect():
    # The console line said 58:16 for a call of about 4 minutes; it now says the call's own time
    ctx, lines = _load_run_context(count=3)
    clock = {"now": 5000.0}

    class FakeTime:
        time = staticmethod(real_time.time)
        strftime = staticmethod(real_time.strftime)

        @staticmethod
        def monotonic():
            return clock["now"]

        # the pause's poll: no wait, the clock moved on and the owner's pause cleared
        sleep = staticmethod(lambda seconds: (clock.update(now=clock["now"] + 54 * 60.0),
                                              control_signal_service.clear(ctx.run_dir)))

    ctx.control_check = lambda: control_signal_service.check(ctx.run_dir, ctx.log)
    saved = (performance_log_service.time, control_signal_service.time)
    performance_log_service.time = control_signal_service.time = FakeTime
    try:
        handles = performance_log_service.start_unit(ctx, "frames_processing", "frames-000-002-second-pass",
                                                     label="fix from the critique of frames 0 → 2")
        control_signal_service.request(ctx.run_dir, control_signal_service.PAUSE, by="owner")
        ctx.control_check()
        clock["now"] += 4 * 60.0
        performance_log_service.record_unit(ctx, handles, "group", "accepted", ["frame-000", "frame-001", "frame-002"])
    finally:
        performance_log_service.time, control_signal_service.time = saved
    outcome = lines.lines[-1]
    harness.check("the console line gives the call's 4 minutes, not the 58 with the pause",
                  outcome.strip().startswith("4:00") and "58:" not in outcome, str(lines.lines))
