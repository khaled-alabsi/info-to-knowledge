"""The program's short operations, one function each, for every front end: the CLI command prints
the result's lines and exits with its code, the web server returns them, the MCP server hands them back. Nothing here
starts `main.py`; each function checks its request, does the work through the services that own it, and says what it
did in the words the terminal always printed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

import config
from models import output_contract
from models.endpoint_errors import EndpointError
from models.input_range_spec import InputRangeSpec
from models.operation_result import OperationResult
from models.ui_errors import UnreadableRunTagsError
from models.run_errors import ModelUnavailable
from models.run_paths import RunPaths
from services import chapter_reset_service
from services import chat_model_service
from services import cut_tidy_service
from services import document_file_service
from services import endpoint_client
from services import frame_drop_service
from services import inbox_service
from services import job_queue_service
from services import input_range_service
from services import profile_convert_service
from services import profile_version_service
from services import run_branch_service
from services import recording_compression_service
from services import recording_medium_service
from services import recording_parts_service
from services import recording_hash_service
from services import run_recording_service
from services import run_reset_service
from services import run_resume_service
from services import run_status_service
from services import run_name_service
from services import run_tags_service
from utils import display_path_utils
from utils import document_name_utils

__all__ = ["RENAMED_WHEN_RUN_ENDS", "name_runs", "rename_after_run", "set_run_display_name", "set_run_tags", "reset_run_to_phase", "reset_run_from_chapter", "move_unused_cuts_aside", "move_recording_to_inbox",
           "keep_recording_in_run", "shrink_kept_recording", "start_compression_process", "describe_models",
           "probe_model_hosts", "find_compression_refusal", "list_or_drop_proposed_frames", "drop_run_frames",
           "copy_run_for_profile", "write_profile_in_place", "create_branch"]

# A rename's outcome for a folder a run writes into: shown under the new name now, renamed on disk when the run ends
RENAMED_WHEN_RUN_ENDS = "renamed when its run ends"
# How many of a host's models the probe lists
_PROBE_MODELS_SHOWN = 6
# A child process runs one operation by calling it, never through `main.py`: it outlives the request and the server
_CHILD_CODE = ("import sys; sys.path.insert(0, {src!r}); from pathlib import Path; "
               "from services import run_operations_service; "
               "sys.exit(run_operations_service.shrink_kept_recording(Path({folder!r}), print).exit_code)")


def _refuse_missing_folder(output_root: Path) -> OperationResult:
    """The one refusal of an operation given a run folder that is not there."""

    return OperationResult(2, [f"{display_path_utils.render_path(output_root)} is not a run folder"])


def _find_live_run_refusal(output_root: Path) -> OperationResult | None:
    """The refusal for a folder a run writes into, or None when none does."""

    live = run_reset_service.list_live_runs(output_root)
    if not live:
        return None

    return OperationResult(2, ["a run is writing into this folder; stop it first:", *[f"  {line}" for line in live]])


def _find_idle_folder_refusal(output_root: Path) -> OperationResult | None:
    """The refusal for a folder that is not there, or that a run writes into; None when an operation may change it."""

    if not output_root.is_dir():
        return _refuse_missing_folder(output_root)

    return _find_live_run_refusal(output_root)


def _build_paths(output_root: Path) -> RunPaths:
    return RunPaths(repository=output_root, module=output_root.name, output_root_override=output_root)


def _render_reset_plan(command: str,
                       output_root: Path,
                       planned: list[str]) -> OperationResult:
    return OperationResult(0, [f"{command} would remove from {display_path_utils.render_path(output_root)}:",
                               *[f"  {target}" for target in planned],
                               "nothing was removed; add --yes to remove them"])


def _render_reset_done(command: str,
                       output_root: Path,
                       removed: list[str]) -> OperationResult:
    return OperationResult(0, [f"{command}: removed from {display_path_utils.render_path(output_root)}:",
                               *[f"  {target}" for target in removed]])


def reset_run_to_phase(output_root: Path,
                       phase: int,
                       is_confirmed: bool) -> OperationResult:
    """Every phase after `phase` removed from the run folder, its files and its progress; listed only, unless
    `is_confirmed`."""

    last = max(output_contract.PHASE_FILES)
    if not 0 <= phase <= last:
        return OperationResult(2, [f"there is no phase {phase}: give 0 to {last}"])
    refusal = _find_idle_folder_refusal(output_root)
    if refusal:
        return refusal
    # Phases 1 and 2 read the recording, so they wait while a done run keeps it in its folder; a branch made to run
    # them again reads the recording kept there, as its run line does (DEFECTS 490)
    stored = run_recording_service.find_stored_recording(output_root)
    if phase < 2 and stored is not None and not run_branch_service.is_rereading_recording(output_root):
        return OperationResult(2, [
            f"phases 1 and 2 read the recording, kept in {display_path_utils.render_path(stored)}: move it back "
            f"to the inbox first (`i2k recording-to-inbox --output {display_path_utils.render_path(output_root)}`)"])
    paths = _build_paths(output_root)
    planned = run_reset_service.list_reset_targets(paths, phase)
    if not planned:
        return OperationResult(0, [f"nothing after phase {phase} in {display_path_utils.render_path(output_root)}"])
    if not is_confirmed:
        return _render_reset_plan(f"reset-to {phase}", output_root, planned)
    removed = run_reset_service.reset_to_phase(paths, phase)

    return _render_reset_done(f"reset-to {phase}", output_root, removed)


def reset_run_from_chapter(output_root: Path,
                           chapter: int,
                           is_confirmed: bool,
                           is_text_only: bool = False) -> OperationResult:
    """Chapter `chapter` and every later one removed from phase 4, so a resume writes them again; listed only, unless
    `is_confirmed`."""

    if chapter < 1:
        return OperationResult(2, [f"give a chapter from 1: {chapter} is none"])
    refusal = _find_idle_folder_refusal(output_root)
    if refusal:
        return refusal
    paths = _build_paths(output_root)
    try:
        planned = chapter_reset_service.list_chapter_reset_targets(paths, chapter, is_text_only)
    except ValueError as error:
        return OperationResult(2, [str(error)])
    if not planned:
        return OperationResult(0, [f"nothing of chapter {chapter} or later in "
                                   f"{display_path_utils.render_path(output_root)}"])
    if not is_confirmed:
        return _render_reset_plan(f"reset-chapter {chapter}", output_root, planned)
    removed = chapter_reset_service.reset_from_chapter(paths, chapter, is_text_only)

    return _render_reset_done(f"reset-chapter {chapter}", output_root, removed)


def _find_frame_change_refusal(output_root: Path) -> OperationResult | None:
    """The refusal for a folder whose frames cannot be changed now: missing, written by a run, or with no frame list."""

    refusal = _find_idle_folder_refusal(output_root)
    if refusal:
        return refusal
    if not (output_root / output_contract.LOOP_ITEMS_FILE).is_file():
        return OperationResult(2, [f"{display_path_utils.render_path(output_root)} has no "
                                   f"{output_contract.LOOP_ITEMS_FILE}: phase 2 has not run"])

    return None


def list_or_drop_proposed_frames(output_root: Path,
                                 is_confirmed: bool = False) -> OperationResult:
    """The frame knowledge classifier's decision on every frame phase 3 has not read, as lines and as `records`;
    nothing in the folder changes, unless `is_confirmed`: then every frame it proposes is dropped, the proposal
    accepted whole."""

    refusal = _find_frame_change_refusal(output_root)
    if refusal:
        return refusal
    try:
        proposal = frame_drop_service.find_frames_to_drop(_build_paths(output_root))
    except (FileNotFoundError, RuntimeError) as exc:
        return OperationResult(1, [str(exc)])
    proposed = [entry for entry in proposal if entry["decision"] == frame_drop_service.DECISION_DROP]
    lines = [f"refine-frames: {len(proposed)} of {len(proposal)} frame(s) phase 3 has not read hold no knowledge, "
             f"by the frame knowledge classifier:",
             *[f"  {entry['slug']}  drop score {entry['dropScore']:.2f}" for entry in proposed]]
    if not is_confirmed or not proposed:
        lines.append("nothing was dropped; add --yes to drop them all, or `i2k drop-frames <frames> --yes` for the "
                     "ones you choose")

        return OperationResult(0, lines, records=proposal)
    slugs = [entry["slug"] for entry in proposed]
    dropped = drop_run_frames(output_root, slugs, is_confirmed=True, proposed=slugs,
                              drop_scores={entry["slug"]: entry["dropScore"] for entry in proposal})

    return OperationResult(dropped.exit_code, [*lines, *dropped.lines], records=proposal)


def drop_run_frames(output_root: Path,
                    slugs: list[str],
                    is_confirmed: bool,
                    proposed: list[str] | None = None,
                    drop_scores: dict[str, float] | None = None) -> OperationResult:
    """The frames `slugs` dropped from the run before phase 3 reads them; listed only, unless `is_confirmed`.
    `proposed` and `drop_scores` are what a Refine proposed, when the frames come from one."""

    refusal = _find_frame_change_refusal(output_root)
    if refusal:
        return refusal
    # An accepted proposal may drop nothing: its mistakes are still saved
    if not slugs and not drop_scores:
        return OperationResult(2, ["name at least one frame to drop"])
    if not is_confirmed:
        return OperationResult(0, [f"drop-frames would drop from {display_path_utils.render_path(output_root)}:",
                                   *[f"  {slug}" for slug in slugs], "nothing was dropped; add --yes to drop them"])
    try:
        result = frame_drop_service.drop_frames(_build_paths(output_root), slugs, proposed or [], drop_scores or {})
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return OperationResult(2, [str(exc)])
    lines = [f"drop-frames: dropped {len(result.dropped)} frame(s) from {display_path_utils.render_path(output_root)} "
             f"into frames/dropped/; phase 3 will not read them:", *[f"  {slug}" for slug in result.dropped]]
    if result.false_drops or result.missed_drops:
        lines += [f"learning data: {len(result.false_drops)} FP and {len(result.missed_drops)} FN saved in "
                  f"{display_path_utils.render_path(result.corrections_dir)}",
                  *[f"  FP {slug} (proposed, kept)" for slug in result.false_drops],
                  *[f"  FN {slug} (dropped, not proposed)" for slug in result.missed_drops]]
    elif drop_scores:
        lines.append("learning data: the proposal was accepted as it stands; nothing to correct")

    return OperationResult(0, lines)


def move_unused_cuts_aside(output_root: Path,
                           is_confirmed: bool) -> OperationResult:
    """The cuts a run folder never placed, listed, or moved into its `.trash/cuts/` when confirmed. Nothing is deleted,
    so a cut moved by mistake is carried back by hand."""

    refusal = _find_idle_folder_refusal(output_root)
    if refusal:
        return refusal
    paths = _build_paths(output_root)
    planned = cut_tidy_service.list_unused_cuts(paths, document_file_service.read_image_store(paths))
    if not planned:
        return OperationResult(0, [f"every cut of {display_path_utils.render_path(output_root)} is placed in the "
                                   f"document"])
    trash = cut_tidy_service.TRASH_CUTS_DIR
    if not is_confirmed:
        return OperationResult(0, [f"tidy-cuts would move from {display_path_utils.render_path(output_root)}:",
                                   *[f"  {row['cut']} ({row['frame']}, {row['why']}): {row['file']}" for row in planned],
                                   f"nothing was moved; add --yes to move them to {trash}"])
    moved = cut_tidy_service.set_aside_unused_cuts(paths)

    return OperationResult(0, [f"tidy-cuts: moved {len(moved)} cut(s) to {trash} in "
                               f"{display_path_utils.render_path(output_root)}:",
                               *[f"  {row['cut']} -> {row['movedTo']}" for row in moved]])


def _rename_folder(output_root: Path,
                   new_name: str,
                   is_suffixed: bool) -> tuple[Path, str]:
    """`output_root` renamed to `new_name` beside it: `(where it is now, the outcome)`. A folder a run writes into keeps
    its name for now, since the run would go on writing at the old path, and is renamed when the run ends; a name another folder holds is never taken over: with
    `is_suffixed` the next free `-2`, `-3` is taken instead (a run and its branches share one AI name, and the run
    branched from could never be renamed once a branch was), else the folder keeps its name."""

    if new_name == output_root.name:
        return output_root, "renamed"
    if run_reset_service.list_live_runs(output_root):
        return output_root, RENAMED_WHEN_RUN_ENDS
    target = output_root.with_name(new_name)
    number = 2
    while is_suffixed and target.exists() and target != output_root:
        target = output_root.with_name(f"{new_name}-{number}")
        number += 1
    if target == output_root:
        return output_root, "renamed"
    if target.exists():
        return output_root, f"kept: {new_name} is taken"
    output_root.rename(target)
    # a queued resume of the run names the folder by its path: it follows the folder
    job_queue_service.move_jobs_to_folder(job_queue_service.QUEUE_DIR, output_root, target)

    return target, "renamed"


def name_runs(output_roots: list[Path],
              is_by_topic: bool) -> OperationResult:
    """Each run folder renamed to its AI name (the topic phase 4 wrote, as a folder name) and shown under the AI name as
    written when `is_by_topic`; else renamed back to the folder name it had before. An AI name another folder holds
    gets the next free `-2`, `-3`. A folder a run writes into is shown under the new name at once and renamed when the
    run ends (`rename_after_run`). A run with no topic yet and one whose file name is taken keep the name they have."""

    records = []
    for output_root in output_roots:
        topic = run_status_service.read_run_topic(output_root)
        if is_by_topic and not topic:
            records.append({"name": output_root.name, "display_name": run_name_service.read_display_name(output_root),
                            "outcome": "kept: no AI name yet"})
            continue
        former = run_name_service.read_former_name(output_root) or output_root.name
        new_name = document_name_utils.build_slug(topic) if is_by_topic else former
        folder, outcome = _rename_folder(output_root, new_name or output_root.name, is_by_topic)
        if outcome in ("renamed", RENAMED_WHEN_RUN_ENDS):
            shown = run_name_service.write_display_name(folder, topic if is_by_topic else "", former if is_by_topic else "")
        else:
            shown = run_name_service.read_display_name(folder)
        waiting = ("ai-name" if is_by_topic else "file-name") if outcome == RENAMED_WHEN_RUN_ENDS else ""
        run_name_service.write_waiting_rename(folder, waiting)
        records.append({"name": folder.name, "display_name": shown, "outcome": outcome})
    renamed = sum(record["outcome"] in ("renamed", RENAMED_WHEN_RUN_ENDS) for record in records)

    return OperationResult(0, [f"{renamed} of {len(records)} run(s) renamed to their "
                               f"{'AI name' if is_by_topic else 'file name'}",
                               *[f"  {record['name']}: {record['outcome']}" for record in records
                                 if record["outcome"] != "renamed"]], records=records)


def rename_after_run(output_root: Path) -> Path:
    """The rename asked while a run wrote into `output_root`, made now that none does; where the folder is after it.
    Nothing is done while a run still writes into it, or when no rename waits."""

    waiting = run_name_service.read_waiting_rename(output_root)
    if not waiting or not output_root.is_dir() or run_reset_service.list_live_runs(output_root):
        return output_root
    record = name_runs([output_root], waiting == "ai-name").records[0]

    return output_root.with_name(record["name"])


def set_run_display_name(output_root: Path,
                         name: str) -> OperationResult:
    """The run shown under `name`, the name the Manager's Rename also sets; an empty name shows it under its folder's
    name again. Nothing is renamed on disk, so a running run is no reason to refuse."""

    if not output_root.is_dir():
        return _refuse_missing_folder(output_root)
    shown = run_name_service.write_display_name(output_root, name, run_name_service.read_former_name(output_root))

    return OperationResult(0, [f"{display_path_utils.render_path(output_root)} is shown as {shown or output_root.name}"],
                           records=[{"display_name": shown}])


def set_run_tags(output_root: Path,
                 added: list[str],
                 removed: list[str],
                 replaced: list[str] | None = None) -> OperationResult:
    """The run folder's tags changed: set to `replaced` when given, else `added` put after the ones it has and `removed`
    taken out. A running run is no reason to refuse: a phase reads them only when it starts."""

    if not output_root.is_dir():
        return _refuse_missing_folder(output_root)
    try:
        tags = (run_tags_service.write_tags(output_root, replaced) if replaced is not None
                else run_tags_service.apply_tag_changes(output_root, added, removed))
    except UnreadableRunTagsError as error:
        return OperationResult(2, [str(error)])

    return OperationResult(0, [f"tags of {display_path_utils.render_path(output_root)}: {', '.join(tags) or '(none)'}"],
                           records=[{"tags": tags}])


def _find_format_tag(folder: Path) -> str:
    """The format tag of the file a run folder was made from: its run record's recording by its suffix, else the medium
    its frames file records (a meeting folder moved away has no suffix to read); "" when neither says."""

    record = run_status_service.load_newest_record(folder) or {}
    recording = Path(record["repository"]) / record["module"] if record.get("repository") and record.get("module") else None
    if recording is not None and (recording.exists() or recording.suffix):
        return run_tags_service.describe_format_tag(recording)
    try:
        medium = json.loads((folder / "02_frames.json").read_text(encoding="utf-8")).get("medium") or ""
    except (OSError, ValueError):
        return ""

    return run_tags_service.describe_medium_format_tag(str(medium) or output_contract.VIDEO_MEDIUM)


def apply_tag_rules(roots: list[Path]) -> OperationResult:
    """Every run folder under `roots` given the tags the naming rules (`tags.nameRules`) give its recording's name (the
    name it had before an AI rename, `run-name.json`, else its own) and the format tag of the file it was made from;
    and every run's tags written into its published
    document's metadata block. A folder whose tags file does not read is named and left as it is."""

    lines, tagged = [], 0
    for root in roots:
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            tags = [*run_tags_service.list_rule_tags(run_name_service.read_former_name(folder) or folder.name),
                    *filter(None, [_find_format_tag(folder)])]
            if not tags:
                # a run whose tags were given by hand still gets them written into its document's metadata block
                held_tags = run_tags_service.read_tags(folder)
                if held_tags:
                    run_tags_service.write_tags(folder, held_tags)
                continue
            try:
                held = run_tags_service.apply_tag_changes(folder, tags, [])
            except UnreadableRunTagsError as error:
                lines.append(str(error))
                continue
            tagged += 1
            lines.append(f"{display_path_utils.render_path(folder)}: {', '.join(held)}")

    return OperationResult(0, [*lines, f"{tagged} run folder(s) given their naming rules' and format tags"])


