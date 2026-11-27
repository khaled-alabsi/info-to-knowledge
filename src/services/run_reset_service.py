"""`i2k reset-to N`: a run folder put back to how it stood when phase N had just finished, as if no later phase had
ever run. Every file a later phase wrote is removed, and every progress record of a later phase in the run stores
(`logs/<run id>/run.json`) is forgotten: its phase status, its attempts, and for phase 3 the frames' statuses.

What stays: the phases up to N and the logs, which are the history of what ran and never the state a resume
reads. Phase 5 edits the published document in place, so a reset to 4 puts back the copy phase 5
kept of it (`05_review/before.md`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from models import output_contract
from models.phase_outputs_spec import PhaseOutputsSpec
from models.run_paths import RunPaths
from services import document_pass_service
from services import document_folder_service
from services import frame_records_service
from services import performance_log_service
from services import worker_feedback_service
from utils import json_store_utils

__all__ = ["PHASE_OUTPUTS", "list_run_processes", "list_live_runs", "list_published_folders", "list_reset_targets",
           "reset_to_phase", "list_finalize_calls"]

_REVIEW_BEFORE_FILE = "before.md"
# What a phase writes that is no file of its own: the notes inside the frames file, the published folder, the reviewed
# copy of the published document
_FRAME_NOTES_TARGET = "frame notes"
_PUBLISHED_FOLDER_TARGET = "published folder"
_REVIEWED_DOCUMENT_TARGET = "reviewed document"
_CLEANED_DOCUMENT_TARGET = "cleaned document"
# The commands that drive phases: `run` on one recording, `inbox` on every new recording in turn.
_RUN_COMMANDS = ("run", "inbox")


# Phase number -> everything it writes.
PHASE_OUTPUTS: dict[int, PhaseOutputsSpec] = {
    1: PhaseOutputsSpec(files=(output_contract.AUDIO_FILE, output_contract.TRANSCRIPT_FILE)),
    # An audio recording's phase 2 writes the speech paragraphs from one call, and its phase 3 the prepared notes:
    # without them here, a reset to 3 took phase 2's call log as phase 4's
    2: PhaseOutputsSpec(files=(output_contract.LOOP_ITEMS_FILE, output_contract.SPEECH_FILE),
                    dirs=(output_contract.FRAME_IMAGES_DIR_NAME,), call_prefixes=(output_contract.SPEECH_SLUG,)),
    3: PhaseOutputsSpec(files=(output_contract.BATCH_SUMMARIES_FILE, output_contract.NOTES_FILE),
                    dirs=(output_contract.ITEMS_DIR_NAME, output_contract.PREPARED_DIR),
                    call_prefixes=("frame-", "frames-", f"{output_contract.PREPARED_CALL_SLUG_PREFIX}-"),
                    special_targets=(_FRAME_NOTES_TARGET,)),
    4: PhaseOutputsSpec(files=(output_contract.SUMMARY_FILE, output_contract.DRAFT_PROVENANCE_FILE),
                    dirs=(output_contract.WRITING_DIR_NAME,), call_prefixes=("",),
                    special_targets=(_PUBLISHED_FOLDER_TARGET,)),
    5: PhaseOutputsSpec(dirs=(output_contract.REVIEW_DIR_NAME,), call_prefixes=("review-",), special_targets=(_REVIEWED_DOCUMENT_TARGET,)),
    # A run made before phase 6 was renamed keeps its rule set and its call log under the retired names
    6: PhaseOutputsSpec(dirs=(output_contract.CRITIQUE_COLLECTION_DIR_NAME, output_contract.RETIRED_CRITIQUE_COLLECTION_DIR_NAME),
                        call_prefixes=(output_contract.CRITIQUE_COLLECTION_SLUG,
                                       output_contract.RETIRED_CRITIQUE_COLLECTION_SLUG)),
    # and phase 7's folder under its name from before it became document reshaping
    7: PhaseOutputsSpec(dirs=(output_contract.DOCUMENT_RESHAPING_DIR_NAME, output_contract.RETIRED_DOCUMENT_RESHAPING_DIR_NAME),
                        special_targets=(_CLEANED_DOCUMENT_TARGET,)),
}
# The call logs of phases 2, 3, 5 and 6 carry their own prefixes; every other call log is phase 4's.
_OWNED_PREFIXES = tuple(prefix for outputs in PHASE_OUTPUTS.values() for prefix in outputs.call_prefixes if prefix)


def _list_calls(output_root: Path,
                prefix: str) -> list[Path]:
    calls = output_root / output_contract.CALLS_DIR_NAME
    if not calls.is_dir():
        return []
    logs = sorted(calls.glob("*.json"))
    if prefix:
        return [log for log in logs if log.name.startswith(prefix)]

    return [log for log in logs if not log.name.startswith(_OWNED_PREFIXES)]


def list_finalize_calls(output_root: Path) -> list[Path]:
    """Phase 4's call logs: every call log no other phase's prefix names."""

    return _list_calls(output_root, "")


def _find_call_phase(slug: str) -> int:
    """The phase a call slug belongs to, by the same prefixes its call log is filed under: phase 4 owns every other."""

    return next((number for number, outputs in PHASE_OUTPUTS.items()
                 for prefix in outputs.call_prefixes if prefix and slug.startswith(prefix)),
                output_contract.PHASE_NUMBERS["finalize"])


def _count_later_reports(output_root: Path,
                         later: list[int]) -> int:
    return sum(1 for report in worker_feedback_service.read_reports(output_root / output_contract.WORKER_FEEDBACK_FILE)
               if _find_call_phase(report.where) in later)


def _is_published_put_back(later: list[int]) -> bool:
    """Whether the reset puts the published document back as phase 4 left it: phase 5 is taken back, phase 4 kept."""

    return output_contract.PHASE_NUMBERS["review"] in later and output_contract.PHASE_NUMBERS["finalize"] not in later


def _describe_special_target(output_root: Path,
                             name: str,
                             is_published_put_back: bool) -> list[str]:
    frames_file = output_root / output_contract.LOOP_ITEMS_FILE
    if name == _FRAME_NOTES_TARGET and frames_file.is_file():
        return [f"the notes of every frame inside {output_contract.LOOP_ITEMS_FILE}"]
    if name == _PUBLISHED_FOLDER_TARGET:
        return [f"{folder.name}/" for folder in list_published_folders(output_root)]
    # Put back only from the copy phase 5 kept, as `_restore_published` does
    has_review_copy = (output_root / output_contract.REVIEW_DIR_NAME / _REVIEW_BEFORE_FILE).is_file()
    if name == _REVIEWED_DOCUMENT_TARGET and is_published_put_back and has_review_copy:
        return [f"{folder.name}/{folder.name}.md put back as phase 4 published it"
                for folder in list_published_folders(output_root)]
    if name == _CLEANED_DOCUMENT_TARGET and document_pass_service.find_first_before(output_root) is not None:
        return [f"{folder.name}/{folder.name}.md put back as it was before phase 7"
                for folder in list_published_folders(output_root)]

    return []


def _list_run_stores(output_root: Path) -> list[Path]:
    return sorted((output_root / output_contract.RUNS_DIR_NAME).glob(f"*/{output_contract.RUN_STATE_FILE}"))


def _remove_later_progress(store_path: Path,
                           phase: int) -> None:
    """One run store with every record of a phase after `phase` removed: statuses, attempts, frame statuses. A finished
    run reads as one that stopped short, since its later phases are gone."""

    data = json_store_utils.read_json(store_path)
    later = {name for name, number in output_contract.PHASE_NUMBERS.items() if number > phase}
    # A record written before the rename names a phase `work` or `enumerate`, read as its name now
    retired = output_contract.RETIRED_STEP_NAMES
    data["phases"] = {name: status for name, status in (data.get("phases") or {}).items()
                      if retired.get(name, name) not in later}
    data["attempts"] = {key: attempts for key, attempts in (data.get("attempts") or {}).items()
                        if retired.get(key.split("/", 1)[0], key.split("/", 1)[0]) not in later}
    if phase < output_contract.PHASE_NUMBERS["frames_preparing"]:
        data["items"] = {}
    elif phase < output_contract.PHASE_NUMBERS["frames_processing"]:
        data["items"] = {slug: {**{key: value for key, value in item.items()
                                   if key not in output_contract.PHASE_3_ITEM_FIELDS}, "status": "pending", "attempts": 0}
                         for slug, item in (data.get("items") or {}).items()}
    if later and data.get("status") == "complete":
        data["status"] = "incomplete"
        data.pop("endedAt", None)
    json_store_utils.write_json(store_path, data)


def _restore_published(output_root: Path) -> None:
    """Phase 5's edits undone: each published document back to the copy phase 5 kept, its report beside it gone."""

    before = output_root / output_contract.REVIEW_DIR_NAME / _REVIEW_BEFORE_FILE
    for folder in list_published_folders(output_root):
        document_folder_service.restore_removed_figures(output_root, folder)
        if before.is_file():
            (folder / f"{folder.name}.md").write_text(before.read_text(encoding="utf-8"), encoding="utf-8")
        (folder / output_contract.REVIEW_REPORT_FILE).unlink(missing_ok=True)
        provenance_path = folder / output_contract.DOCUMENT_PROVENANCE_FILE
        if provenance_path.is_file():
            provenance = json_store_utils.read_json(provenance_path)
            provenance.pop("review", None)
            json_store_utils.write_json(provenance_path, provenance)


