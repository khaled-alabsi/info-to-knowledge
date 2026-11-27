"""What the UI shows of a run folder, read from the files a run writes, never from a run's memory.

A run started by hand on the command line shows up exactly like one started from the UI: the
newest run record (`logs/<runId>/run.json`), the phases the folder holds, the frames by status, the
newest console log, and whether a `run` process is writing into the folder now.
"""

from __future__ import annotations

import functools
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import config
from models import output_contract
from models import video_workflow_vocabulary
from models import phase_guide
from models.run_paths import RunPaths
from models.ui_errors import UnknownRunFolderError
from models.video_errors import AudioExtractionFailed, MissingMediaTool
from services import audio_extraction_service
from services import batch_summaries_service
from services import control_signal_service
from services import conversation_log_service
from services import document_pass_service
from services import document_file_service
from services import document_folder_service
from services import fast_turn_service
from services import inbox_move_service
from services import run_activity_service
from services import run_speed_service
from services import run_worker_set_service
from services import inbox_service
from services import inbox_tags_service
from services import ingesting_profile_service
from services import input_range_service
from services import frames_preparing_progress_service
from services import preprocessing_call_service
from services import performance_log_service
from services import recording_parts_service
from services import critique_collection_service
from services import pdf_default_mode_service
from services import pdf_source_service
from services import profile_version_service
from services import recording_compression_service
from services import run_recording_service
from services import worker_feedback_service
from services import run_artifact_service
from services import run_branch_service
from services import run_reset_service
from services import run_name_service
from services import recording_medium_service
from services import run_tags_service
from services import run_resume_service
from services import run_store_service
from services import video_workflow_service
from utils import display_path_utils
from utils import json_store_utils

__all__ = ["read_run_topic", "add_removed_frames", "RUN_ROOTS", "resolve_run_folder", "list_inbox_items",
           "list_archive_items", "list_run_folders", "clear_shared_run_list", "describe_run_folder", "find_newest_run_dir", "find_newest_log",
           "read_log_text", "list_live_pids", "describe_run_video", "describe_timing", "load_newest_record",
           "mark_step_in_work", "list_step_outcomes", "has_newer_settings", "describe_phase"]

# Where run folders are: every run under `output/` (inbox runs and hand-typed ones alike), and the folders earlier
# inbox runs left under `knowledge/`, which stay where they are.
RUN_ROOTS = {"output": config.DOCUMENT_ROOT, "knowledge": config.WORKSPACE_ROOT / "knowledge"}
# The run list the server's calls share for a moment, `[built at, folders]`, and the lock its builders wait on
_shared_list: list = [0.0, None, None]
_SHARED_LIST_LOCK = threading.Lock()
# The most a single log read returns, so a long log reaches a page in pieces rather than at once.
LOG_READ_BYTES = 256_000
# A run records a phase's status only when the phase ends, and a resume keeps the status the last run left (a phase
# 4 that blocked stays `blocked` while it runs again). So while a run process is live, the phase it is in is read from
# the newest log: the last line that opens with `phase N`, within the log's last part. `phase N time:` is the line a
# phase prints when it ENDS, so after it the next phase runs: phase 3 prints no line of its own when it starts.
_PHASE_LINE = re.compile(r"^phase (\d+)( time:)?", re.MULTILINE)
_PHASE_SCAN_BYTES = 512_000
# The phase whose units seconds per frame is counted over
_FRAMES_PROCESSING_PHASE = config.FRAMES_PROCESSING_PHASE


# The layer a dropped frame's `droppedBy` names, by the phase 2 stage that is that layer
_STAGE_DROPPED_BY = {frames_preparing_progress_service.CLASSIFIER: "filter",
                     frames_preparing_progress_service.MODEL: "redundancy",
                     frames_preparing_progress_service.SIMILARITY: "similarity"}


def add_removed_frames(stages: list[dict],
                       folder: Path) -> list[dict]:
    """Phase 2's stages, each filter stage with the frames it removed (`removed`: `{"slug", "path", "covered_by",
    "reason"}` each), counted from the dropped frames themselves, so a run whose stage record kept no counts shows
    them too (a page with dropped frames and an empty table showed nothing)."""

    dropped = run_artifact_service.list_dropped_frames(folder) if stages else []
    listed = []
    for stage in stages:
        layer = _STAGE_DROPPED_BY.get(stage["name"])
        if layer is None:
            listed.append(stage)
            continue
        removed = [{"slug": entry.get("slug", ""), "path": entry["path"],
                    "covered_by": [str(slug) for slug in entry.get("coveredBy") or []],
                    "reason": str(entry.get("reason") or "")}
                   for entry in dropped if entry.get("droppedBy") == layer]
        listed.append({**stage, "removed": removed})

    return listed


def resolve_run_folder(root_name: str,
                       folder_name: str) -> Path:
    """The run folder `<root>/<name>`, refused when it is not directly inside one of `RUN_ROOTS`."""

    root = RUN_ROOTS.get(root_name)
    folder = (root / folder_name).resolve() if root else None
    if folder is None or folder.parent != root.resolve() or not folder.is_dir():
        raise UnknownRunFolderError(f"no run folder {root_name}/{folder_name}")

    return folder


def load_newest_record(folder: Path) -> dict:
    run_dir = find_newest_run_dir(folder)
    store = run_store_service.RunStore.load(run_dir / output_contract.RUN_STATE_FILE) if run_dir else None

    return store.data if store else {}


def find_newest_run_dir(folder: Path) -> Path | None:
    """The newest `logs/<runId>/` that holds a run record, dry runs included, by when the record last changed."""

    records = list((folder / output_contract.RUNS_DIR_NAME).glob(f"*/{output_contract.RUN_STATE_FILE}"))

    return max(records, key=lambda path: path.stat().st_mtime).parent if records else None


def find_newest_log(folder: Path) -> Path | None:
    """The console log a run of this folder wrote last."""

    logs = list((folder / output_contract.RUNS_DIR_NAME).glob("*/*.log"))

    return max(logs, key=lambda path: path.stat().st_mtime) if logs else None


def list_live_pids(folder: Path) -> list[int]:
    """The process ids of the `run` processes writing into this folder now."""

    return [int(line.split()[0]) for line in run_reset_service.list_live_runs(folder)]