def copy_run_for_profile(output_root: Path,
                         profile: str) -> OperationResult:
    """A run converted to another ingesting profile: phases 1 to 3 copied into `<run>-<profile>/`, which phase 4 then
    writes again; for an audio recording whose two profiles prepare its notes in other steps, phases 1 and 2, and phase
    3 runs again. The run converted from is only read. `records` names the new folder, the
    recording its run reads and the phase it starts at, and the lines say how to start it."""

    if not output_root.is_dir():
        return _refuse_missing_folder(output_root)
    recording = run_recording_service.find_run_recording(output_root, run_status_service.load_newest_record(output_root))
    if recording is None:
        return OperationResult(2, [f"{display_path_utils.render_path(output_root)} names no recording that is still "
                                   f"there: phase 4 cuts its figures from it, so put it back first"])
    first_phase = profile_convert_service.select_first_phase(output_root, profile)
    try:
        target = profile_convert_service.copy_run_for_profile(output_root, profile)
    except ValueError as refusal:
        return OperationResult(2, [str(refusal)])
    line = (f"i2k run --video {display_path_utils.render_path(recording)} --output "
            f"{display_path_utils.render_path(target)} --from {first_phase} --force")

    return OperationResult(0, [f"phases 1 to {first_phase - 1} of {display_path_utils.render_path(output_root)} copied "
                               f"into {display_path_utils.render_path(target)}, ingesting profile {profile}",
                               f"phase {first_phase} writes it again with: {line}"],
                           records=[{"folder": str(target), "video": str(recording), "profile": profile,
                                     "fromPhase": first_phase,
                                     "purpose": profile_convert_service.describe_job(output_root, profile, first_phase)}])