# This playbook's own entry point: another project's `main.py run` is none of its runs
_OWN_MAIN = Path(__file__).resolve().parents[2] / "main.py"


def set_own_main(path: Path) -> Path:
    """Swap the `main.py` counted as this playbook's (a test's stand-in run); returns the one it replaced."""

    global _OWN_MAIN
    previous, _OWN_MAIN = _OWN_MAIN, Path(path).resolve()

    return previous


def _is_own_main(pid: str,
                 main: str) -> bool:
    """Whether the `main.py` a process runs is this playbook's, a relative one read against the process's folder: the
    queue held its retry while another project's `main.py run` went on (DEFECTS 703)."""

    path = Path(main)
    if not path.is_absolute():
        listing = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True,
                                 check=False).stdout
        folder = next((line[1:] for line in listing.splitlines() if line.startswith("n")), "")
        if not folder:
            return True
        path = Path(folder) / path
    try:
        return path.resolve() == _OWN_MAIN
    except OSError:
        return False


def list_run_processes() -> list[str]:
    """Every process running this playbook's phases on this machine now, as `ps` prints it, whatever folder it
    writes: a `run` and an `inbox` alike."""

    listing = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True, text=True, check=False).stdout

    live = []
    for line in listing.splitlines():
        # A run is a Python process whose `main.py` is followed by one of those commands; a shell whose command only
        # holds those words is none (the operator's own shell was once listed, and a reset was refused)
        fields = line.split()
        is_python = len(fields) > 1 and "python" in Path(fields[1]).name.casefold()
        is_run = any(field.endswith("main.py") and following in _RUN_COMMANDS and _is_own_main(fields[0], field)
                     for field, following in zip(fields, fields[1:]))
        if is_python and is_run:
            live.append(line.strip())

    return live


