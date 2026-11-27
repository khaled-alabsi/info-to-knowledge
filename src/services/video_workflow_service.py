"""Which video workflow a run takes, quality or fast-processing, kept in
`run-workflow.json` in the run folder with who chose it, so a resume, the page and the run's status read one answer.
No file: the settings' `video.workflow`. An audio recording has no video workflow: its path is the audio workflow's.
"""

from __future__ import annotations

from pathlib import Path

import config
from models import output_contract
from models import video_workflow_vocabulary
from utils import json_store_utils

__all__ = ["read_chosen_workflow", "write_chosen_workflow", "resolve_workflow", "is_fast_processing", "is_direct_draft",
           "has_no_critic", "describe_frame_grouping"]


def read_chosen_workflow(folder: Path) -> dict | None:
    """`{"workflow", "chosenBy"}` as the run folder keeps it, None when nobody chose one."""

    path = folder / output_contract.RUN_WORKFLOW_FILE

    return json_store_utils.read_json(path) if path.is_file() else None


def write_chosen_workflow(folder: Path,
                          workflow: str,
                          chosen_by: str) -> None:
    """The run's workflow, refused by name when it is none of the list."""

    if workflow not in video_workflow_vocabulary.WORKFLOWS:
        raise ValueError(f"no video workflow {workflow!r}: the workflows are {', '.join(video_workflow_vocabulary.WORKFLOWS)}")
    json_store_utils.write_json(folder / output_contract.RUN_WORKFLOW_FILE, {"workflow": workflow, "chosenBy": chosen_by})


def resolve_workflow(folder: Path) -> str:
    """The workflow the run takes: the one chosen for it, else `video.workflow`."""

    chosen = read_chosen_workflow(folder)

    return chosen["workflow"] if chosen else config.settings.video.workflow


def is_fast_processing(folder: Path) -> bool:
    """Whether the run takes the fast-processing workflow."""

    return resolve_workflow(folder) == video_workflow_vocabulary.FAST_PROCESSING


def is_direct_draft(folder: Path) -> bool:
    """Whether the run takes the direct-draft workflow (the Fast speed's)."""

    return resolve_workflow(folder) == video_workflow_vocabulary.DIRECT_DRAFT


def has_no_critic(folder: Path) -> bool:
    """Whether the run's workflow has no review and no critique collection."""

    return resolve_workflow(folder) in video_workflow_vocabulary.NO_CRITIC_WORKFLOWS


def describe_frame_grouping(folder: Path) -> str:
    """How phase 3 hands the run's frames to its model, for the console: a fast-processing run's sessions, a
    direct-draft run's batches of the draft writer, else the quality workflow's batches."""

    if is_direct_draft(folder):
        return f"batches of {config.settings.resolve_frames_per_call()}"
    if is_fast_processing(folder):
        return f"sessions of {config.settings.video.fast_processing.frame_follow_ups}"

    return f"batches of {config.settings.resolve_frames_per_call()}"