def write_profile_in_place(output_root: Path,
                           profile: str) -> OperationResult:
    """A finished audio run's document written again in `profile` inside the same run folder:
    the document it has set aside whole as a version in `04_versions/`, and phase 4
    left to run again from the same notes. `records` names the folder, its recording and phase 4, as the conversion's
    do, and the lines say how to start it."""

    if not output_root.is_dir():
        return _refuse_missing_folder(output_root)
    recording = run_recording_service.find_run_recording(output_root, run_status_service.load_newest_record(output_root))
    if recording is None:
        return OperationResult(2, [f"{display_path_utils.render_path(output_root)} names no recording that is still "
                                   f"there: put it back first"])
    try:
        version = profile_version_service.set_document_aside(output_root, profile)
    except ValueError as refusal:
        return OperationResult(2, [str(refusal)])
    line = (f"i2k run --video {display_path_utils.render_path(recording)} --output "
            f"{display_path_utils.render_path(output_root)} --from 4 --force")
    purpose = profile_convert_service.describe_job(output_root, profile, 4).replace(
        f"convert to {profile}", f"write as {profile} in this run", 1)

    return OperationResult(0, [f"the document of {display_path_utils.render_path(output_root)} set aside in "
                               f"{version.relative_to(output_root).as_posix()}/, ingesting profile now {profile}",
                               f"phase 4 writes it again with: {line}"],
                           records=[{"folder": str(output_root), "video": str(recording), "profile": profile,
                                     "fromPhase": 4, "purpose": purpose}])


