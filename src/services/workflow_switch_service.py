"""A video run started again on another workflow than the one its phase 3 was made under (the Fast speed after Quality
or Balanced, or back): phases 1 and 2 are the same in every workflow, and phase 3 on, each workflow writes its own
files, so the run is put back to phase 2 and runs phase 3 on again under the new workflow."""

from __future__ import annotations

from pathlib import Path

from models import output_contract
from models import video_workflow_vocabulary
from models.run_paths import RunPaths
from services import run_reset_service
from services import run_speed_service
from services import video_workflow_service

__all__ = ["find_made_workflow", "reset_for_workflow", "keep_next_workflow"]

# The phase every workflow shares last: a switch puts the run back to it
_LAST_SHARED_PHASE = 2


def find_made_workflow(folder: Path) -> str:
    """The workflow the run's phase 3 was made under, read from what it wrote; "" when phase 3 wrote nothing yet."""

    if (folder / output_contract.DRAFT_FILE).is_file():
        return video_workflow_vocabulary.DIRECT_DRAFT
    if (folder / output_contract.FRAME_DESCRIPTIONS_FILE).is_file():
        return video_workflow_vocabulary.FAST_PROCESSING
    items_dir = folder / output_contract.ITEMS_DIR_NAME
    has_items = items_dir.is_dir() and any(items_dir.iterdir())
    if has_items or (folder / output_contract.NOTES_FILE).is_file():
        return video_workflow_vocabulary.QUALITY

    return ""


def reset_for_workflow(paths: RunPaths,
                       next_workflow: str,
                       is_dry_run: bool) -> str:
    """Put the run back to phase 2 when its phase 3 was made under another workflow than `next_workflow`; the warning
    the console prints, "" when the workflow is the same or phase 3 has not begun. A dry run removes nothing."""

    made_workflow = find_made_workflow(paths.output_root)
    if not made_workflow or made_workflow == next_workflow:
        return ""
    made_title = video_workflow_vocabulary.WORKFLOW_TITLES[made_workflow]
    next_title = video_workflow_vocabulary.WORKFLOW_TITLES.get(next_workflow, next_workflow)
    if is_dry_run:
        return (f"the workflow changes from {made_title} to {next_title}: a real start removes phase 3 and every phase "
                f"after it and runs them again on {next_title}; phases 1 and 2 stay")
    run_reset_service.reset_to_phase(paths, _LAST_SHARED_PHASE)

    return (f"the workflow changed from {made_title} to {next_title}: phase 3 and every phase after it were made for "
            f"{made_title}, so they are removed and run again on {next_title}; phases 1 and 2 stay")


def keep_next_workflow(folder: Path,
                       next_workflow: str) -> None:
    """Write `next_workflow` as the run's workflow when the folder holds another, so the phases read the one the run
    started on: a speed changed while the run worked kept only the speed, never its workflow."""

    if next_workflow != video_workflow_service.resolve_workflow(folder):
        video_workflow_service.write_chosen_workflow(folder, next_workflow, run_speed_service.CHOSEN_BY_SPEED)
