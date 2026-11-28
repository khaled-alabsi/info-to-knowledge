"""The HTTP API the web page and the terminal UI share, and the built web page itself.

Every route reads or writes through a service, never in itself; where the command line has the same operation (the
inbox listing, the run record, the control signal, `reset-to`, `reset-chapter`, the recording moves), the route calls
the same function. Runs go through the job queue, so the API never starts a run by itself. A page follows a run or a
job by asking its `/update` route once a second, each answer holding the state and the log text added since the last.
"""

from __future__ import annotations

import collections
import re
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import config
from models import output_contract
from models import video_workflow_vocabulary
from models import ingesting_profile_vocabulary
from models import phase_guide
from models.operation_result import OperationResult
from models import run_job
from models.run_job import RunJob, RunJobRequest
from models.web_api_request import (JobItemRequest, ProviderSetRequest, RunSpeedRequest, AccountChangeRequest, AccountRequest, SignInRequest, BatchDownloadRequest, JobMoveRequest, JobOnFailureRequest, QueuePauseRequest, PartMessageRequest,ModelsConfigureRequest, ModelsDownloadRequest, BranchRequest, ConvertRequest, PartRunRequest, RunNamesRequest, RunRefsRequest, ControlRequest, RunDisplayNameRequest, RunTagsRequest, FrameDropRequest, HiddenRequest, ResetRequest, SettingRequest, SettingRemoveRequest, RestoreRequest, StartJobsRequest,
                                    InboxTagsRequest, RenameRequest, MergeRequest, MoveRequest, ZipRequest,
                                    MarkdownWriteRequest, ProjectRequest, GrantRequest, GroupRequest, GroupAccountsRequest,
                                    RunListsRequest, InstructionSaveRequest, InstructionResetRequest,
                                    InstructionPreviewRequest, PhaseFlowRequest)
from models.input_range_spec import InputRangeSpec
from models.instruction_errors import InstructionRefusedError, UnknownInstructionFileError
from models.run_errors import PromptBuildError
from models.ui_errors import (AccountError, DuplicateRecordingError, UnknownArtifactError, UnknownInboxItemError, UnknownJobError,
                              UnknownRunFolderError, UnreadableTagsError, UploadRefusedError)
from models.video_errors import AudioExtractionFailed, MissingMediaTool
from services import account_service
from services import batch_compression_service
from services import run_lists_service
from services import group_service
from services import project_service
from services import pdf_source_service
from services import recording_parts_service
from services import artifact_edit_service
from services import batch_download_service
from services import batch_history_service
from services import document_chat_service
from services import document_pass_service
from services import document_step_history_service
from services import control_signal_service
from services import hidden_artifact_service
from services import inbox_merge_service
from services import inbox_rename_service
from services import inbox_service
from services import inbox_tags_service
from services import ingesting_profile_service
from services import input_preview_service
from services import input_range_service
from services import inbox_move_service
from services import instruction_catalogue_service
from services import instruction_edit_service
from services import instruction_store_service
from services import job_queue_service
from services import run_provider_set_service
from services import run_artifact_service
from services import run_operations_service
from services import run_recording_service
from services import run_reset_service
from services import run_speed_service
from services import run_status_service
from services import settings_service
from services import setup_check_service
from services import upload_service
from services import upload_bundle_service
from services import worker_live_service
from services import worker_message_service
from services import worker_view_service
from utils import mermaid_text_utils
from utils import route_role_utils
from models import account_role_vocabulary

__all__ = ["set_sign_in", "WEB_DIST_DIR", "SESSION_COOKIE", "build_app"]

# The page `npm run build` writes in `web/`; the server hosts it as it is.
WEB_DIST_DIR = config.PLAYBOOK_ROOT / "web" / "dist"
# The one signal the page sends that no run reads: it removes whatever signal is in `control.json`.
_CONTINUE = "continue"

router = APIRouter(prefix="/api")
# How long the run list the runs, inbox and tags routes share is handed out again: under the page's 3-second refresh,
# so each refresh of every open page builds it once at most (DEFECTS 405)
LIST_MAX_AGE_SECONDS = 2.0
# The cookie the page's session travels in; the CLI and the terminal UI send the same token as a bearer
SESSION_COOKIE = "i2k_session"


def _resolve_folder(root: str,
                    name: str) -> Path:
    try:
        return run_status_service.resolve_run_folder(root, name)
    except UnknownRunFolderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _resolve_output_folder(output: str) -> Path | None:
    """A job item's run folder, sent by the page as its full path or as `output/<name>`, when it is a run folder under
    one of the run roots; else None (a new run makes it at its start)."""

    path = Path(output)
    name = path.name
    root = path.parent.name
    try:
        folder = run_status_service.resolve_run_folder(root, name)
    except UnknownRunFolderError:  # no folder yet: the run line writes the speed when it makes it
        return None

    return folder if not path.is_absolute() or folder.resolve() == path.resolve() else None


def _resolve_idle_folder(root: str,
                         name: str) -> Path:
    """The run folder, refused while a run writes into it: its recording is what that run reads."""

    folder = _resolve_folder(root, name)
    if run_status_service.list_live_pids(folder):
        raise HTTPException(status_code=409, detail=f"{root}/{name}: a run is writing into this folder; stop it first")

    return folder


def _describe_operation(result: OperationResult) -> dict:
    """A backend operation's answer as the page reads it."""

    described = {"exit_code": result.exit_code, "output": result.output}
    if result.records:
        described["records"] = result.records

    return described


def _describe_move(video: str,
                   moved_as: str = "",
                   error: str = "") -> dict:
    """One video's or run folder's move as the page reads it: the name it has now, or why it did not move."""

    return {"video": video, "moved_as": moved_as, "error": error}


def _describe_restore(name: str,
                      restored_as: str = "",
                      error: str = "") -> dict:
    """One trash or archive entry's move back as the page reads it: the name it has now, or why it did not move."""

    return {"name": name, "restored_as": restored_as, "error": error}


def _build_attachment_headers(filename: str) -> dict:
    """The header that makes a browser save the answer as `filename`, any letters kept (RFC 5987)."""

    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


@router.get("/settings")
def list_settings(profile: str = "") -> list[dict]:
    """Every setting with its value in force, the file that set it and its comment, read from the files now."""

    if profile and profile not in config.list_profile_names():
        raise HTTPException(status_code=404, detail=f"no profile {profile!r}")

    return settings_service.list_settings(profile)


@router.put("/settings")
def save_setting(body: SettingRequest) -> dict:
    """One setting saved in both default settings files or in a profile's file, refused with the reason when it would not load.
    A run already going keeps its values; the next job reads the files at its start."""

    result = settings_service.save_setting(key=body.key,
                                           new_value=body.new_value,
                                           profile=body.profile,
                                           key_parts=body.key_parts)

    return _describe_operation(result)


@router.post("/settings/remove")
def remove_setting(body: SettingRemoveRequest) -> dict:
    """One entry (a model, a provider set, a host) taken out of the owner's own settings file or a profile's file, refused
    with the reason when it is shipped or the settings would not load without it."""

    return _describe_operation(settings_service.remove_setting(key_parts=body.key_parts, profile=body.profile))


def _find_account_name(request: Request) -> str:
    """The signed-in account's name, or "" when nobody signs in."""

    account = getattr(request.state, "account", None)

    return str(account.get("name", "")) if account else ""


@router.get("/instructions")
def list_instructions() -> dict:
    """Every workflow with its phases and steps, the files each step's worker is built from, and every instruction
    file with whether a person's edit is in force."""

    return instruction_catalogue_service.build_catalogue()


@router.get("/instructions/file")
def read_instruction(id: str) -> dict:
    """One instruction file: its default, the text in force, whether it is edited, and the steps that send it."""

    try:
        return instruction_catalogue_service.describe_file(id)
    except UnknownInstructionFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/instructions/file")
def save_instruction(body: InstructionSaveRequest,
                     request: Request) -> dict:
    """A person's text put in force for one file, kept as a version, or refused with every reason the program could
    not send it. A running run reads it at its next call."""

    try:
        saved = instruction_edit_service.save_instruction(body.id, body.text, account=_find_account_name(request),
                                                          note=body.note, kind=body.kind)
    except UnknownInstructionFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InstructionRefusedError as exc:
        return {"is_saved": False, "problems": str(exc).splitlines()}

    return {"is_saved": True, "problems": [], **saved}


@router.post("/instructions/reset")
def reset_instruction(body: InstructionResetRequest,
                      request: Request) -> dict:
    """One file's default put back in force; its edit stays a version."""

    try:
        return instruction_edit_service.reset_instruction(body.id, account=_find_account_name(request))
    except UnknownInstructionFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/instructions/versions")
def list_instruction_versions(id: str) -> list[dict]:
    """Every version of one file, newest first."""

    try:
        return instruction_store_service.list_versions(id)
    except UnknownInstructionFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/instructions/version")
def read_instruction_version(id: str,
                             at: str) -> dict:
    """One version's text and the default it was made over."""

    try:
        return instruction_store_service.read_version(id, at)
    except UnknownInstructionFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/instructions/preview")
def build_instruction_preview(body: InstructionPreviewRequest) -> dict:
    """The full system prompt one step's worker would get, with an unsaved text in place of one file when given."""

    try:
        return {"text": instruction_edit_service.build_preview(body.step, body.workflow, body.id, body.text), "error": ""}
    except UnknownInstructionFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # A draft that cannot be built is shown with why, the way the save would refuse it
    except PromptBuildError as exc:
        return {"text": "", "error": str(exc)}


@router.get("/profiles")
def list_profiles() -> dict:
    """The settings profiles a job may name, and the one the server itself runs on (empty: the default settings)."""

    return {"profiles": config.list_profile_names(), "server_profile": config.PROFILE_NAME}