def list_live_runs(output_root: Path,
                   processes: list[str] | None = None) -> list[str]:
    """The processes running this playbook on `output_root` now, as `ps` prints them: a reset under a live run would
    delete what it is writing. `processes` is `list_run_processes()` read once by a caller asking about many folders:
    one `ps` per folder took 2 of the run list's 2.4 s over 32 folders."""

    live = []
    recorded = _read_recorded_pid(output_root)
    for line in list_run_processes() if processes is None else processes:
        pid = int(line.split()[0])
        # The pid the folder's record names (an `inbox` run names no folder on its command line), or a command line
        # whose `--output` names this folder
        if pid == recorded or _is_output_named(line, output_root):
            live.append(line)

    return live


def _is_output_named(line: str,
                     output_root: Path) -> bool:
    """Whether the command line's `--output` names this folder, with the path's own spaces kept: a recording whose name
    holds a space (every meeting recording does) wrote a folder no match up to the first space ever found, so a reset under a live run
    was allowed, and the page showed no phase running."""

    found = re.search(r"--output[ =](.+)$", line)
    if not found:
        return False
    words = found.group(1).split()
    # The value ends at the next flag: joined with a long run line's flags, a word list past the system's name limit
    # raised "File name too long" for every folder asked about while that run lived (DEFECTS 712)
    flag = next((at for at, word in enumerate(words) if at and word.startswith("--")), len(words))
    words = words[:flag]
    candidates = [Path(" ".join(words[:count])) for count in range(1, len(words) + 1)]
    # The longest words that name an existing folder are the value: every prefix tried, a run of `Weekly sync` named
    # `Weekly` too. With none that exists, the folder's name is matched, as a path relative to another place reads
    existing = [candidate for candidate in candidates if _is_folder(candidate)]
    if existing:
        return existing[-1].resolve() == output_root.resolve()

    return any(candidate.name == output_root.name for candidate in candidates)


def _is_folder(path: Path) -> bool:
    """Whether `path` is a folder; a path the system cannot even look up (too long a name) is none."""

    try:
        return path.is_dir()
    except OSError:
        return False


