"""Which workflow a run takes: the audio workflow for an audio recording, or for a video the person chose to process as
audio, else the video workflow. Pure file work.

The choice is kept in the run folder's `run-medium.json`, written before phase 1 by `run --as-audio` (the run form's
Process as), so a resume, the page and the run's status read the same answer. A takeaway run takes the audio workflow
whatever its recording. Otherwise the recording's own suffix decides, as `inbox_service.is_audio_recording` does for
the file itself."""

from __future__ import annotations

import json
from pathlib import Path

from models import output_contract
from services import inbox_service
from services import ingesting_profile_service
from utils import json_store_utils

__all__ = ["read_chosen_medium", "write_chosen_medium", "is_audio_run_recording"]


def _build_medium_path(folder: Path) -> Path:
    return folder / output_contract.RUN_MEDIUM_FILE


def read_chosen_medium(folder: Path) -> str | None:
    """The medium the person chose for the run folder; None when they chose none or the file cannot be read."""

    try:
        data = json.loads(_build_medium_path(folder).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    medium = data.get("medium") if isinstance(data, dict) and data.get("medium") in output_contract.RUN_MEDIUMS else None

    return output_contract.RETIRED_MEDIUMS.get(medium, medium) if medium else None


def write_chosen_medium(folder: Path,
                        medium: str) -> None:
    """The run folder's medium set to `medium`, the person's choice; a medium not in the list is refused by name."""

    if medium not in output_contract.RUN_MEDIUMS:
        raise ValueError(f"no medium {medium!r}: the media are " + ", ".join(output_contract.RUN_MEDIUMS))
    json_store_utils.write_json(_build_medium_path(folder), {"medium": medium})


def is_audio_run_recording(folder: Path,
                           recording: Path | None) -> bool:
    """Whether the run in `folder` takes the audio workflow: the person's choice when there is one; always for a
    one-call profile (`takeaway`), which reads the transcript alone; else whether the recording has no picture. A run
    whose record names no recording yet (`None`) is a video's."""

    chosen = read_chosen_medium(folder)
    if chosen is not None:
        return chosen in output_contract.FRAMELESS_MEDIUMS
    if ingesting_profile_service.is_one_call_profile(folder):
        return True

    return recording is not None and inbox_service.is_audio_recording(recording)