@router.get("/phase-models")
def list_phase_models() -> dict:
    """The configured models a run may give a phase, and each phase that calls a model with the one it uses when the
    run names none."""

    return {"models": [{"name": name, "spec": model.spec} for name, model in config.settings.models.items()],
            "phases": [{"phase": phase, "number": output_contract.PHASE_NUMBERS[phase],
                        "title": phase_guide.PHASE_TITLES.get(output_contract.PHASE_NUMBERS[phase],
                                                              phase.replace("_", " ")),
                        "default": config.settings.resolve_phase_model_name(phase)}
                       for phase in config.PHASE_DEFAULT_MODELS]}


@router.get("/provider-sets")
def list_provider_sets() -> dict:
    """The provider sets a run can be on, read from the settings files now: `default` (the settings as they are) first,
    then each set the files name, with the settings it changes and the machine it runs on; and the set a run with no
    choice is on."""

    merged = config.read_layers(profile_name=config.PROFILE_NAME, provider_set_name=config.DEFAULT_PROVIDER_SET_NAME)
    named = merged.get("providerSets") or {}
    label = config.PROVIDER_SET_LABEL_KEY
    default_set = {"name": config.DEFAULT_PROVIDER_SET_NAME, "changes": [], "machine": config.read_set_machine(merged)}
    # A set names its own machine, never the default's: with none of its own it is the provider its job runs on
    named_set_list = [{"name": name, "changes": sorted(key for key in values if not key.startswith("_") and key != label),
                       "machine": config.read_set_machine({**config.merge_settings(merged, values), label: values.get(label, "")})}
                      for name, values in sorted(named.items()) if not name.startswith("_")]

    return {"default_set": merged.get("defaultProviderSet", config.DEFAULT_PROVIDER_SET_NAME),
            "sets": [default_set, *named_set_list]}


def _check_provider_set_name(provider_set: str) -> None:
    """400 naming the sets that exist when `provider_set` is none of them; an empty name is no choice and passes."""

    if not provider_set:
        return
    known = [entry["name"] for entry in list_provider_sets()["sets"]]
    if provider_set not in known:
        raise HTTPException(status_code=400, detail=f"no provider set {provider_set!r}: the provider sets are " + ", ".join(known))


def _find_job_holding_folder(folder: Path) -> RunJob | None:
    """A job of the queue that is waiting, running, waiting for its retry or holding the queue in `folder`."""

    root = folder.resolve()
    return next((job for job in job_queue_service.list_jobs(job_queue_service.QUEUE_DIR)
                 if job.status in (run_job.JOB_QUEUED, run_job.JOB_RUNNING, run_job.JOB_RETRYING, run_job.JOB_BLOCKED)
                 and Path(job.request.output_root).resolve() == root), None)


def _check_provider_set_free_to_choose(items: list[JobItemRequest],
                                     provider_set: str) -> None:
    """409 when a run asked to be queued on `provider_set` is already held by a job of the queue on another set: queued
    twice with two sets, it would run on both or on neither."""

    if not provider_set:
        return
    default_set = job_queue_service.read_default_provider_set()
    for item in items:
        held = _find_job_holding_folder(Path(item.output))
        if held is None:
            continue
        held_set = job_queue_service.read_job_provider_set(held, default_set)
        if held_set != provider_set:
            raise HTTPException(status_code=409, detail=f"{Path(item.output).name} is already queued on provider set "
                                                        f"{held_set!r}: cancel that job first or queue it on {held_set!r}")


@router.post("/runs/{root}/{name}/provider-set")
def set_run_provider_set(root: str,
                       name: str,
                       body: ProviderSetRequest) -> dict:
    """Give a run that is not running another provider set; its next phases go on with it. 409 while a job of the run
    waits, runs or holds the queue, since that job is already on a set."""

    _check_provider_set_name(body.provider_set)
    folder = _resolve_folder(root, name)
    held = _find_job_holding_folder(folder)
    if held is not None:
        raise HTTPException(status_code=409, detail=f"{name} has a {held.status} job: cancel it or let it end before "
                                                    "choosing another provider set")
    name_in_force = body.provider_set or job_queue_service.read_default_provider_set()
    run_provider_set_service.write_chosen_provider_set(folder, name_in_force)

    return {"provider_set": name_in_force}