def _read_recorded_pid(output_root: Path) -> int | None:
    """The process id the folder's newest run record names, or None."""

    runs_root = output_root / output_contract.RUNS_DIR_NAME
    if not runs_root.is_dir():
        return None
    # Real runs first, newest first: `dry-…` sorts after every dated run id, and a dry run's dead pid hid a live run
    directories = sorted((d for d in runs_root.iterdir() if d.is_dir()), key=lambda d: (not d.name.startswith("dry-"), d.name),
                         reverse=True)
    for directory in directories:
        state = directory / output_contract.RUN_STATE_FILE
        if state.is_file():
            pid = json_store_utils.read_json(state).get("pid")

            return int(pid) if isinstance(pid, int) else None

    return None


def list_published_folders(output_root: Path) -> list[Path]:
    """R13: the folders phase 4 published under the meeting's name, each `<name>/<name>.md`."""

    return sorted(folder for folder in output_root.iterdir()
                  if folder.is_dir() and (folder / f"{folder.name}.md").is_file()) if output_root.is_dir() else []


def list_reset_targets(paths: RunPaths,
                       phase: int) -> list[str]:
    """What `reset_to_phase` would remove or put back, one line each, relative to the run folder. Nothing is touched."""

    root = paths.output_root
    later = [number for number in sorted(PHASE_OUTPUTS) if number > phase]
    is_put_back = _is_published_put_back(later)
    lines: list[str] = []
    for number in later:
        outputs = PHASE_OUTPUTS[number]
        lines += [name for name in outputs.files if (root / name).exists()]
        lines += [f"{name}/" for name in outputs.dirs if (root / name).exists()]
        lines += [f"{output_contract.CALLS_DIR_NAME}/{log.name}" for prefix in outputs.call_prefixes
                  for log in _list_calls(root, prefix)]
        lines += [line for name in outputs.special_targets for line in _describe_special_target(root, name, is_put_back)]
    reports = _count_later_reports(root, later)
    lines += [f"{output_contract.WORKER_FEEDBACK_FILE}: {reports} report(s) sent from phase "
              f"{', '.join(str(number) for number in later)}"] if reports else []
    lines += [f"{store.relative_to(root)}: the progress of phase {', '.join(str(number) for number in later)}"
              for store in _list_run_stores(root)] if later else []

    return lines


def reset_to_phase(paths: RunPaths,
                   phase: int) -> list[str]:
    """Remove everything every phase after `phase` wrote, and forget its progress. Returns what `list_reset_targets` listed."""

    root = paths.output_root
    removed = list_reset_targets(paths, phase)
    later = [number for number in sorted(PHASE_OUTPUTS, reverse=True) if number > phase]

    # STEP 1: the published document back as it was before phase 7's first pass, then as phase 4 left it, while each
    # phase's copy of it still exists
    cleanup_before = document_pass_service.find_first_before(root)
    if output_contract.PHASE_NUMBERS[output_contract.DOCUMENT_RESHAPING_PHASE] in later and cleanup_before is not None:
        for folder in list_published_folders(root):
            (folder / f"{folder.name}.md").write_text(cleanup_before.read_text(encoding="utf-8"), encoding="utf-8")
    if _is_published_put_back(later):
        _restore_published(root)

    # STEP 2: every later phase's files, folders and call logs, the last phase first
    for number in later:
        outputs = PHASE_OUTPUTS[number]
        for name in outputs.files:
            (root / name).unlink(missing_ok=True)
        for name in outputs.dirs:
            if (root / name).is_dir():
                shutil.rmtree(root / name)
        for prefix in outputs.call_prefixes:
            for log in _list_calls(root, prefix):
                log.unlink()
        if _PUBLISHED_FOLDER_TARGET in outputs.special_targets:
            for folder in list_published_folders(root):
                shutil.rmtree(folder)

    # STEP 3: the workers' reports sent from a later phase, which the page would show as the redone phase's problems
    worker_feedback_service.remove_reports(root / output_contract.WORKER_FEEDBACK_FILE,
                                           lambda where: _find_call_phase(where) in later)

    # STEP 4: the frames stay when phase 2 is kept, without the notes phase 3 wrote into them
    if output_contract.PHASE_NUMBERS["frames_processing"] in later and (root / output_contract.LOOP_ITEMS_FILE).is_file():
        frame_records_service.clear_all_frame_records(paths)

    # STEP 5: every run store forgets the later phases, so a resume runs them as new
    for store_path in _list_run_stores(root):
        _remove_later_progress(store_path, phase)
        # The timing log keeps every row, so it is told which phases were taken back
        performance_log_service.record_reset(store_path.parent, phase)

    return removed