def _find_last_phase_reached(output_root: Path) -> int:
    """The last phase a run has started: the highest of its finished phase files and its record's phases not marked
    `not run`; 0 when it reached none."""

    started = run_resume_service.list_finished_phase_numbers(_build_paths(output_root))
    record = run_status_service.load_newest_record(output_root).get("phases", {})

    return max([*started, *(output_contract.PHASE_NUMBERS[name] for name, status in record.items()
                            if name in output_contract.PHASE_NUMBERS and status != "not run")], default=0)


def create_branch(output_root: Path,
                  phase: int,
                  pdf_mode: str = "",
                  input_range: InputRangeSpec | None = None) -> OperationResult:
    """A run branched from `phase`: copied whole into `<run>-from-<phase>/` and taken back to the
    phase before it there. The run branched from is only read; `records` names the new folder. `pdf_mode` (`pages-as-images`
    or `text-only`) sets a PDF run's processing mode in the branch, which phase 2 decides, so only a branch from it;
    `input_range` the part of the input it processes (an empty one: the whole input), read by
    phase 1, so only a branch from phase 1."""

    refusal = _find_idle_folder_refusal(output_root)
    if refusal:
        return refusal
    if pdf_mode and phase != 2:
        return OperationResult(2, [f"a processing mode is chosen by phase 2: branch from phase 2, not {phase}"])
    if input_range is not None and phase != 1:
        return OperationResult(2, [f"the part of the input is read by phase 1: branch from phase 1, not {phase}"])
    reached = _find_last_phase_reached(output_root)
    if phase > reached + 1:
        return OperationResult(2, [f"phase {phase} cannot be branched from: the run reached phase {reached}, so a branch "
                                   f"from phase {reached + 1} at most keeps all of it"])
    try:
        target = run_branch_service.create_branch(output_root, phase)
    except ValueError as refusal_text:
        return OperationResult(2, [str(refusal_text)])
    if pdf_mode in output_contract.PDF_MODE_MEDIUMS:
        recording_medium_service.write_chosen_medium(target, output_contract.PDF_MODE_MEDIUMS[pdf_mode])
    elif pdf_mode == output_contract.PDF_MODE_PAGES:
        (target / output_contract.RUN_MEDIUM_FILE).unlink(missing_ok=True)
    if input_range is not None and (input_range.is_pages or input_range.start_s is not None
                                    or input_range.end_s is not None):
        input_range_service.write_range(folder=target, input_range=input_range)
    elif input_range is not None:
        # the whole input again, after a run of a part
        (target / output_contract.RUN_INPUT_RANGE_FILE).unlink(missing_ok=True)

    # the recording the run reads, for the run line: the one kept in the branch, else the one the record names
    recording = run_recording_service.find_run_recording(target, run_status_service.load_newest_record(target))
    video = f" --video {display_path_utils.render_path(recording)}" if recording is not None else ""

    kept = f"phases 1 to {phase - 1} kept" if phase > 2 else "phase 1 kept" if phase == 2 else "its choices and recording kept"
    return OperationResult(0, [f"{display_path_utils.render_path(output_root)} branched from phase {phase} into "
                               f"{display_path_utils.render_path(target)}: {kept}",
                               f"phase {phase} runs there with: i2k run{video} --output "
                               f"{display_path_utils.render_path(target)} --resume"],
                           records=[{"folder": str(target), "name": target.name, "phase": phase,
                                     "video": str(recording) if recording is not None else ""}])