@router.post("/runs/{root}/{name}/speed")
def set_run_speed(root: str,
                  name: str,
                  body: RunSpeedRequest) -> dict:
    """Keep the Workflow speed the page's chip chose in the run's folder at once, so it shows after a reload and the
    run's next start takes it. A run that works goes on as it started: it reads its speed only at its start, and its
    workflow file, which it reads while it works, is left as it is until then."""

    folder = _resolve_folder(root, name)
    try:
        speed = run_speed_service.resolve_speed(body.speed_preset, body.speed_settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run_speed_service.write_chosen_speed(folder, speed, is_workflow_kept=not run_status_service.list_live_pids(folder))

    return speed


@router.get("/run-defaults")
def read_run_defaults(profile: str = "",
                      provider_set: str = "") -> dict:
    """What a run started now takes for each choice the run form leaves untouched, read from the files now: the
    default settings with `profile` on top, or with the server's own profile when the run names none, and the
    `provider_set`'s changes on top of those."""

    if profile and profile not in config.list_profile_names():
        raise HTTPException(status_code=404, detail=f"no profile {profile!r}")
    _check_provider_set_name(provider_set)
    next_job_settings = settings_service.read_next_job_settings(profile or config.PROFILE_NAME, provider_set)

    return {"pdf_mode": next_job_settings.pdf.default_mode,
            # under "by-size", the page counts that pick a PDF's default: at most the first, pages as images; over
            # the second, the markdown conversion; text only between
            "pdf_images_page_limit": next_job_settings.pdf.images_page_limit,
            "pdf_model_writing_page_limit": next_job_settings.pdf.model_writing_page_limit,
            "pdf_ocr_engine": next_job_settings.pdf.ocr_engine,
            "knowledge_filter_model": next_job_settings.frames.knowledge_filter_model,
            "workflow": next_job_settings.video.workflow,
            # the Workflow speed menu: its settings, its presets, and the preset the settings in force hold
            "speed": run_speed_service.describe_speed_options(next_job_settings),
            # the extra wishes each LLM pass was last given, which the run form loads into its box
            "document_wishes": {"cleanup": next_job_settings.pdf.cleanup_wishes,
                                "rewrite": next_job_settings.pdf.rewrite_wishes,
                                "knowledge": next_job_settings.pdf.knowledge_wishes}}


@router.get("/ingesting-profiles")
def list_ingesting_profiles() -> dict:
    """The ingesting profiles a run's document can be written in, each with the tags that pick it, the default first."""

    return {"profiles": ingesting_profile_service.list_profiles()}


@router.get("/lists")
def list_run_lists() -> dict:
    """The person's own lists of the run list, each with its rows."""

    return {"lists": run_lists_service.read_lists()}


@router.put("/lists")
def save_run_lists(body: RunListsRequest) -> dict:
    """Every list saved whole: made, renamed, removed or filled by a drop. A list with no name, or a row in two lists,
    is refused by name."""

    try:
        return {"lists": run_lists_service.save_lists([entry.model_dump() for entry in body.lists])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tags")
def list_tags() -> dict:
    """What the tag box suggests: every tag a run folder has, once each whatever its letter case, in name order (a tag
    written in two letter cases as most runs write it, the first in name order when as many write each), and the
    ingesting profiles with the tags that pick them."""

    spellings: dict[str, collections.Counter] = {}
    for run in run_status_service.list_run_folders(LIST_MAX_AGE_SECONDS):
        for tag in run.get("tags") or []:
            spellings.setdefault(tag.casefold(), collections.Counter())[tag] += 1
    chosen = [min(counts, key=lambda spelling: (-counts[spelling], spelling)) for counts in spellings.values()]

    return {"tags": sorted(chosen, key=str.casefold), "profiles": ingesting_profile_service.list_profiles()}


@router.get("/phases")
def list_phases(flow: Annotated[PhaseFlowRequest, Query()]) -> list[dict]:
    """The numbered phases, as `--phase`, `--from` and `--to` take them, each with what it does and whether its switch
    is on (`review.isOn`, `critiqueCollection.isOn`; a phase with no switch is always on). The switches are read from the playbook
    files now, not from the server's start, since the next queued job reads them at its own start. Given the run's
    flow, each phase is titled for it and a phase that flow never runs is off: an audio recording's (`medium` "audio",
    or the one-call profile `takeaway`) and a fast-processing video's review and critique collection; a PDF's (`medium`
    "pdf-pages" or "pdf-text-only") phases are titled by its pages or its figures. `flow` is read from the query's `medium`, `workflow`,
    `ingesting_profile`, `document_pass`, `review` and `critique_collection`."""

    medium = flow.medium
    workflow = flow.workflow
    ingesting_profile = flow.ingesting_profile
    document_pass = flow.document_pass
    review = flow.review
    critique_collection = flow.critique_collection
    next_job_settings = settings_service.read_next_job_settings(config.PROFILE_NAME)
    switches = {"review": next_job_settings.review.is_on,
                "critique_collection": next_job_settings.critique_collection.is_on}
    is_audio = medium == "audio" or ingesting_profile in ingesting_profile_vocabulary.ONE_CALL_PROFILES
    pdf_mode = {"pdf-pages": output_contract.PDF_MODE_PAGES, "pdf-text-only": output_contract.PDF_MODE_TEXT,
                output_contract.PDF_MARKDOWN_CONVERSION_MEDIUM: output_contract.PDF_MODE_MARKDOWN_CONVERSION}.get(medium, "")
    # a PDF read as text only takes the audio route from phase 4 on: no review, no critique collection
    off_phases = (set(output_contract.AUDIO_OFF_PHASES)
                  if is_audio or pdf_mode in output_contract.PDF_MODE_MEDIUMS else set())
    if pdf_mode == output_contract.PDF_MODE_MARKDOWN_CONVERSION:
        off_phases |= set(output_contract.MARKDOWN_CONVERSION_OFF_PHASES)
    if workflow in video_workflow_vocabulary.NO_CRITIC_WORKFLOWS and not is_audio and not pdf_mode:
        off_phases |= set(video_workflow_vocabulary.FAST_OFF_PHASES)
    # a video run's Workflow speed may switch the review or the critique collection off (Balanced does): the page sends
    # "off" for each its chosen speed switches off
    if not is_audio and not pdf_mode:
        for phase, value in (("review", review), ("critique_collection", critique_collection)):
            if value == "off":
                off_phases.add(output_contract.PHASE_NUMBERS[phase])
    # phase 7, document reshaping, runs only for a run that asks for a clean up, a rewrite or a knowledge reshaping
    if document_pass not in output_contract.DOCUMENT_PASSES:
        off_phases.add(output_contract.PHASE_NUMBERS[output_contract.DOCUMENT_RESHAPING_PHASE])

    return [{"number": number, "name": row["phase"], **phase_guide.PHASE_GUIDES.get(number, {}),
             **(run_status_service.describe_phase(number, is_audio, pdf_mode, document_pass, workflow)
                if medium or ingesting_profile or document_pass
                or workflow == video_workflow_vocabulary.DIRECT_DRAFT else {}),
             "is_on": switches.get(row["phase"], True) and number not in off_phases}
            for number, row in sorted(output_contract.PHASE_FILES.items())]


@router.get("/inbox")
def list_inbox(request: Request) -> list[dict]:
    """Every video in the inbox, with its run folder and whether its document exists; one whose run is in a project the
    account may not see is left out."""

    return [item for item in run_status_service.list_inbox_items(config.INBOX_DIR, config.DOCUMENT_ROOT,
                                                                   LIST_MAX_AGE_SECONDS)
            if not item.get("run_folder") or _is_run_visible(request, Path(str(item["run_folder"])).name)]


@router.get("/inbox/video")
def send_inbox_video(path: str,
                     part: int = 0) -> FileResponse:
    """One video the inbox lists (part `part` of a meeting in parts), streamed so a player can seek in it."""

    wanted = Path(path).resolve()
    if wanted not in {video.resolve() for video in inbox_service.list_inbox_videos(config.INBOX_DIR)}:
        raise HTTPException(status_code=404, detail=f"{Path(path).name} is not a recording in the inbox")

    return FileResponse(run_artifact_service.resolve_recording_part(wanted, part))


def _find_inbox_recording(path: str) -> Path:
    """The recording the inbox lists at `path`; a path it does not list is refused, so no other file is read."""

    wanted = Path(path).resolve()
    if wanted not in {video.resolve() for video in inbox_service.list_inbox_videos(config.INBOX_DIR)}:
        raise HTTPException(status_code=404, detail=f"{Path(path).name} is not a recording in the inbox")

    return wanted


@router.get("/inbox/preview/frame")
def send_frame_preview(path: str,
                       at: float) -> FileResponse:
    """A small frame of an inbox video at `at` seconds, for the run form's Range filmstrip."""

    recording = _find_inbox_recording(path)
    try:
        return FileResponse(input_preview_service.build_frame_preview(recording, max(at, 0.0)))
    except (AudioExtractionFailed, MissingMediaTool) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/inbox/preview/waveform")
def send_waveform(path: str,
                  bins: int = 400) -> dict:
    """The loudness of an inbox recording in `bins` stretches of its length, 0 to 1, for the Range waveform."""

    recording = _find_inbox_recording(path)
    try:
        return {"peaks": input_preview_service.compute_waveform(recording, bins)}
    except (AudioExtractionFailed, MissingMediaTool) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/inbox/preview/page")
def send_page_preview(path: str,
                      page: int) -> FileResponse:
    """A small picture of page `page` (from 1) of an inbox PDF, for the Range page strip."""

    recording = _find_inbox_recording(path)
    try:
        return FileResponse(input_preview_service.build_page_preview(recording, page))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/inbox/upload")
def save_uploads(files: list[UploadFile]) -> list[dict]:
    """Save each uploaded recording into the inbox; one refused file does not stop the others."""

    saved = []
    for upload in files:
        try:
            target = upload_service.save_upload(upload.file, config.INBOX_DIR, upload.filename or "")
            saved.append(upload_service.describe_upload(upload.filename, saved_as=target.name))
        except DuplicateRecordingError as exc:
            saved.append(upload_service.describe_upload(upload.filename, error=str(exc), duplicate=exc.duplicate))
        except (UploadRefusedError, OSError) as exc:
            saved.append(upload_service.describe_upload(upload.filename, error=str(exc)))

    return saved


def _move_out(videos: list[str],
              run_folders: list[str],
              place: str) -> list[dict]:
    moved = []
    for video in videos:
        try:
            target = inbox_move_service.move_out_of_inbox(config.INBOX_DIR, Path(video), place)
            moved.append(_describe_move(video, moved_as=target.name))
        except (UnknownInboxItemError, OSError) as exc:
            moved.append(_describe_move(video, error=str(exc)))
    for run_folder in run_folders:
        root_name, _, folder_name = run_folder.partition("/")
        try:
            folder = run_status_service.resolve_run_folder(root_name, folder_name)
        except UnknownRunFolderError as exc:
            moved.append(_describe_move(run_folder, error=str(exc)))
            continue
        # a run writing into the folder would go on writing at the old path and leave half a folder there
        if run_status_service.list_live_pids(folder):
            moved.append(_describe_move(run_folder, error=f"{run_folder} cannot move: a run is writing into it"))
            continue
        try:
            target = inbox_move_service.move_run_folder_out(config.INBOX_DIR, folder, place)
            moved.append(_describe_move(run_folder, moved_as=target.name))
        except OSError as exc:
            moved.append(_describe_move(run_folder, error=str(exc)))

    return moved


def _move_back(names: list[str],
               place: str) -> list[dict]:
    restored = []
    for name in names:
        try:
            target = inbox_move_service.move_back(config.INBOX_DIR, place, name)
            restored.append(_describe_restore(name, restored_as=target.name))
        except (UnknownInboxItemError, OSError) as exc:
            restored.append(_describe_restore(name, error=str(exc)))

    return restored


@router.post("/inbox/trash")
def move_inbox_to_trash(body: MoveRequest) -> list[dict]:
    """Move each named inbox video, and each named run folder whole, into `inbox/.trash/`; one that cannot be moved
    does not stop the others."""

    return _move_out(body.videos, body.run_folders, inbox_move_service.TRASH)


@router.post("/inbox/archive")
def move_inbox_to_archive(body: MoveRequest) -> list[dict]:
    """Move each named inbox video, and each named run folder whole, into `inbox/.archive/`; one that cannot be moved
    does not stop the others."""

    return _move_out(body.videos, body.run_folders, inbox_move_service.ARCHIVE)


@router.post("/inbox/upload-bundle")
def save_inbox_bundle(files: list[UploadFile],
                      paths: list[str] = Form(default=[])) -> dict:
    """Save several files, a folder (`paths` gives each file's path in it) or a zip into the inbox, each recording as
    its own: `{"added", "refused"}`. Recordings that are one meeting are merged afterwards (`/inbox/merge`)."""

    # A file with no path in the dropped folder (a single file, or a zip) is known by its own name
    folder_paths = [*paths, *[""] * (len(files) - len(paths))]
    originals = [folder_path or upload.filename or "" for folder_path, upload in zip(folder_paths, files)]
    try:
        return upload_bundle_service.save_upload_bundle(config.INBOX_DIR,
                                                        [(upload.file, original) for upload, original in zip(files, originals)])
    except UploadRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/inbox/merge")
def merge_inbox_recordings(body: MergeRequest) -> dict:
    """Two or more inbox recordings merged into one recording in parts: a folder of
    their own, their tags with it. A recording the inbox does not list is a 404, one that cannot be merged a 400."""

    videos = [Path(video) for video in body.videos]
    # a recording a job waits to read would be gone from where its job looks for it
    waiting = {job.request.video.resolve() for job in job_queue_service.list_jobs(job_queue_service.QUEUE_DIR)
               if job.status in ("queued", "running")}
    busy = next((video for video in videos if video.resolve() in waiting), None)
    if busy is not None:
        raise HTTPException(status_code=400, detail=f"{busy.name} has a job queued or running: it is not merged")
    try:
        folder = inbox_merge_service.merge_recordings(config.INBOX_DIR, videos, body.name)
    except UnknownInboxItemError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UploadRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The tags are kept by the path the inbox lists: each recording's go to the folder, once each
    try:
        tags = [tag for video in videos for tag in inbox_tags_service.read_video_tags(video)]
        for video in videos:
            inbox_tags_service.write_video_tags(video, [])
        if tags:
            inbox_tags_service.write_video_tags(config.INBOX_DIR / folder.name, tags)
    except UnreadableTagsError as exc:
        raise HTTPException(status_code=409, detail=f"merged into {folder.name}, but its tags were not kept: {exc}") from exc

    return {"video": str(config.INBOX_DIR / folder.name), "name": folder.name}


@router.put("/inbox/tags")
def save_inbox_tags(body: InboxTagsRequest) -> dict:
    """The tags of an inbox video before its first run, kept so its page shows them after a refresh; a video not in the
    inbox is refused by name."""

    video = Path(body.video)
    if video not in inbox_service.list_inbox_videos(config.INBOX_DIR):
        raise HTTPException(status_code=404, detail=f"{video.name} is not in the inbox")

    try:
        return {"video": str(video), "tags": inbox_tags_service.write_video_tags(video, body.tags)}
    except UnreadableTagsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/inbox/rename")
def rename_inbox_video(body: RenameRequest) -> dict:
    """Rename one inbox video, its tags given before its first run going with it; a name that is taken, holds a
    folder, or is no video is refused with the reason."""

    try:
        target = inbox_rename_service.rename_inbox_video(config.INBOX_DIR, Path(body.video), body.name)
    except UnknownInboxItemError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UploadRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The tags are kept by the path the inbox lists, never the resolved one the rename returns
    try:
        inbox_tags_service.move_video_tags(Path(body.video), Path(body.video).with_name(target.name))
    except UnreadableTagsError as exc:
        raise HTTPException(status_code=409,
                            detail=f"{target.name} was renamed, but its tags stay under the old name: {exc}") from exc

    return {"video": str(target), "name": target.name}


@router.get("/inbox/trash")
def list_trash() -> list[dict]:
    """What `inbox/.trash/` holds."""

    return inbox_move_service.list_moved(config.INBOX_DIR, inbox_move_service.TRASH)


@router.post("/inbox/trash/delete")
def delete_trash_entries(body: RestoreRequest) -> list[dict]:
    """Delete each named trash entry for good, confirmed in the page; one that cannot be deleted does not stop the
    others."""

    deleted = []
    for name in body.names:
        try:
            inbox_move_service.delete_from_trash(config.INBOX_DIR, name)
            deleted.append({"name": name, "error": ""})
        except (UnknownInboxItemError, OSError) as exc:
            deleted.append({"name": name, "error": str(exc)})

    return deleted


@router.post("/inbox/trash/restore")
def restore_from_trash(body: RestoreRequest) -> list[dict]:
    """Move each named trash entry back where it came from: a video into the inbox, a run folder into its root."""

    return _move_back(body.names, inbox_move_service.TRASH)


@router.get("/inbox/archive")
def list_archive() -> list[dict]:
    """What `inbox/.archive/` holds, each entry with the runs that name it and its state."""

    return run_status_service.list_archive_items(config.INBOX_DIR)


@router.post("/inbox/archive/restore")
def restore_from_archive(body: RestoreRequest) -> list[dict]:
    """Move each named archive entry back where it came from under the name it had there."""

    return _move_back(body.names, inbox_move_service.ARCHIVE)


@router.get("/runs")
def list_runs(request: Request) -> list[dict]:
    """Every run folder the account may see, the most recently changed first, each with its project."""

    return [{**run, "project": project_service.find_run_project(run["name"])}
            for run in run_status_service.list_run_folders(LIST_MAX_AGE_SECONDS) if _is_run_visible(request, run["name"])]


@router.get("/runs/{root}/{name}")
def describe_run(root: str,
                 name: str) -> dict:
    """One run folder: its phases, frames, newest log and live processes."""

    _resolve_folder(root, name)

    return run_status_service.describe_run_folder(root, name)


@router.get("/runs/{root}/{name}/batches")
def list_run_batches(root: str,
                     name: str) -> list[dict]:
    """Every phase 3 batch and follow-up of the folder: time, tokens, result and each refused answer with why."""

    return batch_history_service.list_batches(_resolve_folder(root, name))


@router.get("/runs/{root}/{name}/document-pass/parts/{number}")
def read_document_pass_part(root: str,
                            name: str,
                            number: int) -> dict:
    """Phase 7's part `number`: each step its worker took and what it was answered, and its thinking so far."""

    return document_pass_service.describe_part_activity(_resolve_folder(root, name), number, time.time())


@router.post("/runs/{root}/{name}/document-pass/parts/{number}/messages")
def write_to_document_pass_part(root: str,
                                name: str,
                                number: int,
                                body: PartMessageRequest) -> dict:
    """Keep what the person wrote for phase 7's part `number`, whose worker gets it before its next call."""

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="write something to send")
    folder = _resolve_folder(root, name)
    worker_message_service.queue_message(document_pass_service.build_part_messages_path(folder, number), body.model_dump())

    return document_pass_service.describe_part_activity(folder, number, time.time())


@router.get("/runs/{root}/{name}/phases/{number}/worker")
def read_phase_worker(root: str,
                      name: str,
                      number: int) -> dict:
    """The worker of phase `number` in any workflow: its step, its thinking so far, its steps and the person's messages."""

    return worker_live_service.describe_phase_worker(_resolve_folder(root, name), number, time.time())


@router.get("/runs/{root}/{name}/fast-sessions/{slug}")
def send_fast_session(root: str,
                      name: str,
                      slug: str) -> dict:
    """One phase 3 session of a fast-processing run: each frame's call with its thinking, its words and its note."""

    return worker_live_service.describe_fast_session(_resolve_folder(root, name), slug)


@router.post("/runs/{root}/{name}/phases/{number}/worker/messages")
def write_to_phase_worker(root: str,
                          name: str,
                          number: int,
                          body: PartMessageRequest) -> dict:
    """Keep what the person wrote for the worker of phase `number`, which gets it before its next call."""

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="write something to send")
    folder = _resolve_folder(root, name)
    worker_live_service.queue_phase_message(folder, number, body.model_dump())

    return worker_live_service.describe_phase_worker(folder, number, time.time())


