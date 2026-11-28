"""A video run started again on another workflow than its phase 3 was made under: the Fast speed after Quality, a speed
changed on the page while the run worked, and Balanced after Quality. Made data: an invented recording in a made inbox
and a run folder whose phases were made by hand, run through the real run command with a resume; the run stops where
its phases would begin, and the test reads the folder the phases would read."""

import argparse
import json
import logging
import tempfile
from pathlib import Path

from support import harness

import config
from cli import run_command
from models import output_contract
from models import video_workflow_vocabulary
from services import model_capability_service
from services import run_speed_service
from services import transcription_service
from services import video_workflow_service


class _WarningList(logging.Handler):
    """The warnings the run command logs, kept for the test to read."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.message_list: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.message_list.append(record.getMessage())


def _write(path: Path,
           text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_run_folder(phase_3_files: tuple[str, ...]) -> tuple[Path, Path]:
    """An invented recording, and its run folder whose phases 1 and 2 are done and whose phase 3 wrote
    `phase_3_files`."""

    root = Path(tempfile.mkdtemp())
    video = root / "inbox" / "Gateway-weekly.mp4"
    _write(video, "<an invented recording>")
    folder = root / "output" / "Gateway-weekly"
    _write(folder / output_contract.TRANSCRIPT_FILE, json.dumps({"segments": [{"start": 0.0, "end": 2.0,
                                                                                 "text": "Hello."}]}))
    _write(folder / "frames/frame-000.jpg")
    _write(folder / output_contract.LOOP_ITEMS_FILE, json.dumps({"schemaVersion": 1, "items": [
        {"slug": "frame-000", "index": 0, "timestamp": 1.0, "path": "frames/frame-000.jpg", "builtFrom": []}]}))
    for name in phase_3_files:
        _write(folder / name)

    return video, folder


def _start(video: Path,
           folder: Path,
           speed_preset: str) -> tuple[int, list[str], dict]:
    """The run command's resume of `folder`, stopped where its phases would begin; its exit code, its warnings, and
    what the phases would have found in the folder."""

    logger = logging.getLogger(f"workflow-switch-{folder.parent.parent.name}")
    warnings = _WarningList()
    logger.addHandler(warnings)
    args = argparse.Namespace(target_file=video, output_root=folder, only_phase=None, from_phase=None, to_phase=None,
                              worker_indexes=[], is_resuming=True, is_dry_run=False, is_forced=True, is_queued=False,
                              profile_name="", tags=[], ingesting_profile="", is_run_as_audio=False, phase_models=[],
                              workflow="", speed_preset=speed_preset, speed_settings=[], logger=logger)
    found: dict = {}

    def record_folder(ctx, selection, checks, recording, is_audio) -> int:
        found.update(workflow=video_workflow_service.resolve_workflow(folder),
                     has_notes=(folder / output_contract.NOTES_FILE).exists(),
                     has_items=(folder / output_contract.ITEMS_DIR_NAME).exists(),
                     has_draft=(folder / output_contract.DRAFT_FILE).exists(),
                     has_frames=(folder / output_contract.LOOP_ITEMS_FILE).exists())
        return 0

    saved = (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities,
             run_command._run_selected_phases, config.settings)
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    model_capability_service.check_phase_capabilities = lambda: []
    run_command._run_selected_phases = record_folder
    config.settings = config.settings.model_copy(deep=True)
    try:
        code = run_command.cmd_run(args)
    finally:
        (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities,
         run_command._run_selected_phases, config.settings) = saved
        logger.removeHandler(warnings)

    return code, warnings.message_list, found


def test_a_quality_run_resumed_on_the_fast_speed_runs_phase_3_again_under_fast_defect():
    # A run whose phase 3 was made under Quality, resumed on the Fast speed, went on with no draft and stopped in
    # phase 4 on "03_draft.md is missing"; now the start warns and puts the run back to phase 2
    video, folder = _build_run_folder(("03_processed-frames/frame-000.json", output_contract.NOTES_FILE,
                                       "04_writing/parts.json"))
    video_workflow_service.write_chosen_workflow(folder, video_workflow_vocabulary.QUALITY, "workflow speed")
    code, warnings, found = _start(video, folder, "fast")
    harness.check("the run starts", code == 0, str(code))
    harness.check("it warns that the workflow changed and phase 3 runs again",
                  any("from quality to direct draft" in line for line in warnings), str(warnings))
    harness.check("the phases find the Fast workflow, no quality notes and the frames of phase 2",
                  found == {"workflow": video_workflow_vocabulary.DIRECT_DRAFT, "has_notes": False, "has_items": False,
                            "has_draft": False, "has_frames": True}, str(found))


def test_a_speed_changed_on_the_page_while_the_run_worked_takes_its_workflow_at_the_next_start_defect():
    # The page's chip changed a working Fast run to Balanced: only the speed was kept, so the next start read the old
    # workflow file and went on with Fast; now the start takes Balanced's workflow and runs phase 3 again
    video, folder = _build_run_folder((output_contract.DRAFT_FILE,))
    run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed("fast", {}))
    run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed("balanced", {}),
                                         is_workflow_kept=False)
    code, warnings, found = _start(video, folder, "")
    harness.check("the run starts", code == 0, str(code))
    harness.check("it warns that the workflow changed", any("from direct draft to quality" in line for line in warnings),
                  str(warnings))
    harness.check("the phases find the quality workflow and no Fast draft",
                  found.get("workflow") == video_workflow_vocabulary.QUALITY and not found.get("has_draft"), str(found))


def test_balanced_after_quality_keeps_phase_3():
    video, folder = _build_run_folder(("03_processed-frames/frame-000.json", output_contract.NOTES_FILE))
    run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed("quality", {}))
    code, warnings, found = _start(video, folder, "balanced")
    harness.check("the run starts with no warning about the workflow", code == 0
                  and not any("workflow changed" in line for line in warnings), f"{code} {warnings}")
    harness.check("phase 3's notes stay", found.get("has_notes") and found.get("has_items"), str(found))
