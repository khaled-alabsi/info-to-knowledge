"""A resume queued under a run folder's old name, after the folder was renamed to its AI name, was refused "no run to
resume", and still left a folder of the old name holding its speed and workflow files: the page listed that empty
folder as an unfinished run beside the renamed one (DEFECTS 718, following DEFECTS 708). Made data: an invented
recording in a made inbox, run through the real run command with a Workflow speed, nothing at the folder it names."""

import argparse
import logging
import tempfile
from pathlib import Path

from support import harness

from cli import run_command
from services import model_capability_service
from services import transcription_service


def _build_resume_args(video: Path,
                       run_folder: Path,
                       logger: logging.Logger) -> argparse.Namespace:
    return argparse.Namespace(target_file=video, output_root=run_folder, only_phase=None, from_phase=None, to_phase=None,
                              worker_indexes=[], is_resuming=True, is_dry_run=False, is_forced=True, is_queued=False,
                              profile_name="", tags=[], ingesting_profile="", is_run_as_audio=False, phase_models=[],
                              workflow="", speed_preset="balanced", speed_settings=[], logger=logger)


def test_a_refused_resume_with_a_speed_leaves_no_folder_of_its_name_defect():
    # A refused run wrote its speed before the refusal, making the folder; now nothing is written until every check
    # passed, so the old name stays unmade
    root = Path(tempfile.mkdtemp())
    inbox = root / "inbox"
    inbox.mkdir()
    video = inbox / "Gateway-weekly.mp4"
    video.write_bytes(b"<an invented recording>")
    old_name = root / "output" / "Gateway-weekly"
    logger = logging.getLogger("refused-resume")
    saved = (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities)
    transcription_service.describe_available_engine = lambda settings: "audio: test engine"
    model_capability_service.check_phase_capabilities = lambda: []
    try:
        code = run_command.cmd_run(_build_resume_args(video, old_name, logger))
    finally:
        (transcription_service.describe_available_engine, model_capability_service.check_phase_capabilities) = saved
    harness.check("the resume is refused", code == 2, str(code))
    harness.check("and no folder of the old name is made", not old_name.exists(),
                  str(sorted(path.name for path in old_name.rglob("*"))) if old_name.exists() else "")