@router.get("/runs/{root}/{name}/document-chat")
def read_document_chat(root: str,
                       name: str) -> dict:
    """The chat on the run's final document: the messages and answers, the worker's steps and thinking. A message left
    waiting with no worker (it ended as the message came) gets one here."""

    folder = _resolve_folder(root, name)
    if document_chat_service.has_waiting_message(folder):
        document_chat_service.start_worker(folder)

    return document_chat_service.describe_chat(folder, time.time())


@router.post("/runs/{root}/{name}/document-chat/messages")
def write_to_document_chat(root: str,
                           name: str,
                           body: PartMessageRequest) -> dict:
    """Keep what the person asks of the final document's worker, and start the worker when none runs. Refused while a
    run writes into the folder: its phase would write over the chat's edits."""

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="write something to send")
    folder = _resolve_folder(root, name)
    if run_reset_service.list_live_runs(folder):
        raise HTTPException(status_code=409, detail="a run is writing this document: chat once it has ended")
    document_chat_service.queue_chat_message(folder, body.text.strip())
    document_chat_service.start_worker(folder)

    return document_chat_service.describe_chat(folder, time.time())


@router.post("/runs/{root}/{name}/document-chat/start-again")
def start_document_chat_again(root: str,
                              name: str) -> dict:
    """A new conversation on the final document, the one before kept in the chat's folder."""

    folder = _resolve_folder(root, name)
    document_chat_service.set_conversation_aside(folder)

    return document_chat_service.describe_chat(folder, time.time())


@router.get("/runs/{root}/{name}/document-steps")
def list_run_document_steps(root: str,
                            name: str) -> list[dict]:
    """Every phase 4 step of the folder: stage, status, calls, time, tokens and each refused answer with why."""

    return document_step_history_service.list_document_steps(_resolve_folder(root, name))


@router.get("/runs/{root}/{name}/review-steps")
def list_run_review_steps(root: str,
                          name: str) -> list[dict]:
    """Every phase 5 step of the folder: status, calls, time, tokens and each rejection with why."""

    return document_step_history_service.list_review_steps(_resolve_folder(root, name))


@router.post("/runs/{root}/{name}/cuts/tidy")
def move_unused_run_cuts_aside(root: str,
                               name: str) -> dict:
    """The cuts the run folder's document never placed, moved into its `.trash/cuts/`, as `i2k tidy-cuts --yes` does."""

    return _describe_operation(run_operations_service.move_unused_cuts_aside(_resolve_folder(root, name), True))


@router.get("/runs/{root}/{name}/log")
def read_run_log(root: str,
                 name: str,
                 offset: int = 0) -> dict:
    """The newest console log of the folder from byte `offset`."""

    text, next_offset = run_status_service.read_log_text(
        run_status_service.find_newest_log(_resolve_folder(root, name)), offset)

    return {"text": text, "offset": next_offset}


@router.get("/runs/{root}/{name}/artifacts")
def list_run_artifacts(root: str,
                       name: str) -> list[dict]:
    """The files of the folder a person looks at, grouped: document, draft, notes, frame notes, frames, audio."""

    groups = run_artifact_service.list_artifacts(_resolve_folder(root, name))
    hidden = hidden_artifact_service.list_hidden(root, name)
    for group in groups:
        for item in group["items"]:
            item["is_hidden"] = item["path"] in hidden

    return groups


@router.post("/runs/{root}/{name}/zip")
def send_run_zip(root: str,
                 name: str,
                 body: ZipRequest) -> Response:
    """The chosen artifacts as one zip, named after the folder and the label (`<folder>-frames.zip`)."""

    data = run_artifact_service.build_files_zip(_resolve_folder(root, name), body.paths)
    # A label of only symbols keeps nothing, and the zip takes the request's own default label
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", body.label).strip("-") or ZipRequest.model_fields["label"].default
    filename = f"{name}-{label}.zip"

    return Response(data, media_type="application/zip", headers=_build_attachment_headers(filename))


@router.post("/runs/{root}/{name}/hidden")
def set_run_hidden(root: str,
                   name: str,
                   body: HiddenRequest) -> dict:
    """Hide the chosen artifacts from this folder's view for good, or show them again; the files are not touched."""

    _resolve_folder(root, name)

    return {"hidden": sorted(hidden_artifact_service.set_hidden(root, name, body.paths, body.is_hidden))}


RUN_FILE_CACHE = "no-cache"


@router.get("/runs/{root}/{name}/files/{relative:path}")
def send_run_file(root: str,
                  name: str,
                  relative: str) -> Response:
    """One artifact of the folder, as it is on disk; a document's images resolve against the same route. A markdown
    file's Mermaid diagrams are mended as they are sent, so a document written before a mistake could be mended still
    draws; the file on disk stays as the run wrote it."""

    # A run file changes in place (a tag written into a document, an edit, a run's next attempt): with no Cache-Control
    # the browser kept its own copy and showed the document without the tags written into it (the owner); "no-cache"
    # has it ask again each time, answered from its copy while the file is the same (its ETag)
    path = run_artifact_service.resolve_artifact(_resolve_folder(root, name), relative)
    if path.suffix == ".md":
        text = path.read_text(encoding="utf-8", errors="replace")
        mended = mermaid_text_utils.open_prose_fences(mermaid_text_utils.repair_fenced_blocks(text))
        if mended != text:
            return Response(mended, media_type="text/markdown; charset=utf-8", headers={"Cache-Control": RUN_FILE_CACHE})

    return FileResponse(path, headers={"Cache-Control": RUN_FILE_CACHE})