def move_recording_to_inbox(output_root: Path) -> OperationResult:
    """The recording a done run keeps in its folder, back in the inbox, so phases 1 and 2 can run again."""

    refusal = _find_live_run_refusal(output_root)
    if refusal:
        return refusal
    if run_recording_service.find_stored_recording(output_root) is None:
        return OperationResult(2, [f"{display_path_utils.render_path(output_root)} keeps no recording in "
                                   f"{run_recording_service.RECORDING_DIR}/"])
    video = run_recording_service.move_recording_to_inbox(config.INBOX_DIR, output_root)

    return OperationResult(0, [f"recording moved back to {display_path_utils.render_path(video)}; "
                               f"phases 1 and 2 can run again"])


def keep_recording_in_run(output_root: Path) -> OperationResult:
    """A done run's inbox recording kept in its folder, as the run does when it ends."""

    refusal = _find_live_run_refusal(output_root)
    if refusal:
        return refusal
    record = run_status_service.load_newest_record(output_root)
    video = run_status_service.describe_run_video(output_root)
    if not video or not run_recording_service.can_keep_recording(record, config.INBOX_DIR, Path(video)):
        return OperationResult(2, [f"{display_path_utils.render_path(output_root)} is not a done run whose recording is in the "
                                   f"inbox"])
    kept = run_recording_service.keep_recording_if_done(record, config.INBOX_DIR, output_root, Path(video))

    return OperationResult(0, [f"recording kept in {display_path_utils.render_path(kept)}; "
                               f"phases 1 and 2 wait until it is back in the inbox"])