def describe_run_video(folder: Path) -> str:
    """The recording the folder's newest run record names, as a path; empty when no record names one."""

    return _describe_record_video(load_newest_record(folder))


def _describe_record_video(record: dict) -> str:
    if not record.get("repository") or not record.get("module"):
        return ""

    return str(Path(record["repository"]) / record["module"])


def _find_running_phase(log: Path | None) -> int | None:
    if log is None:
        return None
    size = log.stat().st_size
    with log.open("rb") as text:
        text.seek(max(0, size - _PHASE_SCAN_BYTES))
        tail = text.read().decode("utf-8", errors="replace")
    found = _PHASE_LINE.findall(tail)
    if not found:
        return None
    number, ended = int(found[-1][0]), bool(found[-1][1])
    later = [candidate for candidate in sorted(output_contract.PHASE_FILES) if candidate > number]

    # After the last phase's time line no phase runs: the process is only closing.
    return (later[0] if later else None) if ended else number


def _read_started_s(row: dict) -> float | None:
    if isinstance(row.get("startedAtS"), (int, float)):
        return float(row["startedAtS"])
    try:
        return datetime.fromisoformat(row["startedAt"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _read_row_phase(row: dict) -> str | None:
    """A performance row's phase by its name now, for a row a run wrote before the rename too."""

    return output_contract.RETIRED_STEP_NAMES.get(row.get("phase"), row.get("phase"))


def _count_phase_seconds(rows: list[dict]) -> dict[str, float]:
    """Seconds per phase over one session's rows: its phase line, or the sum of its unit lines when the session ended
    before the phase line was written (a killed process)."""

    seconds: dict[str, float] = {}
    closed = {_read_row_phase(row) for row in rows if row.get("level") == "phase"}
    for row in rows:
        phase = _read_row_phase(row)
        is_counted = (row.get("level") == "phase"
                      or (row.get("level") in performance_log_service.UNIT_LEVELS and phase not in closed))
        if phase and is_counted and isinstance(row.get("durationS"), (int, float)):
            seconds[phase] = seconds.get(phase, 0.0) + float(row["durationS"])

    return seconds


# The phases a resume runs again from the start, never from where they stopped: what an earlier try spent is gone
_RESTARTED_PHASES = ("transcribe", "frames_preparing")


def describe_timing(folder: Path,
                    running_phase: str | None,
                    now_s: float | None = None) -> dict:
    """How long each phase and the whole run took, over every session the folder's runs wrote to their
    `performance.jsonl`, resumes included. While a run is live (`running_phase` is set) the newest session is counted
    up to now: the running phase from its first unit line of that session, the run from that session's start. Phases 1
    and 2 start over on every run (`_RESTARTED_PHASES`), so each counts only the newest session that ran it: a phase 2 taken
    back and run many times read hours too long. One of them running again means every phase after it was taken back,
    so their earlier time is dropped there."""

    now = time.time() if now_s is None else now_s
    sessions: dict[tuple[str, str], list[dict]] = {}
    for log in sorted((folder / output_contract.RUNS_DIR_NAME).glob(f"*/{performance_log_service.PERFORMANCE_FILE}")):
        for row in performance_log_service.read_rows(log.parent):
            sessions.setdefault((log.parent.name, str(row.get("session"))), []).append(row)
    ordered = sorted(sessions.values(), key=lambda rows: min((_read_started_s(row) or 0.0) for row in rows))
    phases: dict[str, float] = {}
    total = 0.0
    for position, rows in enumerate(ordered):
        is_current = running_phase is not None and position == len(ordered) - 1
        counted = _count_phase_seconds(rows)
        restarted = [phase for phase in [*counted, running_phase if is_current else None] if phase in _RESTARTED_PHASES]
        if restarted:
            first_restarted = min(output_contract.PHASE_NUMBERS[phase] for phase in restarted)
            phases = {phase: seconds for phase, seconds in phases.items()
                      if output_contract.PHASE_NUMBERS.get(phase, 0) <= first_restarted}
        for phase, seconds in counted.items():
            phases[phase] = seconds if phase in _RESTARTED_PHASES else phases.get(phase, 0.0) + seconds
        video = next((row for row in rows if row.get("level") == "video"), None)
        if is_current:
            started = [_read_started_s(row) for row in rows if row.get("level") == "session"]
            total += now - started[0] if started and started[0] else 0.0
            units = [_read_started_s(row) for row in rows if _read_row_phase(row) == running_phase
                     and row.get("level") in performance_log_service.UNIT_LEVELS]
            closed = any(row.get("level") == "phase" and _read_row_phase(row) == running_phase for row in rows)
            if running_phase and not closed:
                first = min((value for value in units if value), default=started[0] if started else None)
                done = sum(float(row.get("durationS") or 0) for row in rows if _read_row_phase(row) == running_phase
                           and row.get("level") in performance_log_service.UNIT_LEVELS)
                earlier = 0.0 if running_phase in _RESTARTED_PHASES else phases.get(running_phase, 0.0) - done
                phases[running_phase] = earlier + (now - first if first else 0.0)
        elif video is not None and isinstance(video.get("durationS"), (int, float)):
            total += float(video["durationS"])
        else:
            total += sum(_count_phase_seconds(rows).values())

    return {"phases": {phase: round(seconds, 1) for phase, seconds in phases.items()}, "total_s": round(total, 1),
            "is_counting": running_phase is not None}


def _describe_recording(folder: Path) -> list[dict]:
    """The recording's parts with their start seconds, while the recording is still there; empty otherwise."""

    recording = run_artifact_service.find_recording(folder, describe_run_video(folder))

    return run_artifact_service.describe_recording_parts(recording) if recording is not None else []


def _list_batch_marks(paths: RunPaths) -> list[dict]:
    """Phase 3's batches as places to jump to in the recording: each batch's title, its first and last second, and its
    summary."""

    return [{"title": batch.get("title", ""), "start_s": float(batch["start"]), "end_s": float(batch["end"]),
             "summary": batch.get("summary", "")}
            for batch in batch_summaries_service.read_batch_summaries(paths)]


def mark_step_in_work(steps: list[tuple[str, str]],
                      is_live: bool) -> list[tuple[str, str]]:
    """The steps with the one a live run works on marked `running`: the first that is not accepted. A resume asks its
    blocked step again, so while it is live that step is no block."""

    if not is_live:
        return steps
    first = next((index for index, (_, status) in enumerate(steps) if status != "accepted"), None)

    return [(slug, "running" if index == first else status) for index, (slug, status) in enumerate(steps)]


def _group_document_stages(steps: list[tuple[str, str]]) -> list[dict]:
    """Phase 4's follow-ups in its three stages, each with its steps counted by status: the table of contents (the
    executive summary draft, the agenda, each chapter's headings), the chapters (each chapter's text, a step per part),
    and the final summary."""

    stages = {"table of contents": {}, "chapters": {}, "final summary": {}}
    for slug, step_status in steps:
        match = document_file_service.CHAPTER_NAME_PATTERN.match(slug)
        stage = ("chapters" if match and match.group(2) else "final summary" if slug == document_file_service.SUMMARY_SLUG
                 else "table of contents")
        stages[stage][step_status] = stages[stage].get(step_status, 0) + 1

    return [{"name": name, "counts": counts} for name, counts in stages.items()]


def read_run_topic(folder: Path) -> str:
    """What the meeting is about in a few words: phase 4's agenda step writes it into `04_writing/parts.json`, minutes
    into phase 4; empty before that, or when the file cannot be read."""

    path = document_file_service.build_parts_path(RunPaths(repository=folder, module=folder.name,
                                                           output_root_override=folder))
    try:
        parts = json_store_utils.read_json(path) if path.is_file() else {}
    except (OSError, ValueError):
        return ""

    return str(parts.get("topic") or "").strip() if isinstance(parts, dict) else ""


def _decide_run_state(is_live: bool,
                      has_document: bool,
                      status: str) -> str:
    """A run's state by the inbox's rule: `running` while live, `done` once it holds its document or completed,
    `started` otherwise. The web page's `describeRunState` follows the same rule."""

    if is_live:
        return "running"

    return "done" if has_document or status == "complete" else "started"


def _find_state_time(folder: Path,
                     state: str) -> float:
    """The moment a run's state is dated by, in seconds: for `running` when its newest console log began (this session
    started), for `done` when its document was written, for `started` when its newest console log last changed (it
    last did something). The folder's own change time when that file is missing."""

    document = folder / output_contract.SUMMARY_FILE
    if state == "done" and document.is_file():
        return document.stat().st_mtime
    log = find_newest_log(folder)
    if log is None:
        return folder.stat().st_mtime
    log_stat = log.stat()

    return getattr(log_stat, "st_birthtime", log_stat.st_ctime) if state == "running" else log_stat.st_mtime


def _decide_phase_statuses(record: dict,
                           on_disk: set[int]) -> dict[int, str]:
    """Each phase's status, from its record or its file: the one source the run's page and the Inbox's "phase N" read,
    so the two never name different phases."""

    recorded = record.get("phases", {})
    # A run stopped inside the frame loop records no status for it, and the loop counts as done only once every frame
    # has notes: frames already worked on mean it started and did not finish.
    is_frame_loop_started = any(item.get("status", "pending") in output_contract.ITEM_STATUSES
                                and item.get("status", "pending") != "pending"
                                for item in (record.get("items") or {}).values())
    statuses = {}
    for number, row in sorted(output_contract.PHASE_FILES.items()):
        status = recorded.get(row["phase"]) or ("complete" if number in on_disk else "not run")
        if status == "not run" and number == output_contract.PHASE_NUMBERS["frames_processing"] and is_frame_loop_started:
            status = "incomplete"
        statuses[number] = status

    return statuses


def _list_off_phases(folder: Path,
                     record: dict) -> tuple[int, ...]:
    """The phases this run's flow has none of: an audio recording's and a fast-processing run's review and critique
    collection, and a PDF's markdown conversion's phase 3 too, which reads nothing (the owner: a skipped phase is not
    shown). Phase 7, document reshaping, is off unless the run asked for it."""

    reshaping = () if document_pass_service.read_document_pass(folder) else (
        output_contract.PHASE_NUMBERS[output_contract.DOCUMENT_RESHAPING_PHASE],)
    if recording_medium_service.read_chosen_medium(folder) == output_contract.PDF_MARKDOWN_CONVERSION_MEDIUM:
        return (*output_contract.MARKDOWN_CONVERSION_OFF_PHASES, *reshaping)
    if _is_audio_record(folder, record):
        return (*output_contract.AUDIO_OFF_PHASES, *reshaping)

    return (*(video_workflow_vocabulary.FAST_OFF_PHASES
              if video_workflow_service.has_no_critic(folder) else ()),
            *_list_speed_off_phases(folder), *reshaping)


def _list_speed_off_phases(folder: Path) -> tuple[int, ...]:
    """The review and the critique collection when the run's Workflow speed switches them off (Balanced, Fast)."""

    values = (run_speed_service.read_chosen_speed(folder) or {}).get("values") or {}
    switches = (("review.isOn", "review"), ("critiqueCollection.isOn", output_contract.CRITIQUE_COLLECTION_PHASE))

    return tuple(output_contract.PHASE_NUMBERS[phase] for key, phase in switches if values.get(key) is False)


def _find_stopped_phase(folder: Path,
                        record: dict) -> int:
    """Where a run that is not going and has no document stopped: the first phase of its flow not complete, the phase
    its own page offers to resume from."""

    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    statuses = _decide_phase_statuses(record, run_resume_service.list_finished_phase_numbers(paths))
    off_phases = _list_off_phases(folder, record)

    return next((number for number in sorted(statuses)
                 if number not in off_phases and statuses[number] not in ("complete", "off")), 0)


def _is_audio_record(folder: Path,
                     record: dict) -> bool:
    """Whether the run takes the audio workflow, as the run itself decides it (`recording_medium_service`)."""

    video = _describe_record_video(record)

    return recording_medium_service.is_audio_run_recording(folder, Path(video) if video else None)


def _describe_input_range(folder: Path) -> str:
    """The part of the input the run in `folder` processes, as the page shows it; "" for the whole input."""

    input_range = input_range_service.read_range(folder=folder)

    return input_range_service.describe_range(input_range=input_range) if input_range is not None else ""


def _describe_pdf_mode(folder: Path,
                       video: str) -> str:
    """A document run's processing mode, `pages-as-images`, `text-only` or `markdown-conversion` (its chosen medium); an
    office document's too, so its page offers what a PDF's does; empty for a run of another recording."""

    if not inbox_service.is_document(Path(video)):
        return ""

    medium = recording_medium_service.read_chosen_medium(folder)

    return next((mode for mode, chosen in output_contract.PDF_MODE_MEDIUMS.items() if chosen == medium),
                output_contract.PDF_MODE_PAGES)


def describe_phase(number: int,
                    is_audio: bool,
                    pdf_mode: str = "",
                    document_pass: str = "") -> dict:
    """The phase card's title and help for this run's medium: an audio recording's phases 2 and 3 are other work than a
    video's, and its other phases read no frame; a PDF's phases 2 and 3 are named by its pages or its figures. Phase 7
    is named by the pass the run asked for."""

    title = phase_guide.PHASE_TITLES.get(number, output_contract.PHASE_FILES[number]["phase"].replace("_", " "))
    guide = phase_guide.PHASE_GUIDES.get(number, {})
    if is_audio:
        title = phase_guide.AUDIO_PHASE_TITLES.get(number, title)
        guide = {**guide, **phase_guide.AUDIO_PHASE_GUIDES.get(number, {})}
    if pdf_mode:
        titles, guides = {output_contract.PDF_MODE_TEXT: (phase_guide.PDF_TEXT_ONLY_PHASE_TITLES,
                                                          phase_guide.PDF_TEXT_ONLY_PHASE_GUIDES),
                          output_contract.PDF_MODE_MARKDOWN_CONVERSION: (
                              phase_guide.PDF_MARKDOWN_CONVERSION_PHASE_TITLES,
                              phase_guide.PDF_MARKDOWN_CONVERSION_PHASE_GUIDES),
                          }.get(pdf_mode, (phase_guide.PDF_PHASE_TITLES, phase_guide.PDF_PHASE_GUIDES))
        title = titles.get(number, title)
        guide = {**phase_guide.PHASE_GUIDES.get(number, {}), **guides.get(number, {})}
    if number == output_contract.PHASE_NUMBERS[output_contract.DOCUMENT_RESHAPING_PHASE]:
        if document_pass == output_contract.DOCUMENT_PASS_KNOWLEDGE:
            guide = {**guide, "summary": phase_guide.KNOWLEDGE_PASS_SUMMARY}

    return {"title": title, **guide}


def _has_phase_worker(folder: Path,
                      number: int,
                      name: str) -> bool:
    """Whether a worker of the phase left anything to show: phase 4's or 5's conversation log, one attempt of a call, or
    a fast-processing run's turns of phase 3 or 4."""

    if number in (3, 4) and fast_turn_service.list_phase_turns(folder, number):
        return True
    log = {4: output_contract.WRITE_CONVERSATION_LOG, 5: output_contract.REVIEW_CONVERSATION_LOG}.get(number)
    if log:
        return (folder / log).is_file()
    logs = folder / output_contract.RUNS_DIR_NAME
    if not logs.is_dir():
        return False

    return next(logs.glob(f"*/{name}/**/attempt-*"), None) is not None


def _find_document_file(folder: Path,
                        paths: RunPaths) -> str:
    """The published document's path inside the run folder, for the header's Open and Download; empty before it is
    published."""

    if not paths.summary.is_file():
        return ""
    document = document_folder_service.build_document_path(paths)

    return str(document.relative_to(folder)) if document.is_file() else ""


def _list_document_versions(folder: Path,
                            paths: RunPaths) -> list[dict]:
    # the published folder is named from phase 4's document; while phase 4 writes it again, the documents set aside
    # are still listed
    document = document_folder_service.build_document_path(paths) if paths.summary.is_file() else None

    return profile_version_service.list_document_versions(folder, document)


def _is_derived_folder(folder: Path) -> bool:
    """Whether a run folder was made from another run, by Branch (its `branch.json`) or by Convert (its profile chosen
    by the conversion): a run of its own, never an earlier attempt of its video."""

    profile = ingesting_profile_service.read_profile(folder) or {}

    return run_branch_service.read_branch(folder) is not None or profile.get("chosenBy") == "convert"


def _holds_settings_alone(folder: Path) -> bool:
    """Whether a folder holds only the settings a run line chose before phase 1 (`run-*.json`, `ingesting-profile.json`)
    and nothing a run made: a refused start left such a folder, which the run list showed as an unfinished run at phase
    1 though no run ever ran there (DEFECTS 720). Its tags (`run-tags.json`) are the person's own and make it a run."""

    entries = list(folder.iterdir())

    return bool(entries) and all(entry.is_file() and entry.suffix == ".json"
                                 and entry.name != output_contract.RUN_TAGS_FILE
                                 and (entry.name.startswith("run-") or entry.name == "ingesting-profile.json")
                                 for entry in entries)


def _count_stored_recording_bytes(folder: Path) -> int:
    """The bytes of the recording the run keeps in its folder, every part counted; 0 when it keeps none."""

    recording = run_recording_service.find_stored_recording(folder)
    if recording is None:
        return 0

    return sum(part.stat().st_size for part in recording_parts_service.list_recording_parts(recording))


def _summarise_folder(root_name: str,
                      folder: Path,
                      processes: list[str] | None = None) -> dict:
    record = load_newest_record(folder)
    is_live = bool(run_reset_service.list_live_runs(folder, processes))
    has_document = (folder / output_contract.SUMMARY_FILE).is_file()
    status = "running" if is_live else record.get("status", "no run record")
    # a run asked for some phases only (`--to 2`) records its own range complete; the run itself is not, until every
    # phase the document depends on is ("complete" was once shown on a run stopped after phase 2)
    if status == "complete" and not run_recording_service.is_run_done(record):
        status = "incomplete"
    video = _describe_record_video(record)
    state = _decide_run_state(is_live, has_document, status)

    return {"root": root_name, "name": folder.name, "folder": str(folder), "path": display_path_utils.render_path(folder),
            "run_id": record.get("runId", ""),
            "status": status,
            "started_at": record.get("startedAt", ""), "video": video,
            # the inbox path of the recording the run keeps in its folder, by which the person's lists hold it: a run
            # started again from its kept recording names that copy as its video, and left its list (the owner)
            "origin_video": run_recording_service.read_origin_video(folder),
            # an audio recording has no frames to show; the profile its document is written in
            "is_audio": _is_audio_record(folder, record),
            # the recording's own file, `video`, `audio` or `pdf`; empty when the run names none
            "file_type": inbox_service.describe_file_type(Path(video)) if video else "",
            # the recording kept in the run folder once the run is done, so its row still shows the file's size
            "size_bytes": _count_stored_recording_bytes(folder),
            # a PDF's processing mode, `pages` or `markdown`; empty for any other recording
            "pdf_mode": _describe_pdf_mode(folder, video),
            # the LLM pass phase 7 was asked to give the document (`cleanup`, `rewrite`, `knowledge`), so the Document
            # pill names what the document is; empty when none was asked
            "document_pass": (document_pass_service.read_document_pass(folder) or {}).get("pass", ""),
            "ingesting_profile": ingesting_profile_service.resolve_profile(folder),
            # the part of the input the run processes ("12:30 to 1:05:00", "pages 12 to 40"); empty for the whole input
            "input_range": _describe_input_range(folder),
            # the settings profile the run read on top of the default settings (`--profile`), empty for none
            "settings_profile": record.get("settingsProfile") or "",
            # whether a tag, phase 2 or a conversion has chosen it yet: an audio run's is picked by phase 2
            "is_profile_chosen": ingesting_profile_service.read_profile(folder) is not None,
            "is_live": is_live, "has_document": has_document,
            "topic": read_run_topic(folder), "tags": run_tags_service.read_tags(folder),
            "display_name": run_name_service.read_display_name(folder),
            "state_at": _find_state_time(folder, state),
            # only a started run is read for it: phase 3's check reads every frame's notes
            "stopped_phase": _find_stopped_phase(folder, record) if state == "started" else 0,
            # the phase a live run is in (its first phase not complete), so the running count's menu names it; 0 for a
            # run not going
            "live_phase": _find_stopped_phase(folder, record) if is_live else 0,
            # the worker set the run chose, so the running count's menu names it; empty for the default set
            "worker_set": run_worker_set_service.read_chosen_worker_set(folder) or "",
            # a phase recorded blocked or failed: a resume asks the blocked step again
            "is_blocked": any(status in ("blocked", "failed") for status in (record.get("phases") or {}).values()),
            # made by Branch or Convert from another run: listed apart from its video's own run
            "is_derived": _is_derived_folder(folder),
            # phase 1's transcript, which every later phase builds on: a folder reset to 0 has none
            "has_phase_output": (folder / output_contract.TRANSCRIPT_FILE).is_file(),
            "updated_at": folder.stat().st_mtime}


def _decide_inbox_state(is_finished: bool,
                        runs: list[dict]) -> str:
    if any(run["is_live"] for run in runs):
        return "running"
    if is_finished or any(run["has_document"] for run in runs):
        return "done"

    return "started" if runs else "new"


def _list_video_runs(folders: list[dict],
                     resolved_video: str) -> list[dict]:
    # a folder made from another run by Branch or Convert is listed apart, never folded into its video's row
    return [{key: folder[key] for key in ("root", "name", "status", "is_live", "has_document", "topic", "display_name", "state_at",
                                    "stopped_phase", "has_phase_output", "is_blocked", "worker_set")}
            for folder in folders
            if folder["video"] and not folder["is_derived"] and str(Path(folder["video"]).resolve()) == resolved_video]


def _holds_run(run: dict) -> bool:
    """Whether a run counts for its video: running, or holding a phase's output. One that holds nothing (reset to 0, or
    stopped before phase 1 wrote) is as if it never ran, so its video is new."""

    return run["is_live"] or run["has_document"] or run["has_phase_output"]


@functools.lru_cache(maxsize=512)
def _count_pages_once(path: Path,
                      changed_at: float) -> int:
    """A PDF's page count, read once for each version of the file (`changed_at`, its modification time): the inbox is
    listed every few seconds."""

    return pdf_default_mode_service.count_pages(path)


def _count_pdf_pages(video: Path) -> int:
    if not video.is_file() or not pdf_source_service.is_pdf(video):
        return 0

    return _count_pages_once(path=video, changed_at=video.stat().st_mtime)


@functools.lru_cache(maxsize=512)
def _read_part_seconds_once(path: Path,
                            changed_at: float) -> float:
    """A recording file's length by ffprobe, read once for each version of the file (`changed_at`, its modification
    time): the inbox is listed every few seconds; 0 when ffprobe cannot read it."""

    try:
        return audio_extraction_service.read_duration(path)
    except (AudioExtractionFailed, MissingMediaTool):
        # a file whose length cannot be read gets a run form with no time bounds, never a broken inbox
        return 0.0


def _read_recording_seconds(parts: list[Path]) -> float:
    """A video's or an audio recording's length, its parts' lengths added; 0 for a document."""

    if not parts or any(inbox_service.is_document(part) for part in parts):
        return 0.0

    return round(sum(_read_part_seconds_once(path=part, changed_at=part.stat().st_mtime) for part in parts), 1)


def _list_inbox_parts(parts: list[Path]) -> list[dict]:
    """A folder of parts as the page lists them: each part's name, size and start on the meeting's clock."""

    listed, start_s = [], 0.0
    for part in parts:
        listed.append({"name": part.name, "size_bytes": part.stat().st_size, "start_s": round(start_s, 1)})
        start_s += _read_part_seconds_once(path=part, changed_at=part.stat().st_mtime)

    return listed


def _find_inbox_item_time(video: Path,
                          state: str,
                          runs: list[dict]) -> float:
    """The moment an inbox video's state is dated by, in seconds: a `new` one by when it came into the inbox, any
    other by the newest date of its runs in that same state (of all its runs when none is in it)."""

    if not runs:
        return video.stat().st_ctime
    in_state = [run for run in runs if _decide_run_state(run["is_live"], run["has_document"], run["status"]) == state]

    return max(run["state_at"] for run in in_state or runs)


def _choose_topic(runs: list[dict]) -> str:
    # the topic of the live run first, else of the first run that has one
    return next((run["topic"] for run in sorted(runs, key=lambda run: not run["is_live"]) if run["topic"]), "")


def list_inbox_items(inbox_root: Path,
                     document_root: Path,
                     max_age_seconds: float = 0.0) -> list[dict]:
    """Every video `i2k inbox` would see, newest first by the date its state means (`state_at`), with the run folder `inbox` would write it into, every run folder whose run
    record names this video (one typed by hand under `output/` too), and its state: `running` while one of those
    runs is live, `done` once one holds its document, `started` when a run holds a phase's output (phase 1's transcript), `new`
    otherwise: a run reset to 0 is as if it never ran. `max_age_seconds` as `list_run_folders` takes it."""

    folders = list_run_folders(max_age_seconds)
    items = []
    for video in inbox_service.list_inbox_videos(inbox_root):
        matched = _list_video_runs(folders, str(video.resolve()))
        runs = [run for run in matched if _holds_run(run)]
        is_finished = inbox_service.is_item_finished(document_root, video)
        state = _decide_inbox_state(is_finished, runs)
        parts = recording_parts_service.list_recording_parts(video)
        run_folder = inbox_service.build_run_folder(document_root, video)
        items.append({"name": video.name, "video": str(video), "path": display_path_utils.render_path(video),
                      "size_bytes": sum(part.stat().st_size for part in parts),
                      # each part with where it starts on the meeting's clock, so the player opens a second in its part
                      "parts": _list_inbox_parts(parts) if video.is_dir() else [],
                      "run_folder": run_folder.name, "output": str(run_folder),
                      # the file has no picture: the page offers no "Process as" for it, since it is audio already
                      "is_audio": inbox_service.is_audio_recording(video),
                      "file_type": inbox_service.describe_file_type(video),
                      # a PDF's pages, which pick its default processing and shaping on the run form; 0 for others
                      "page_count": _count_pdf_pages(video),
                      # a video's or an audio recording's length in seconds, which the run form's Range is bounded by;
                      # 0 for a PDF or a file ffprobe cannot read
                      "duration_s": _read_recording_seconds(parts),
                      "is_finished": is_finished, "runs": runs, "state": state,
                      "state_at": _find_inbox_item_time(video, state, runs), "topic": _choose_topic(runs),
                      "display_name": _choose_display_name(runs),
                      # its run folders that hold nothing: still the video's, so the page lists them nowhere else
                      "empty_runs": [{"root": run["root"], "name": run["name"]} for run in matched if not _holds_run(run)],
                      # the tags given on its page before its first run
                      "tags": inbox_tags_service.read_video_tags(video)})

    return sorted(items, key=lambda item: item["state_at"], reverse=True)


def list_archive_items(inbox_root: Path) -> list[dict]:
    """What `inbox/.archive/` holds, each entry with the run folders whose run record names the video it was in the
    inbox, their state by the inbox's own rule, and the topic."""

    folders = list_run_folders()
    items = []
    for entry in inbox_move_service.list_moved(inbox_root, inbox_move_service.ARCHIVE):
        runs = _list_video_runs(folders, entry["video"]) if entry["video"] else []
        items.append({**entry, "runs": runs, "state": _decide_inbox_state(False, runs), "topic": _choose_topic(runs)})

    return items


def _choose_display_name(runs: list[dict]) -> str:
    # the name the live run is shown under first, else the first run's that has one
    return next((run["display_name"] for run in sorted(runs, key=lambda run: not run["is_live"]) if run["display_name"]), "")


def list_run_folders(max_age_seconds: float = 0.0) -> list[dict]:
    """Every run folder under `output/` and `knowledge/`, the most recently changed first. With `max_age_seconds`, a
    list built that recently is handed out again, and callers arriving while one is built wait for it: the page asks
    for the runs, the inbox and the tags every 3 seconds from each open page, each building the list, and the server
    spun a core beside the model run, each call queuing 6 to 8 seconds (DEFECTS 405)."""

    # a list built over other roots is no list of these: a test's roots, or a server pointed elsewhere
    roots = tuple((root_name, str(root)) for root_name, root in RUN_ROOTS.items())
    with _SHARED_LIST_LOCK:
        built_at, folders, built_roots = _shared_list
        if (max_age_seconds and folders is not None and built_roots == roots
                and time.monotonic() - built_at < max_age_seconds):
            return list(folders)
        # the machine's run processes read once for every folder
        processes = run_reset_service.list_run_processes()
        folders = sorted((_summarise_folder(root_name, folder, processes)
                          for root_name, root in RUN_ROOTS.items() if root.is_dir()
                          for folder in root.iterdir()
                          if folder.is_dir() and not folder.name.startswith(".") and not _holds_settings_alone(folder)),
                         key=lambda row: row["updated_at"], reverse=True)
        _shared_list[:] = [time.monotonic(), folders, roots]

    return list(folders)


def clear_shared_run_list() -> None:
    """The next `list_run_folders` builds the list afresh, whatever its age: a request that changed a run asks it."""

    with _SHARED_LIST_LOCK:
        _shared_list[:] = [0.0, None, None]


def list_step_outcomes(folder: Path) -> list[dict]:
    """Every follow-up of phases 4 and 5 in the order it was asked, with its phase number, how often it blocked, was
    accepted and was reset, and each block's reason and time, read from the two conversations' logs."""

    logs = ((output_contract.PHASE_NUMBERS["finalize"], output_contract.WRITE_CONVERSATION_LOG),
            (output_contract.PHASE_NUMBERS["review"], output_contract.REVIEW_CONVERSATION_LOG))

    return [{"phase": number, **step} for number, log in logs
            for step in conversation_log_service.count_follow_up_outcomes(folder / log)]


def _find_process_start(pid: int) -> float | None:
    """When a live process started, from `ps`'s elapsed time ([[dd-]hh:]mm:ss); None when it cannot be read."""

    try:
        elapsed = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not elapsed:
        return None
    days, _, clock = elapsed.rpartition("-")
    seconds = 0
    for part in clock.split(":"):
        seconds = seconds * 60 + int(part)

    return time.time() - seconds - int(days or 0) * 86400


def has_newer_settings(record: dict,
                       live_pids: list[int] | None = None) -> bool:
    """Whether a settings file this run reads (the default settings, and its profile's) changed after the run read them:
    the run goes on with what it read, and takes the new values only when it is stopped and resumed. A run started
    before it recorded when it read them is taken to have read them when its process started."""

    read_at = record.get("settingsReadAt") or (_find_process_start(live_pids[0]) if live_pids else None)
    if not read_at:
        return False
    profile = record.get("settingsProfile") or ""
    files = [config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE] + ([config.build_profile_path(profile)] if profile else [])

    return any(path.is_file() and path.stat().st_mtime > read_at for path in files)


def describe_run_folder(root_name: str,
                        folder_name: str) -> dict:
    """One run folder in full: its summary, each phase with its status, the frames by status and the blocked ones by
    slug, phase 4's follow-ups by stage and status (`document_stages`), the newest log, and the control signal waiting for the
    run."""

    folder = resolve_run_folder(root_name, folder_name)
    record = load_newest_record(folder)
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    on_disk = run_resume_service.list_finished_phase_numbers(paths)
    log = find_newest_log(folder)
    live_pids = list_live_pids(folder)
    running_number = _find_running_phase(log) if live_pids else None

    # STEP 1: the frames by status, and the blocked ones by slug
    frames = {status: 0 for status in output_contract.ITEM_STATUSES}
    blocked_items = []
    for slug, item in (record.get("items") or {}).items():
        frames[item.get("status", "pending")] = frames.get(item.get("status", "pending"), 0) + 1
        if item.get("status") == "blocked":
            blocked_items.append(slug)

    # STEP 2: each phase's status, from its record or its file
    statuses = _decide_phase_statuses(record, on_disk)

    # STEP 3: the phase a live run is in. A resume reads a finished phase from its file and prints a line naming it, then the next phase starts without a
    # line of its own: the phase running is the first one from the log's phase on that is not complete.
    if running_number is not None:
        running_number = next((number for number in sorted(statuses)
                               if number >= running_number and statuses[number] != "complete"), None)
    # A resume asks every blocked frame again, and phase 3 stops on the first frame that blocks anew: while a live run
    # reads frames, a frame recorded blocked is waiting to be read, as `mark_step_in_work` does for phase 4's steps
    if running_number == output_contract.PHASE_NUMBERS["frames_processing"]:
        frames["pending"], frames["blocked"] = frames["pending"] + frames["blocked"], 0
        blocked_items = []

    # STEP 4: the answer
    is_audio = _is_audio_record(folder, record)
    pdf_mode = _describe_pdf_mode(folder, _describe_record_video(record))
    workflow = video_workflow_service.resolve_workflow(folder)
    # An audio recording's flow and a fast-processing run have no critic: their review and critique collection are no
    # phase they show
    off_phases = _list_off_phases(folder, record)
    document_pass = (document_pass_service.read_document_pass(folder) or {}).get("pass", "")
    phases = [{"number": number, "name": output_contract.PHASE_FILES[number]["phase"],
               "status": "running" if number == running_number else statuses[number],
               **describe_phase(number, is_audio, pdf_mode, document_pass)} for number in sorted(statuses) if number not in off_phases]
    # An audio run's phase 3 is complete with nothing written when its profile prepares nothing for this many
    # paragraphs: the card says so, since "complete" alone left a reader looking for a file that never was
    for phase in phases:
        # A card offers its worker only when one ran: a phase with no model call, or one that read nothing, has none
        phase["has_worker"] = bool(phase.get("is_model_used")) and (
            phase["status"] == "running" or _has_phase_worker(folder, phase["number"], phase["name"]))
        if (is_audio and phase["number"] == output_contract.PHASE_NUMBERS["frames_processing"]
                and phase["status"] == "complete" and not (folder / output_contract.PREPARED_DIR).is_dir()):
            phase["note"] = "nothing to prepare"
    run_dir = find_newest_run_dir(folder)
    recording_parts = _describe_recording(folder)
    running_name = output_contract.PHASE_FILES[running_number]["phase"] if running_number else ""
    document_steps = document_file_service.list_follow_up_steps(paths)
    is_document_total_known = document_file_service.is_agenda_known(paths)

    timing = describe_timing(folder, running_name if live_pids else None)
    record_video = _describe_record_video(record)
    is_recording_kept = run_recording_service.find_stored_recording(folder) is not None

    return {**_summarise_folder(root_name, folder), "phases": phases, "frames": frames,
            "timing": timing,
            "recording_parts": recording_parts,
            "has_recording": bool(recording_parts),
            # A running run whose settings files changed after it read them: the page says the change waits for a resume
            "has_newer_settings": bool(live_pids) and has_newer_settings(record, live_pids),
            # A done run keeps its recording in its folder; phases 1 and 2 wait until it is back in the inbox
            "is_recording_kept": is_recording_kept,
            # Compress recording: its status, sizes before and after, and error; {} when never compressed
            "recording_compression": recording_compression_service.describe_compression(folder),
            # A done run whose recording is still in the inbox can keep it in its folder (the page's button)
            "can_keep_recording": bool(record_video) and not is_recording_kept
                                  and run_recording_service.can_keep_recording(record, config.INBOX_DIR, Path(record_video)),
            "batches": _list_batch_marks(paths),
            # A step runs only while phase 4 does, and a markdown conversion writes its document with no step at all: its
            # table stood "running" through the hours of a later phase
            "document_stages": ([] if pdf_mode == output_contract.PDF_MODE_MARKDOWN_CONVERSION else _group_document_stages(
                mark_step_in_work(document_steps, running_number == output_contract.PHASE_NUMBERS["finalize"]))),
            "is_document_total_known": is_document_total_known,
            "document_file": _find_document_file(folder, paths),
            # the document as phase 4 wrote it and after each clean up or rewrite, for the Open document menu; empty
            # while phase 7 never ran
            "document_versions": _list_document_versions(folder, paths),
            # the profiles a finished audio run can write its document in again in this folder, from the same notes
            "appendable_profiles": profile_version_service.list_appendable_profiles(folder),
            # the document's chapters, one per agenda point, for Reset from chapter; none before the agenda is written
            "chapters": [{"number": number, "title": point.get("title", "")}
                         for number, point in enumerate(document_file_service.read_parts(paths).get("points") or [], start=1)],
            # what the run said about its own work: the rules its batches reported, grouped and counted the way
            # phase 6 groups them, so the page shows the same numbers the rule set does
            "critique": describe_critique(paths),
            # the problems the workers reported through `report_problem`, so the page can say so and open the file: a
            # report nobody reads is a call spent for nothing
            "worker_feedback": describe_worker_feedback(folder),
            # how long one frame of phase 3 has taken on average so far, for the Frames bar
            "seconds_per_frame": _compute_seconds_per_frame(folder),
            # an audio recording's phase 3: each preparation step's calls done of how many, for its bar
            "preparation": preprocessing_call_service.describe_progress(paths),
            # phase 2's stages, the rules, the classifier and the model, each with how far it is, for its bar
            "frames_preparing": add_removed_frames(
                frames_preparing_progress_service.read_progress(run_dir) if run_dir else [], folder),
            # phase 1's stages: the transcription, then the speakers
            "transcribing": frames_preparing_progress_service.read_progress(
                run_dir, frames_preparing_progress_service.TRANSCRIBE_PROGRESS_FILE) if run_dir else [],
            # phase 7's parts, each with its status, calls and time, for its bar and table (none while no pass cut them)
            "document_pass_parts": document_pass_service.describe_parts(folder, bool(live_pids), time.time()),
            # the run and phase a branched run was copied from, None for any other run
            "branch": run_branch_service.read_branch(folder),
            # the video workflow from phase 3 on, quality or fast-processing; an audio recording's is the audio workflow
            "workflow": "" if is_audio else workflow,
            # the Workflow speed chosen for the run, {"preset", "values", "name"}, None when the settings decided
            "speed": None if is_audio else run_speed_service.read_chosen_speed(folder),
            # the worker set the run keeps (its models and hosts); empty when it kept none
            "worker_set": run_worker_set_service.read_chosen_worker_set(folder) or "",
            # how often each phase 4 and 5 step blocked, was accepted and was reset (`i2k blocks` prints the same)
            "step_outcomes": list_step_outcomes(folder),
            "blocked_items": sorted(blocked_items),
            "live_pids": live_pids, "log": display_path_utils.render_path(log) if log else "",
            "signal": control_signal_service.read_signal(run_dir) if run_dir else None,
            # the pause reached: the run holds now, no longer only asked to
            "is_held": bool(run_dir) and control_signal_service.is_run_holding(run_dir, log),
            # what the live run does right now, in words, since when: preparing a request, or waiting for the model
            "activity": run_activity_service.read_activity(run_dir) if run_dir and live_pids else None}


def describe_worker_feedback(folder: Path) -> dict:
    """The problems the workers of this run reported: how many entries, how many reports in all (one entry's count
    rises when it is reported again), how many no answer covers, the newest entry's kind and summary and its answer
    ("" while it is open), and the file to open. The owner saw a report on the page that had been judged and fixed hours
    before, with nothing saying so, and had to ask whether it was read."""

    path = folder / output_contract.WORKER_FEEDBACK_FILE
    entries = worker_feedback_service.read_reports(path)
    open_whens = {entry.when for entry in worker_feedback_service.list_open_reports(path)}
    answers = worker_feedback_service.read_answers(folder / output_contract.WORKER_FEEDBACK_ANSWERS_FILE)
    newest_answer = answers[entries[-1].when].text if entries and entries[-1].when not in open_whens else ""

    return {"entries": len(entries), "reports": sum(entry.times for entry in entries),
            "open": sum(1 for entry in entries if entry.when in open_whens),
            "newest_kind": entries[-1].kind if entries else "",
            "newest_summary": entries[-1].summary if entries else "",
            "newest_answer": newest_answer,
            "file": output_contract.WORKER_FEEDBACK_FILE if entries else ""}


def _compute_seconds_per_frame(folder: Path) -> float:
    """What one frame of phase 3 has cost so far: the seconds of every FINISHED unit of it over the frames they
    covered - the batch call, its repair, its critique, the repair of the notes, its questions and their answers."""

    # The batch in flight is left out on purpose: its seconds run while it has written no frame, and dividing phase
    # 3's whole elapsed time by the frames finished so far read 3:19 a frame where the console said 26 s.

    seconds, covered = 0.0, set()
    for log in sorted((folder / output_contract.RUNS_DIR_NAME).glob(f"*/{performance_log_service.PERFORMANCE_FILE}")):
        for row in performance_log_service.read_rows(log.parent):
            if _read_row_phase(row) != _FRAMES_PROCESSING_PHASE or row.get("level") not in performance_log_service.UNIT_LEVELS:
                continue
            seconds += float(row.get("durationS") or 0.0)
            covered.update(str(slug) for slug in row.get("frameSlugs") or [])

    return round(seconds / len(covered), 1) if covered and seconds > 0 else 0.0


def describe_critique(paths: RunPaths) -> dict:
    """The run's own critique of its work, for the page's Critique card: how many rules its batches reported, from how
    many batches, how many anti-patterns they group into, and the most frequent one's name. The grouping is phase 6's
    own (`services/critique_collection_service.py`), so the card and the rule set never disagree. The rule set is named
    under the path it was written to, which is the retired one for a run made before the rename."""

    files = critique_collection_service.list_critique_files(paths)
    rules = critique_collection_service.read_run_rules(paths)
    groups = critique_collection_service.group_rules(rules)
    rule_set = critique_collection_service.find_written_rule_set(paths)

    return {"files": len(files), "rules": len(rules), "anti_patterns": len(groups),
            "top": groups[0]["name"] if groups else "",
            "top_batches": len(groups[0]["batches"]) if groups else 0,
            "top_frames": groups[0]["frames"] if groups else 0,
            "rule_set": rule_set.relative_to(paths.output_root).as_posix() if rule_set else ""}


def read_log_text(path: Path | None,
                  offset: int) -> tuple[str, int]:
    """The text of `path` from byte `offset`, at most `LOG_READ_BYTES` of it, and the offset to read from next. A
    file shorter than `offset` was replaced, so it is read from its start."""

    if path is None or not path.is_file():
        return "", 0
    size = path.stat().st_size
    start = offset if 0 <= offset <= size else 0
    with path.open("rb") as log:
        log.seek(start)
        chunk = log.read(LOG_READ_BYTES)

    return chunk.decode("utf-8", errors="replace"), start + len(chunk)