@router.put("/runs/{root}/{name}/files/{relative:path}")
def save_run_markdown(root: str,
                      name: str,
                      relative: str,
                      body: MarkdownWriteRequest) -> dict:
    """Save one markdown artifact of the folder as the page's editor left it. The bytes it replaces are kept in
    `.edits/<stamp>/<path>` first, so no edit loses what a run produced."""

    return artifact_edit_service.save_markdown(_resolve_folder(root, name), relative, body.markdown)


@router.get("/runs/{root}/{name}/versions/{relative:path}")
def list_markdown_versions(root: str,
                           name: str,
                           relative: str) -> list[dict]:
    """Every version of one markdown artifact the folder kept, newest first."""

    return artifact_edit_service.list_versions(_resolve_folder(root, name), relative)


@router.get("/runs/{root}/{name}/version/{relative:path}")
def send_markdown_version(root: str,
                          name: str,
                          relative: str,
                          at: str) -> dict:
    """One kept version's text, by the stamp the listing gives; putting it back is an ordinary save."""

    return {"at": at, "markdown": artifact_edit_service.read_version(_resolve_folder(root, name), relative, at)}


@router.get("/runs/{root}/{name}/recording")
def send_run_recording(root: str,
                       name: str,
                       part: int = 0) -> FileResponse:
    """The recording the folder's run record names (part `part` of a meeting in parts), streamed so a player can seek
    in it."""

    folder = _resolve_folder(root, name)
    video = run_status_service.describe_run_video(folder)
    recording = run_artifact_service.find_recording(folder, video)
    if recording is None:
        raise HTTPException(status_code=404, detail=f"the recording is not at {video or 'a recorded path'} any more "
                                                    "(moved to the trash, or renamed)")

    return FileResponse(run_artifact_service.resolve_recording_part(recording, part))


@router.get("/runs/{root}/{name}/transcript")
def send_run_transcript(root: str,
                        name: str) -> dict:
    """Phase 1's transcript line by line, each with its seconds, for the Recording tab's list and search."""

    return {"lines": run_artifact_service.read_transcript_lines(_resolve_folder(root, name))}


@router.get("/runs/{root}/{name}/worker")
def send_worker_steps(root: str,
                      name: str,
                      phase: int) -> dict:
    """Every step of one phase whose worker the run saved, each with its attempts."""

    return {"steps": worker_view_service.list_worker_steps(_resolve_folder(root, name), phase)}


@router.get("/runs/{root}/{name}/worker/attempt")
def send_worker_attempt(root: str,
                        name: str,
                        ref: str) -> dict:
    """What one attempt or follow-up was sent: its instructions, its input as text and frames, and its findings."""

    return worker_view_service.read_attempt(_resolve_folder(root, name), ref)


@router.get("/phase-instructions")
def send_phase_instructions(phase: int,
                            is_audio: bool = Query(False, alias="audio")) -> dict:
    """The instructions each step of a phase would get now in a video run, or an audio run with `audio`, from the
    current files, with no model call."""

    return {"instructions": worker_view_service.build_instruction_previews(phase, is_audio)}


@router.get("/runs/{root}/{name}/transcript/download")
def send_run_transcript_text(root: str,
                             name: str) -> Response:
    """The transcript as a text file to save, named after the folder (`<folder>-transcript.txt`)."""

    text = run_artifact_service.render_transcript_text(_resolve_folder(root, name))

    return Response(text, media_type="text/plain; charset=utf-8",
                    headers=_build_attachment_headers(f"{name}-transcript.txt"))


@router.get("/runs/{root}/{name}/download/{relative:path}")
def send_run_download(root: str,
                      name: str,
                      relative: str) -> Response:
    """One artifact as a download: a markdown document that links figures comes as a zip of it and them, anything
    else as the file itself."""

    folder = _resolve_folder(root, name)
    path = run_artifact_service.resolve_artifact(folder, relative)
    linked = run_artifact_service.list_linked_files(folder, path) if path.suffix.lower() == ".md" else []
    if not linked:
        return FileResponse(path, filename=path.name)

    return Response(run_artifact_service.build_document_zip(path, linked), media_type="application/zip",
                    headers=_build_attachment_headers(f"{path.stem}.zip"))


@router.get("/runs/{root}/{name}/document-versions/download")
def send_document_versions(root: str,
                           name: str,
                           number: int | None = None) -> Response:
    """One version of the published document (`number`, from 1: as phase 4 wrote it, then after each clean up or
    rewrite), or every version, as a zip with the figures they link."""

    data, filename = run_artifact_service.build_versions_download(_resolve_folder(root, name), number)

    return Response(data, media_type="application/zip", headers=_build_attachment_headers(filename))


@router.post("/runs/{root}/{name}/control")
def send_control(root: str,
                 name: str,
                 body: ControlRequest) -> dict:
    """Write the signal into the newest run's `control.json`, where the run reads it; `continue` removes it."""

    run_dir = run_status_service.find_newest_run_dir(_resolve_folder(root, name))
    if run_dir is None:
        raise HTTPException(status_code=409, detail=f"{root}/{name} has no run record to send a signal to")
    if body.signal == _CONTINUE:
        control_signal_service.clear(run_dir)
    else:
        try:
            control_signal_service.request(run_dir, body.signal, by="ui")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"signal": control_signal_service.read_signal(run_dir)}


@router.post("/runs/{root}/{name}/reset")
def reset_run(root: str,
              name: str,
              body: ResetRequest) -> dict:
    """`reset-to N` or `reset-chapter N` on the folder, the same backend operation the command runs: a listing, or with
    `is_confirmed` the removal."""

    folder = _resolve_folder(root, name)
    if body.kind == "phase":
        result = run_operations_service.reset_run_to_phase(folder, body.number, body.is_confirmed)
    elif body.kind == "chapter":
        result = run_operations_service.reset_run_from_chapter(folder, body.number, body.is_confirmed, body.is_text_only)
    else:
        raise HTTPException(status_code=400, detail=f"kind is phase or chapter, not {body.kind!r}")

    return _describe_operation(result)


@router.post("/runs/{root}/{name}/frames/refine")
def list_frames_to_drop_for_page(root: str,
                                 name: str) -> dict:
    """The frame knowledge classifier's decision on every frame phase 3 has not read: a proposal, nothing changes."""

    return _describe_operation(run_operations_service.list_or_drop_proposed_frames(_resolve_folder(root, name)))


@router.post("/runs/{root}/{name}/frames/drop")
def drop_frames(root: str,
                name: str,
                body: FrameDropRequest) -> dict:
    """The frames named dropped from the run before phase 3 reads them, the same operation `i2k drop-frames` runs."""

    return _describe_operation(run_operations_service.drop_run_frames(_resolve_folder(root, name), body.frames, True,
                                                                      body.proposed, body.drop_scores))


@router.post("/runs/display-names")
def name_runs(body: RunNamesRequest) -> dict:
    """The ticked runs' folders renamed to their AI name, or back to their file name."""

    return _describe_operation(run_operations_service.name_runs([_resolve_folder(run.root, run.name) for run in body.runs],
                                                                body.is_by_topic))


@router.get("/videos/compression")
def list_videos_to_compress(request: Request) -> list[dict]:
    """Every done run's video the account may see, with its compression state (the Manager tab's Compress videos)."""

    return [row for row in batch_compression_service.list_videos() if _is_run_visible(request, row["name"])]


@router.post("/videos/compression")
def start_video_compression(body: RunRefsRequest) -> dict:
    """The ticked runs' videos compressed one after another in a process of its own; refused (409) while any
    compression runs, so two never run at once, or when a ticked video cannot be compressed."""

    folders = [_resolve_folder(run.root, run.name) for run in body.runs]
    refusal = batch_compression_service.find_batch_refusal(folders)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    return {"pid": batch_compression_service.start_batch(folders)}


@router.get("/download/parts")
def list_download_parts() -> list[dict]:
    """The parts of a run a batch download may hold, with the words the page shows for each."""

    return [{"key": key, "label": label} for key, label in batch_download_service.DOWNLOAD_PARTS.items()]


@router.post("/download/document-versions")
def list_document_versions(body: RunRefsRequest) -> list[dict]:
    """Each ticked run's document versions, so the page asks which version to download when one run has several."""

    return [{"root": run.root, "name": run.name,
             "versions": batch_download_service.list_versions(_resolve_folder(run.root, run.name))} for run in body.runs]


@router.post("/download/documents")
def send_documents_zip(body: BatchDownloadRequest) -> Response:
    """The ticked runs' finished documents with their resources folders, in one zip; refused (409) when no part is
    known or none of the runs has a document. The runs left out are named in the `X-Skipped-Runs` header."""

    unknown = [part for part in body.parts if part not in batch_download_service.DOWNLOAD_PARTS]
    if unknown or not body.parts:
        raise HTTPException(status_code=409, detail=f"nothing to download: unknown parts {unknown or body.parts}")
    data, skipped = batch_download_service.build_documents_zip([_resolve_folder(run.root, run.name) for run in body.runs],
                                                               body.version)
    if len(skipped) == len(body.runs):
        raise HTTPException(status_code=409, detail="none of the ticked runs has a finished document")
    headers = {**_build_attachment_headers(f"documents-{len(body.runs) - len(skipped)}.zip"),
               "X-Skipped-Runs": quote(", ".join(skipped))}

    return Response(data, media_type="application/zip", headers=headers)


@router.put("/runs/{root}/{name}/display-name")
def set_run_display_name(root: str,
                         name: str,
                         body: RunDisplayNameRequest) -> dict:
    """The run shown under the name sent, from the pencil beside its title; nothing is renamed on disk."""

    return _describe_operation(run_operations_service.set_run_display_name(_resolve_folder(root, name), body.name))