def find_compression_refusal(output_root: Path) -> OperationResult | None:
    """Why the folder's recording cannot be compressed now, or None: a run writes into the folder, it keeps no
    recording, it is an audio recording (no picture to shrink), or a compression of it is running. The command and the
    page refuse by this one answer."""

    refusal = _find_live_run_refusal(output_root)
    if refusal:
        return refusal
    stored = run_recording_service.find_stored_recording(output_root)
    if stored is None:
        return OperationResult(2, [f"{display_path_utils.render_path(output_root)} keeps no recording in "
                                   f"{run_recording_service.RECORDING_DIR}/"])
    if inbox_service.is_audio_recording(stored):
        return OperationResult(2, [f"{stored.name} is an audio recording: it has no picture to shrink to 720p"])
    if recording_compression_service.is_compression_running(output_root):
        pid = recording_compression_service.read_compression(output_root).get("pid")
        return OperationResult(2, [f"the recording is already being compressed, by process {pid}"])

    return None


def shrink_kept_recording(output_root: Path,
                          report: Callable[[str], None] = lambda line: None) -> OperationResult:
    """Each video of the recording a done run keeps, re-encoded to 720p in place, with
    `recording/.compression.json` written after each. `report` hears each line as it happens, since a long meeting
    takes minutes."""

    # STEP 1: only a recording kept in an idle run folder, and not one already being compressed
    refusal = find_compression_refusal(output_root)
    if refusal is not None:
        return refusal
    recording = run_recording_service.find_stored_recording(output_root)
    earlier = recording_compression_service.read_compression(output_root)

    # STEP 2: the record, with the hash the recording has now, so it is still known once its bytes change
    record = {"status": recording_compression_service.COMPRESSION_RUNNING, "pid": os.getpid(), "height": recording_compression_service.TARGET_HEIGHT,
              "original_sha256": earlier.get("original_sha256")
              or recording_hash_service.compute_recording_hash(recording), "files": [], "error": ""}
    recording_compression_service.write_compression(output_root, record)

    # STEP 3: each video in turn, the record written after each, and its share done on the way, every percent
    lines: list[str] = []
    videos = recording_parts_service.list_recording_parts(recording)
    sizes = [video.stat().st_size for video in videos]
    total_bytes = sum(sizes) or 1
    for position, video in enumerate(videos):
        lines.append(f"compressing {video.name} ({sizes[position] / 1e9:.2f} GB) to "
                     f"{recording_compression_service.TARGET_HEIGHT}p …")
        report(lines[-1])
        done_bytes = sum(sizes[:position])

        def record_progress(share: float,
                            done_bytes: int = done_bytes,
                            size: int = sizes[position]) -> None:
            progress = round((done_bytes + share * size) / total_bytes, 2)
            if progress > record.get("progress", 0.0):
                record["progress"] = progress
                recording_compression_service.write_compression(output_root, record)

        try:
            entry = recording_compression_service.shrink_video(video, on_progress=record_progress)
        except RuntimeError as exc:
            record.update(status=recording_compression_service.COMPRESSION_FAILED, error=str(exc))
            recording_compression_service.write_compression(output_root, record)
            return OperationResult(1, [*lines, str(exc)])
        record["files"].append(entry)
        recording_compression_service.write_compression(output_root, record)
        lines.append(f"{video.name}: {entry['outcome']}, {entry['before_bytes'] / 1e9:.2f} GB → "
                     f"{entry['after_bytes'] / 1e9:.2f} GB")
        report(lines[-1])
    record["status"] = recording_compression_service.COMPRESSION_DONE
    recording_compression_service.write_compression(output_root, record)

    return OperationResult(0, lines)


