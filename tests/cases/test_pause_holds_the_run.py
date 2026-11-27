"""A pause written into the run's `control.json` holds the run between frames and between attempts until it is cleared.

Phase 3 once read the signal and acted only on `drain`, so a pause from the web page or from a watcher was ignored and
the next batch started anyway.
"""

import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

from support import harness

from chains import chain_runner
from services import call_record_service
from services import control_signal_service
from services import run_activity_service

RUN_COMMAND = Path(__file__).resolve().parents[2] / "src" / "cli" / "run_command.py"


def test_a_pause_is_waited_out_and_the_next_signal_returned():
    run_dir = Path(tempfile.mkdtemp())
    control_signal_service.request(run_dir, control_signal_service.PAUSE)
    waits = []

    def _clear_on_wait(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            control_signal_service.clear(run_dir)

    previous_sleep = control_signal_service.time.sleep
    control_signal_service.time.sleep = _clear_on_wait
    try:
        signal = control_signal_service.check(run_dir)
    finally:
        control_signal_service.time.sleep = previous_sleep
    harness.check("the run waited while the pause stood, and went on once it was cleared",
                  len(waits) == 2 and signal is None, f"waits={waits} signal={signal}")


def test_the_run_s_control_check_is_the_one_that_waits_out_a_pause():
    source = RUN_COMMAND.read_text(encoding="utf-8")
    harness.check("run_command hands the nodes control_signal_service.check, never a bare read of the signal",
                  "control_check=lambda: control_signal_service.check(run_dir" in source
                  and "control_check=lambda: control_signal_service.read_signal" not in source)


def test_a_pause_is_never_seen_as_cleared_while_a_new_signal_is_being_written():
    """A reader that opens `control.json` between the truncate and the write of an in-place write sees nothing, and a
    run the operator paused would go on."""

    run_dir = Path(tempfile.mkdtemp())
    control_signal_service.request(run_dir, control_signal_service.PAUSE)
    seen_while_writing: list = []
    real_write_text = Path.write_text

    def write_text_with_a_reader(self, *arguments, **keywords):
        if self.name == control_signal_service.CONTROL_FILE:
            # What the file is for a reader between the truncate and the write of an in-place write
            real_write_text(self, "", encoding="utf-8")
            seen_while_writing.append(control_signal_service.read_signal(run_dir))
        return real_write_text(self, *arguments, **keywords)

    Path.write_text = write_text_with_a_reader
    try:
        control_signal_service.request(run_dir, control_signal_service.STOP)
    finally:
        Path.write_text = real_write_text
    harness.check("the control file is replaced whole, so no reader finds it empty and the pause lifted",
                  None not in seen_while_writing, str(seen_while_writing))
    harness.check("and the new signal stands", control_signal_service.read_signal(run_dir) == control_signal_service.STOP)


class _RecordingModel:
    """A model whose every call is recorded, with whether the run was paused when it came."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.calls_while_paused: list[bool] = []

    def invoke(self, messages: list) -> SimpleNamespace:
        self.calls_while_paused.append(control_signal_service.read_signal(self.run_dir) == control_signal_service.PAUSE)

        return SimpleNamespace(content="ok", response_metadata={}, additional_kwargs={}, usage_metadata=None)


def test_every_model_call_waits_out_a_pause_before_it_is_sent_defect():
    # A paused run went on making model calls wherever a phase had no check of its own (phase 3's follow-ups, phase 2's
    # filter, phase 6): the owner paused and the run kept calling. Now the one module every call passes waits it out.
    run_dir = Path(tempfile.mkdtemp())
    control_signal_service.request(run_dir, control_signal_service.PAUSE)
    model = _RecordingModel(run_dir)
    waits = []

    def _clear_on_wait(seconds: float) -> None:
        waits.append(model.calls_while_paused[:])
        control_signal_service.clear(run_dir)

    previous_sleep = control_signal_service.time.sleep
    control_signal_service.time.sleep = _clear_on_wait
    call_record_service.set_place(run_dir=run_dir, phase="frames_processing", slug="frames-000-003-open-questions")
    try:
        chain_runner.invoke_reply(model, [])
    finally:
        control_signal_service.time.sleep = previous_sleep
        call_record_service.clear_place()
    harness.check("the call waited while paused and was sent once the pause was cleared",
                  waits == [[]] and model.calls_while_paused == [False], f"waits={waits} calls={model.calls_while_paused}")


def test_a_model_call_records_that_the_run_waits_for_the_model_defect():
    # The page said only "Running phase 3" while the run read frames' text for minutes, and the owner took the run for
    # stuck. Now each model call records, before it is sent, that the run waits for the model's answer, for which step.
    run_dir = Path(tempfile.mkdtemp())
    run_activity_service.write_activity(run_dir, "preparing the request for frames 4 → 10, no model call yet")
    call_record_service.set_place(run_dir=run_dir, phase="frames_processing", slug="frames-004-010")
    try:
        chain_runner.invoke_reply(_RecordingModel(run_dir), [])
    finally:
        call_record_service.clear_place()
    activity = run_activity_service.read_activity(run_dir)
    harness.check("the run's step reads as waiting for the model, for that step",
                  activity is not None and activity["text"] == "waiting for the model's answer for frames-004-010",
                  str(activity))


def test_the_page_reads_paused_only_once_the_run_holds_defect():
    # The page said "Pause requested" while the run was already holding, and the owner took the pause for broken.
    # Now the run's status says held when its console's last line is the line a run writes as it starts holding.
    run_dir = Path(tempfile.mkdtemp())
    log = run_dir / "run.log"
    log.write_text("open questions of frames 0 → 3 …\n", encoding="utf-8")
    control_signal_service.request(run_dir, control_signal_service.PAUSE)
    harness.check("asked, a run still in its call is not held", not control_signal_service.is_run_holding(run_dir, log))
    logger = logging.getLogger("pause-test")
    handler = logging.FileHandler(log, encoding="utf-8")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    previous_sleep = control_signal_service.time.sleep
    seen: list[bool] = []

    def _look_then_clear(seconds: float) -> None:
        handler.flush()
        seen.append(control_signal_service.is_run_holding(run_dir, log))
        control_signal_service.clear(run_dir)

    control_signal_service.time.sleep = _look_then_clear
    try:
        control_signal_service.check(run_dir, logger)
    finally:
        control_signal_service.time.sleep = previous_sleep
        logger.removeHandler(handler)
        handler.close()
    harness.check("while it waits, the run reads as held", seen == [True], str(seen))
    harness.check("once the pause is cleared, it no longer does", not control_signal_service.is_run_holding(run_dir, log))