@router.put("/runs/{root}/{name}/tags")
def set_run_tags(root: str,
                 name: str,
                 body: RunTagsRequest) -> dict:
    """`tags --set` on the folder, as the command line runs it: the run's tags become the list sent."""

    return _describe_operation(run_operations_service.set_run_tags(_resolve_folder(root, name), [], [], body.tags))


@router.post("/runs/{root}/{name}/convert")
def convert_run(root: str,
                name: str,
                body: ConvertRequest) -> dict:
    """The run converted to another ingesting profile: phases 1 to 3 copied into a new run folder beside it, as
    `i2k convert` does, and that folder's later phases queued from the phase it starts at.
    The run is only read."""

    folder = _resolve_folder(root, name)
    result = (run_operations_service.write_profile_in_place(folder, body.profile) if body.is_in_place
              else run_operations_service.copy_run_for_profile(folder, body.profile))
    described = _describe_operation(result)
    if result.exit_code == 0:
        copied = result.records[0]
        request = RunJobRequest(video=Path(copied["video"]), output_root=Path(copied["folder"]),
                                from_phase=int(copied["fromPhase"]), is_forced=True, purpose=copied["purpose"],
                                provider_set=run_provider_set_service.read_chosen_provider_set(folder) or "")
        described["jobs"] = [job.as_dict() for job in job_queue_service.queue_jobs(job_queue_service.QUEUE_DIR, [request])]

    return described


@router.post("/runs/{root}/{name}/branch")
def create_branch(root: str,
               name: str,
               body: BranchRequest) -> dict:
    """The run branched from a phase, as `i2k branch` does: a new run folder beside it with the
    phases before that one; nothing is queued, the page opens the new run. The run is only read."""

    return _describe_operation(run_operations_service.create_branch(_resolve_folder(root, name), body.phase,
                                                                          body.pdf_mode))


def _find_run_input(root: str,
                    name: str) -> Path:
    """The recording or PDF a run reads, kept in its folder or still where its record names it; 404 when it has none."""

    folder = _resolve_folder(root, name)
    recording = run_recording_service.find_run_recording(folder, run_status_service.load_newest_record(folder))
    if recording is None:
        raise HTTPException(status_code=404, detail=f"{name} keeps no recording and its record names none that exists")

    return recording


@router.get("/runs/{root}/{name}/input")
def describe_run_input(root: str,
                       name: str) -> dict:
    """What the run reads, for its Another part editor: its recording's length in seconds or its PDF's pages, and the
    part it processes now (each field empty for the whole input)."""

    recording = _find_run_input(root, name)
    is_pdf = pdf_source_service.is_pdf(recording)
    try:
        duration_s = (0.0 if inbox_service.is_document(recording)
                      else round(sum(recording_parts_service.list_part_durations(recording)), 1))
    except (AudioExtractionFailed, MissingMediaTool):
        duration_s = 0.0
    input_range = input_range_service.read_range(folder=_resolve_folder(root, name))
    pages = (f"{input_range.first_page}-{input_range.last_page}"
             if input_range is not None and input_range.is_pages else "")
    start = (input_range_service.format_time(seconds=input_range.start_s)
             if input_range is not None and input_range.start_s is not None else "")
    end = (input_range_service.format_time(seconds=input_range.end_s)
           if input_range is not None and input_range.end_s is not None else "")

    return {"recording": recording.name, "is_audio": inbox_service.is_audio_recording(recording),
            "duration_s": duration_s, "page_count": pdf_source_service.count_pages(recording) if is_pdf else 0,
            "range_start": start, "range_end": end, "range_pages": pages}


@router.get("/runs/{root}/{name}/preview/frame")
def send_run_frame_preview(root: str,
                           name: str,
                           at: float) -> FileResponse:
    """A small frame of a run's video at `at` seconds, for its Another part filmstrip."""

    try:
        return FileResponse(input_preview_service.build_frame_preview(_find_run_input(root, name), max(at, 0.0)))
    except (AudioExtractionFailed, MissingMediaTool) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/runs/{root}/{name}/preview/waveform")
def send_run_waveform(root: str,
                      name: str,
                      bins: int = 400) -> dict:
    """The loudness of a run's recording in `bins` stretches, for its Another part waveform."""

    try:
        return {"peaks": input_preview_service.compute_waveform(_find_run_input(root, name), bins)}
    except (AudioExtractionFailed, MissingMediaTool) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/runs/{root}/{name}/preview/page")
def send_run_page_preview(root: str,
                          name: str,
                          page: int) -> FileResponse:
    """A small picture of page `page` of a run's PDF, for its Another part page strip."""

    try:
        return FileResponse(input_preview_service.build_page_preview(_find_run_input(root, name), page))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{root}/{name}/part")
def run_another_part(root: str,
                     name: str,
                     body: PartRunRequest) -> dict:
    """Another part of the run's input processed: branched from phase 1 into a new run folder
    beside it, the part written there, and its run queued from phase 1. The run is only read."""

    try:
        input_range = input_range_service.build_range(start=body.range_start, end=body.range_end, pages=body.range_pages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    part = input_range or InputRangeSpec()
    result = run_operations_service.create_branch(_resolve_folder(root, name), 1, input_range=part)
    described = _describe_operation(result)
    if result.exit_code == 0 and result.records[0]["video"]:
        branch = result.records[0]
        words = input_range_service.describe_range(input_range=input_range) if input_range is not None else "the whole input"
        # Past the processed-once check: the branch is a copy of a run of the same recording, run again on purpose
        request = RunJobRequest(video=Path(branch["video"]), output_root=Path(branch["folder"]), from_phase=1,
                                is_forced=True, purpose=f"{words} · phases 1 on",
                                provider_set=run_provider_set_service.read_chosen_provider_set(_resolve_folder(root, name)) or "")
        described["jobs"] = [job.as_dict() for job in job_queue_service.queue_jobs(job_queue_service.QUEUE_DIR, [request])]

    return described


@router.post("/runs/{root}/{name}/recording/to-inbox")
def move_recording_to_inbox(root: str,
                            name: str) -> dict:
    """`recording-to-inbox` on the folder, as the command line runs it: the recording a done run keeps goes back to the
    inbox, so phases 1 and 2 can run again."""

    result = run_operations_service.move_recording_to_inbox(_resolve_folder(root, name))

    return _describe_operation(result)


@router.post("/runs/{root}/{name}/recording/to-run")
def move_recording_to_run(root: str,
                          name: str) -> dict:
    """`recording-to-run` on the folder: a done run's inbox recording is kept in its folder."""

    result = run_operations_service.keep_recording_in_run(_resolve_folder(root, name))

    return _describe_operation(result)


@router.post("/runs/{root}/{name}/recording/compress")
def start_recording_compression(root: str,
                                name: str) -> dict:
    """Start compressing the kept recording in a process of its own session, since re-encoding a long meeting takes
    minutes: it outlives the request and the server, and writes its progress into `recording/.compression.json`, which
    the run's status reads."""

    folder = _resolve_folder(root, name)
    refusal = run_operations_service.find_compression_refusal(folder)
    if refusal is not None:
        raise HTTPException(status_code=409, detail=refusal.output)

    return {"pid": run_operations_service.start_compression_process(folder)}


@router.post("/runs/{root}/{name}/recording/delete")
def delete_recording(root: str,
                     name: str) -> dict:
    """Move the run's recording, from the inbox or from the run folder, into `inbox/.trash/`, where Restore brings it
    back into the inbox."""

    folder = _resolve_idle_folder(root, name)
    video = run_status_service.describe_run_video(folder)
    recording = run_artifact_service.find_recording(folder, video)
    if recording is None:
        raise HTTPException(status_code=404, detail=f"{root}/{name} has no recording to delete")
    try:
        trashed = run_recording_service.move_recording_to_trash(config.INBOX_DIR, folder, recording)
    except UnknownInboxItemError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"moved_as": trashed.name}


@router.post("/runs/{root}/{name}/recording/upload")
def save_recording_upload(root: str,
                          name: str,
                          file: UploadFile) -> dict:
    """Save an uploaded file as the run's missing recording, under the inbox path its run record names; a done run
    keeps it in its folder at once."""

    folder = _resolve_idle_folder(root, name)
    try:
        saved = run_recording_service.save_uploaded_recording(file.file, file.filename or "", config.INBOX_DIR, folder,
                                                              run_status_service.load_newest_record(folder))
    except UploadRefusedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"saved_as": str(saved)}


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict]:
    """The queue, oldest first, each job saying whether its run folder went missing while it ran, and each job that may
    still run naming the provider set it runs on (its lane, which the queue groups by); a job of a run the account may not
    see is left out."""

    default_set = job_queue_service.read_default_provider_set()
    return [{**job.as_dict(), "is_output_missing": job_queue_service.is_run_folder_missing(job_queue_service.QUEUE_DIR, job),
             "tries": job_queue_service.count_tries(job),
             "lane": job_queue_service.read_job_lane(job, default_set) if job.status in (
                 run_job.JOB_QUEUED, run_job.JOB_RUNNING, run_job.JOB_RETRYING, run_job.JOB_BLOCKED) else ""}
            for job in job_queue_service.list_jobs(job_queue_service.QUEUE_DIR)
            if _is_run_visible(request, Path(str(job.as_dict()["request"].get("output_root") or "")).name)]