def start_compression_process(output_root: Path) -> int:
    """`shrink_kept_recording` in a process of its own session, its lines in `recording/.compression.log`; the pid.
    Re-encoding a long meeting takes minutes, so the caller (the page, an agent) does not wait for it."""

    log_path = output_root / run_recording_service.RECORDING_DIR / recording_compression_service.COMPRESSION_LOG_FILE
    code = _CHILD_CODE.format(src=str(config.PLAYBOOK_ROOT / "src"), folder=str(output_root))
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-c", code], cwd=config.PLAYBOOK_ROOT, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                   env={**os.environ, "PYTHONUNBUFFERED": "1"})

    return process.pid


def describe_models() -> OperationResult:
    """Each phase that calls a model, the configured model it uses and the settings that will be sent with it, then
    every configured model by name."""

    try:
        rows = chat_model_service.describe_phase_models()
    except ModelUnavailable as exc:
        return OperationResult(2, [f"ERROR: {exc}"])
    phase_width = max(len(row["phase"]) for row in rows)
    name_width = max(len(row["name"]) for row in rows)
    lines = []
    for row in rows:
        source = "explicit" if row["explicit"] else "default"
        # The sampling beyond the temperature, printed only when set, so a model without it reads as before
        sampling = "  ".join(f"{key}={setting:g}" for key, setting in {"top_p": row["top_p"], **row["sampling"]}.items()
                             if setting is not None)
        lines.append(f"  {row['phase']:{phase_width}}  {row['name']:{name_width}}  {row['provider']}:{row['model']}"
                     f"   temp={row['temperature']}  num_ctx={row['num_ctx']}{'  ' + sampling if sampling else ''}"
                     f"  ({source})")
    lines.append("  configured models: " + ", ".join(f"{name} ({model.spec})"
                                                   for name, model in config.settings.models.items()))

    return OperationResult(0, lines)