@router.post("/jobs")
def queue_jobs(body: StartJobsRequest,
               request: Request) -> list[dict]:
    """Queue one `run` per item, in the order given. A settings profile with no file, or an ingesting profile not in the
    list, is refused by name, as `run` would refuse it at its start; so is a run whose project gives the account no
    operator's role."""

    account = getattr(request.state, "account", None)
    for item in body.items if account is not None else []:
        role = project_service.find_role(account, project_service.find_run_project(Path(item.output).name))
        if role is None or not route_role_utils.is_role_allowed(role, account_role_vocabulary.OPERATOR):
            raise HTTPException(status_code=403, detail=f"{Path(item.output).name}: your role in its project may not run it")

    if body.profile and body.profile not in config.list_profile_names():
        raise HTTPException(status_code=400, detail=f"no profile {body.profile!r}: {config.build_profile_path(body.profile).name} is not on disk")
    if body.ingesting_profile and body.ingesting_profile not in ingesting_profile_service.list_profile_names():
        raise HTTPException(status_code=400, detail=f"no ingesting profile {body.ingesting_profile!r}: the profiles are "
                                                    + ", ".join(ingesting_profile_service.list_profile_names()))
    if config.IS_CONTAINER_MODE and not body.is_dry_run:
        # in a container the page has no wizard to say what is missing: a run with no provider is refused by name
        missing_providers = setup_check_service.list_missing_providers()
        if missing_providers:
            lines = [f"{row['name']} (called by {', '.join(row['used_by'])}): {row['reason']}" for row in missing_providers]
            raise HTTPException(status_code=409, detail="a run needs a model provider that is not set up: " + "; ".join(lines))
    _check_provider_set_name(body.provider_set)
    _check_provider_set_free_to_choose(body.items, body.provider_set)
    for phase, name in body.phase_models.items():
        try:
            config.check_phase_model(phase, name, config.settings.models)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"no model {name!r} for phase {phase!r}: {exc}") from exc
    try:
        input_range_service.build_range(start=body.range_start, end=body.range_end, pages=body.range_pages)
        if body.speed_preset:
            run_speed_service.resolve_speed(body.speed_preset, body.speed_settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # each changed value as the run line takes it, a switch as true or false
    speed_pairs = tuple(f"{key}={str(value).lower() if isinstance(value, bool) else value}"
                        for key, value in body.speed_settings.items())
    requests = [RunJobRequest(video=Path(item.video), output_root=Path(item.output), only_phase=body.only_phase,
                              from_phase=body.from_phase, to_phase=body.to_phase, worker_indexes=tuple(body.worker_indexes),
                              is_resuming=body.is_resuming, is_dry_run=body.is_dry_run, profile=body.profile,
                              tags=tuple(body.tags), ingesting_profile=body.ingesting_profile, is_run_as_audio=body.is_run_as_audio,
                              pdf_mode=body.pdf_mode, pdf_ocr_engine=body.pdf_ocr_engine,
                              document_pass=body.document_pass, document_wishes=body.document_wishes,
                              document_source=body.document_source, knowledge_filter_model=body.knowledge_filter_model,
                              range_start=body.range_start, range_end=body.range_end, range_pages=body.range_pages,
                              phase_models=tuple(f"{phase}={name}" for phase, name in body.phase_models.items()),
                              workflow=body.workflow, provider_set=body.provider_set, speed_preset=body.speed_preset,
                              speed_settings=speed_pairs)
                for item in body.items]
    _save_document_wishes(body.document_pass, body.document_wishes)
    # a run folder that exists keeps its new speed at once, so its page names the speed it will run at while it waits
    # (a queued run's pill read "Fast" until its job started)
    for item in body.items if body.speed_preset else []:
        folder = _resolve_output_folder(item.output)
        if folder is not None and folder.is_dir():
            run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed(body.speed_preset,
                                                                                         body.speed_settings))

    # a run folder that exists keeps its set at once, so its page names the set it waits on
    for item in body.items if body.provider_set else []:
        folder = _resolve_output_folder(item.output)
        if folder is not None and folder.is_dir():
            run_provider_set_service.write_chosen_provider_set(folder, body.provider_set)

    return [job.as_dict() for job in job_queue_service.queue_jobs(job_queue_service.QUEUE_DIR, requests)]


def _save_document_wishes(document_pass: str,
                          wishes: str) -> None:
    """The wishes a run was queued with for an LLM clean up, rewrite or knowledge pass, saved as that pass's setting
    (`pdf.cleanupWishes`, `pdf.rewriteWishes`, `pdf.knowledgeWishes`) when they changed, so the next run form loads them (the owner: each
    pass keeps its own last wishes). Saved in the default settings files, where the Settings page shows them."""

    if document_pass not in output_contract.DOCUMENT_PASSES:
        return
    key = f"pdf.{document_pass}Wishes"
    saved = getattr(settings_service.read_next_job_settings().pdf, f"{document_pass}_wishes")
    if wishes.strip() != saved:
        settings_service.save_setting(key, wishes.strip())


@router.get("/queue/pause")
def read_queue_pause() -> dict:
    """When the paused queue starts its next job again, and whether it waits to be resumed; both empty when not paused."""

    return job_queue_service.read_pause(job_queue_service.QUEUE_DIR)


@router.post("/queue/pause")
def pause_queue(body: QueuePauseRequest) -> dict:
    """No queued job starts for the minutes given, or until resumed with no minutes; a running job goes on."""

    try:
        return job_queue_service.pause_queue(job_queue_service.QUEUE_DIR, body.minutes, body.is_stopping_now)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/queue/resume")
def resume_queue() -> dict:
    """End the queue's pause now."""

    job_queue_service.resume_queue(job_queue_service.QUEUE_DIR)

    return job_queue_service.read_pause(job_queue_service.QUEUE_DIR)


@router.post("/jobs/clear")
def clear_ended_jobs() -> dict:
    """Take every job that is neither waiting nor running off the list."""

    return {"cleared": job_queue_service.clear_ended_jobs(job_queue_service.QUEUE_DIR)}


@router.post("/jobs/{job_id}/move")
def move_job(job_id: str,
             body: JobMoveRequest) -> dict:
    """Move a waiting job up (sooner) or down (later) in the queue."""

    return job_queue_service.move_job(job_queue_service.QUEUE_DIR, job_id, body.direction).as_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Take a queued job off the queue."""

    return job_queue_service.cancel_job(job_queue_service.QUEUE_DIR, job_id).as_dict()


@router.post("/jobs/{job_id}/on-failure")
def set_job_on_failure(job_id: str,
                       body: JobOnFailureRequest) -> dict:
    """Set what the queue does after this job's last failed try: go on to the next job, or hold the queue."""

    try:
        return job_queue_service.set_on_failure(job_queue_service.QUEUE_DIR, job_id, body.choice).as_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
def retry_blocked_job(job_id: str) -> dict:
    """Run a job that holds the queue once more, before any other job."""

    return job_queue_service.retry_blocked_job(job_queue_service.QUEUE_DIR, job_id).as_dict()


@router.post("/jobs/{job_id}/skip")
def skip_blocked_job(job_id: str) -> dict:
    """Let a job that holds the queue go, so the next job starts."""

    return job_queue_service.skip_blocked_job(job_queue_service.QUEUE_DIR, job_id).as_dict()


@router.post("/jobs/{job_id}/interrupt")
def send_job_interrupt(job_id: str) -> dict:
    """Interrupt a running job's process, as Ctrl-C would; the next job of its machine starts."""

    return job_queue_service.send_interrupt(job_queue_service.QUEUE_DIR, job_id).as_dict()


@router.post("/jobs/{job_id}/interrupt-and-pause")
def send_job_interrupt_and_pause(job_id: str) -> dict:
    """Interrupt a running job's process, as Ctrl-C would, and hold its machine's queue until it is retried or skipped."""

    return job_queue_service.send_interrupt(job_queue_service.QUEUE_DIR, job_id, is_holding=True).as_dict()


@router.get("/jobs/{job_id}/log")
def read_job_log(job_id: str,
                 offset: int = 0) -> dict:
    """Everything the job's process printed, from byte `offset`: a refused line names its reason here."""

    text, next_offset = run_status_service.read_log_text(
        job_queue_service.build_console_path(job_queue_service.QUEUE_DIR, job_id), offset)

    return {"text": text, "offset": next_offset}


@router.get("/models")
def describe_models() -> dict:
    """What `i2k models` prints, from the same backend operation."""

    result = run_operations_service.describe_models()

    return _describe_operation(result)


@router.get("/probe")
def probe_hosts() -> dict:
    """What `i2k probe` prints, from the same backend operation."""

    result = run_operations_service.probe_model_hosts()

    return _describe_operation(result)


@router.get("/setup")
def check_setup() -> dict:
    """What the setup wizard shows: each runtime a run needs (needed, installed, running), each model a phase calls
    (configured, on its host, downloaded) and the models download (`setup_check_service`). Also `is_container`, which
    the page reads to hide the wizard, and `missing_providers`, the models a run calls that have no reachable provider."""

    return setup_check_service.check_setup()


@router.post("/setup/configure")
def configure_models(request: ModelsConfigureRequest) -> dict:
    """The wizard's choice of host and model saved for every phase in both default settings files, a key in `.env`;
    `exit_code` 2 with the reason when refused."""

    result = setup_check_service.configure_models(request.provider, request.model_id, request.base_url,
                                                  request.api_key)

    return _describe_operation(result)


@router.post("/setup/models")
def start_models_download(request: ModelsDownloadRequest) -> dict:
    """Start `i2k setup-models` in a process of its own session; downloading the models takes minutes to hours.
    Refused in a container, where the models are mounted and nothing is set up from the page."""

    if config.IS_CONTAINER_MODE:
        raise HTTPException(status_code=409, detail=setup_check_service.describe_container_refusal("Downloading models"))
    if setup_check_service.check_setup()["download"]["is_running"]:
        # two downloads would write the same files at once
        raise HTTPException(status_code=409, detail="a models download is already running")

    return {"pid": setup_check_service.start_models_download(request.hf_token)}


def _read_run_update(root: str,
                     name: str,
                     offset: int) -> tuple[dict, int]:
    folder = run_status_service.resolve_run_folder(root, name)
    text, next_offset = run_status_service.read_log_text(run_status_service.find_newest_log(folder), offset)

    return {"run": run_status_service.describe_run_folder(root, name), "text": text}, next_offset


def _read_job_update(job_id: str,
                     offset: int) -> tuple[dict, int]:
    text, next_offset = run_status_service.read_log_text(
        job_queue_service.build_console_path(job_queue_service.QUEUE_DIR, job_id), offset)
    job = job_queue_service.find_job(job_queue_service.QUEUE_DIR, job_id)

    return {"job": {**job.as_dict(), "tries": job_queue_service.count_tries(job)}, "text": text}, next_offset


@router.get("/runs/{root}/{name}/update")
def read_run_update(root: str,
                    name: str,
                    offset: int = 0) -> dict:
    """The folder's state and its log text from `offset` on, with the offset to ask from next. One short answer the page
    asks for each second: an open event stream per detail view took every connection Firefox allows one server (6), and a
    recording's video waited for good."""

    _resolve_folder(root, name)
    update, next_offset = _read_run_update(root, name, offset)

    return {**update, "offset": next_offset}


@router.get("/jobs/{job_id}/update")
def read_job_update(job_id: str,
                    offset: int = 0) -> dict:
    """The job's state and its console text from `offset` on, with the offset to ask from next; a job not in the queue
    is a 404."""

    update, next_offset = _read_job_update(job_id, offset)

    return {**update, "offset": next_offset}


def _answer_not_found(request: Request,
                      exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


def _read_token(request: Request) -> str:
    """The session token a request carries: its bearer, else the page's cookie."""

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[len("bearer "):].strip()

    return request.cookies.get(SESSION_COOKIE, "")


@router.post("/auth/login")
def sign_in(body: SignInRequest) -> JSONResponse:
    token = account_service.sign_in(body.name, body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="wrong name or password")
    account = account_service.read_session(token) or {}
    answer = JSONResponse({**account, "token": token})
    answer.set_cookie(SESSION_COOKIE, token, max_age=account_service.SESSION_DAYS * 86_400, httponly=True,
                      samesite="lax")

    return answer


@router.post("/auth/logout")
def sign_out(request: Request) -> JSONResponse:
    account_service.sign_out(_read_token(request))
    answer = JSONResponse({"is_signed_out": True})
    answer.delete_cookie(SESSION_COOKIE)

    return answer


@router.get("/auth/me")
def read_signed_in(request: Request) -> dict:
    """Whether the page asks anyone to sign in (the setting on and an account existing), the account signed in, if any,
    with its groups, and the roles."""

    account = account_service.read_session(_read_token(request))
    if account is not None:
        account = {**account, "groups": group_service.list_account_groups(account["name"])}

    return {"has_accounts": account_service.has_accounts(), "account": account, "roles": list(account_role_vocabulary.ROLES),
            "is_sign_in_required": _is_sign_in_required()}


# Whether the server asks anyone to sign in, chosen when it starts (`i2k serve --sign-in on|off`, or `I2K_SIGN_IN`) and
# never from the page: off, nobody signs in and every route and project is open to all (the owner)
_is_sign_in_on = True


def set_sign_in(is_on: bool) -> None:
    """Sign-in on or off for this server, as it starts."""

    global _is_sign_in_on
    _is_sign_in_on = is_on


def _is_sign_in_required() -> bool:
    """Whether a request needs a session: sign-in on for this server and an account to sign in with."""

    return _is_sign_in_on and account_service.has_accounts()


def _answer_account_error(action) -> dict:
    try:
        action()
    except AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"accounts": account_service.list_accounts()}


@router.get("/accounts")
def list_accounts() -> dict:
    return {"accounts": account_service.list_accounts()}


def _answer_project_error(action) -> dict:
    try:
        action()
    except AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"projects": project_service.list_projects(), "groups": group_service.list_groups()}


@router.get("/projects")
def list_projects(request: Request) -> dict:
    """The projects the account may see, each with the role it holds there, and the groups; an admin, or a server with
    sign-in off, sees every project with its grants."""

    account = getattr(request.state, "account", None)
    projects = project_service.list_projects()
    if account is None or account["role"] == account_role_vocabulary.ADMIN:
        return {"projects": [{**project, "role": account_role_vocabulary.ADMIN if account else None} for project in projects],
                "groups": group_service.list_groups()}
    visible = [{"slug": project["slug"], "title": project["title"], "runs": project["runs"],
                "role": project_service.find_role(account, project["slug"])} for project in projects]

    return {"projects": [project for project in visible if project["role"] is not None], "groups": []}


@router.post("/projects")
def add_project(body: ProjectRequest) -> dict:
    return _answer_project_error(lambda: project_service.add_project(body.title))


@router.put("/projects/{slug}")
def rename_project(slug: str,
                   body: ProjectRequest) -> dict:
    return _answer_project_error(lambda: project_service.rename_project(slug, body.title))


@router.delete("/projects/{slug}")
def remove_project(slug: str) -> dict:
    return _answer_project_error(lambda: project_service.remove_project(slug))


@router.put("/projects/{slug}/members/{account}")
def set_project_member(slug: str,
                       account: str,
                       body: GrantRequest) -> dict:
    return _answer_project_error(lambda: project_service.set_member(slug, account, body.role or None))


@router.put("/projects/{slug}/groups/{group}")
def set_project_group(slug: str,
                      group: str,
                      body: GrantRequest) -> dict:
    return _answer_project_error(lambda: project_service.set_group_role(slug, group, body.role or None))


@router.put("/projects/{slug}/runs/{run}")
def move_run_to_project(slug: str,
                        run: str) -> dict:
    return _answer_project_error(lambda: project_service.move_run(run, slug))


@router.post("/groups")
def add_group(body: GroupRequest) -> dict:
    return _answer_project_error(lambda: group_service.add_group(body.name))


@router.put("/groups/{name}")
def set_group_accounts(name: str,
                       body: GroupAccountsRequest) -> dict:
    return _answer_project_error(lambda: group_service.set_group_accounts(name, body.accounts))


@router.delete("/groups/{name}")
def remove_group(name: str) -> dict:
    return _answer_project_error(lambda: group_service.remove_group(name))


@router.post("/accounts")
def add_account(body: AccountRequest) -> dict:
    return _answer_account_error(lambda: account_service.add_account(body.name, body.password, body.role))


@router.put("/accounts/{name}")
def update_account(name: str,
                   body: AccountChangeRequest) -> dict:
    def update() -> None:
        if body.role:
            account_service.set_role(name, body.role)
        if body.password:
            account_service.set_password(name, body.password)

    return _answer_account_error(update)


@router.delete("/accounts/{name}")
def remove_account(name: str) -> dict:
    return _answer_account_error(lambda: account_service.remove_account(name))


async def _check_account(request: Request,
                         call_next):
    """Every `/api` request, while sign-in is on and an account exists, by the role it needs: 401 with no live session,
    403 for a role below it. With sign-in off for this server or no account, every request goes through, as before
    accounts existed."""

    path = request.url.path
    needed = route_role_utils.find_needed_role(request.method, path)
    request.state.account = None
    if needed is not None and _is_sign_in_required():
        account = account_service.read_session(_read_token(request))
        if account is None:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        request.state.account = account
        role = _find_request_role(account, path)
        if role is None:
            return JSONResponse({"detail": "this run is in a project you are no member of"}, status_code=403)
        if not route_role_utils.is_role_allowed(role, needed):
            return JSONResponse({"detail": f"the {role} role may not do this: it needs {needed}"}, status_code=403)

    return await call_next(request)


def _find_request_role(account: dict,
                       path: str) -> str | None:
    """The role an account holds for a request: the role the run's project gives it on a route
    of one run, its own role on the accounts, projects, settings and setup, else its role in the default project,
    which holds the inbox and the queue. None where its run's project is closed to it."""

    run = route_role_utils.find_request_run(path)
    if run is not None:
        return project_service.find_role(account, project_service.find_run_project(run))
    if route_role_utils.is_admin_path(path):
        return account["role"]

    return project_service.find_role(account, project_service.DEFAULT_PROJECT)


def _is_run_visible(request: Request,
                    run: str) -> bool:
    """Whether the signed-in account may see the run folder named `run`; every run with no account at all."""

    account = getattr(request.state, "account", None)

    return account is None or project_service.find_role(account, project_service.find_run_project(run)) is not None


def _render_missing_page() -> HTMLResponse:
    return HTMLResponse("<p>The web page is not built. Run <code>npm install && npm run build</code> in "
                        "<code>web/</code>, then start <code>./i2k serve</code> again.</p>")


class _PageFiles(StaticFiles):
    """The built page, its HTML sent `Cache-Control: no-cache`: with no header a browser kept `index.html`, and with it
    the stylesheet and script of the build before, after the page was built again (a phone showed a fixed layout
    broken). The hashed assets keep the default: a new build names them anew."""

    async def get_response(self,
                           path: str,
                           scope: Any) -> Any:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = RUN_FILE_CACHE

        return response


async def _clear_run_list_on_change(request: Request,
                                    call_next):
    """A request that may change a run (anything but a read) has the shared run list built afresh after it, so the page
    shows its change at once rather than up to `LIST_MAX_AGE_SECONDS` later."""

    response = await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        run_status_service.clear_shared_run_list()

    return response


def build_app() -> FastAPI:
    """The API under `/api`, and the built page at `/` when `web/dist/` exists."""

    app = FastAPI(title=config.PLAYBOOK_NAME)
    app.middleware("http")(_check_account)
    app.middleware("http")(_clear_run_list_on_change)
    app.include_router(router)
    # An artifact or a job the request names that is not there is a 404 in every route, said in the error's words
    for missing in (UnknownArtifactError, UnknownJobError):
        app.add_exception_handler(missing, _answer_not_found)
    if WEB_DIST_DIR.is_dir():
        app.mount("/", _PageFiles(directory=WEB_DIST_DIR, html=True), name="web")
    else:
        app.add_api_route("/", _render_missing_page, methods=["GET"])

    return app