def _probe_providers() -> tuple[list[str], int]:
    """One line for each kind of model that has a URL in `providers`: the models its server lists, or why it did not
    answer; and how many did not. A kind with no URL has no line."""

    lines = []
    down_count = 0
    for kind in type(config.settings.providers).model_fields:
        endpoint = endpoint_client.find_endpoint(kind=kind)
        if endpoint is None:
            continue
        try:
            names = endpoint_client.list_served_models(endpoint=endpoint)
        except EndpointError as failed:
            lines.append(f"  DOWN {kind}: {failed}")
            down_count += 1
            continue
        shown = ", ".join(names[:_PROBE_MODELS_SHOWN]) or "(no models listed)"
        lines.append(f"  OK   {endpoint.base_url}  [{kind}]  {shown}")

    return lines, down_count


def probe_model_hosts() -> OperationResult:
    """Whether each configured host answers, and what it has loaded if it does; 1 when any host is down."""

    provider_lines, provider_down_count = _probe_providers()
    try:
        results = chat_model_service.probe_hosts()
    except ModelUnavailable as exc:
        return OperationResult(2, [f"ERROR: {exc}"])
    if not results:
        return OperationResult(1 if provider_down_count else 0, ["no local model hosts are configured", *provider_lines])
    lines = []
    for row in results:
        if row["reachable"]:
            shown, hidden = row["models"][:_PROBE_MODELS_SHOWN], row["models"][_PROBE_MODELS_SHOWN:]
            more = f" and {len(hidden)} more" if hidden else ""
            lines.append(f"  OK   {row['host']}  [{row['provider']}]  {', '.join(shown) or '(no models loaded)'}{more}")
        else:
            lines.append(f"  DOWN {row['detail']}")

    lines.extend(provider_lines)
    is_any_host_down = provider_down_count > 0 or any(not row["reachable"] for row in results)

    return OperationResult(1 if is_any_host_down else 0, lines)
