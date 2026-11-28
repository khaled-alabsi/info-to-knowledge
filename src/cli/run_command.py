"""`run`: the only command that calls a model.

Exit codes are the contract a caller depends on: 0 every selected phase finished and every frame
reached a terminal status, 1 a phase failed or something is blocked, 2 the line could not be
honoured (a bad target, a contradictory phase selection, a model that lacks what its phase needs, a
resume with nothing to resume), 130 an operator stopped it.

A chain pipeline is pointed at ONE FILE, told where to write and told which numbered phases to run. The sequence is
marked step by step in `cmd_run` and `_run_selected_phases`.
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from pydantic.alias_generators import to_camel

import config
from agent_client.agent_errors import ControlStop
from graph import graph_assembly
from graph import graph_state
from graph import routing
from models import run_job
from models import ingesting_profile_vocabulary
from models import output_contract
from models import phase_guide
from prompts import system_prompt_builder
from models import video_workflow_vocabulary
from models.input_range_spec import InputRangeSpec
from models.phase_request import PhaseRequest
from models.pipeline_errors import EngineUnavailable, MissingModelCapability
from models.run_context import RunContext
from models.run_errors import BadTarget, ModelUnavailable
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from models.run_start_checks_result import RunStartChecksResult
from services import call_log_service
from services import chat_model_service
from services import cli_log_service
from services import control_signal_service
from services import cut_tidy_service
from services import decision_filter_service
from services import document_pass_service
from services import diarization_service
from services import frame_extraction_service
from services import host_window_service
from services import inbox_service
from services import ingesting_profile_service
from services import input_range_service
from services import ignore_list_service
from services import model_capability_service
from services import office_source_service
from services import pdf_default_mode_service
from services import pdf_source_service
from services import phase_file_service
from services import performance_log_service
from services import recording_hash_service
from services import recording_medium_service
from services import recording_parts_service
from services import run_branch_service
from services import run_paths_service
from services import run_recording_service
from services import run_resume_service
from services import run_store_service
from services import run_tags_service
from services import run_speed_service
from services import run_provider_set_service
from services import server_client_service
from services import slides_source_service
from services import text_detection_service
from services import transcription_service
from services import video_workflow_service
from utils import display_path_utils
from utils import frame_slug_utils
from utils import json_store_utils

__all__ = ["cmd_run", "describe_run_line", "describe_redundancy_model", "render_model_lines", "add_run_arguments", "add_run_switches", "resolve_paths", "find_duplicate_refusal",
           "build_phase_request", "read_phase_models", "add_given_tags", "write_chosen_profile", "write_chosen_medium",
           "write_chosen_workflow", "choose_speed"]

# the phases whose model a fast-processing run's settings name (`video.fastProcessing`), not `phaseModels`
_FAST_MODEL_PHASES = (config.FRAMES_PROCESSING_PHASE, config.FINALIZE_PHASE)
# The header's label column: as wide as its longest label, `frames_processing`, so every model line lines up
_HEADER_LABEL_WIDTH = 17
# The cuts a done run's last line names by name; the rest are counted
_CUTS_LISTED = 8


def _read_budgets() -> dict:
    """The budgets a run records at its start, a new run or a resume, from the settings as they are then."""

    budgets = config.settings.budgets

    return {"maxAttempts": budgets.max_attempts, "attemptTimeoutS": budgets.attempt_timeout_s}


def _render_setting_value(setting_value: object) -> str:
    """A switch as on or off, any other setting as it prints."""

    if isinstance(setting_value, bool):
        return "on" if setting_value else "off"

    return str(setting_value)


def _render_settings(section: str,
                     values: object,
                     fields: tuple[str, ...]) -> str:
    """`section.firstField=1 secondField=on`: each field by the name the playbook files give it, a switch as on or
    off."""

    rendered = [f"{to_camel(field)}={_render_setting_value(getattr(values, field))}" for field in fields]

    return f"{section}.{' '.join(rendered)}"


def _describe_repair_settings() -> str:
    """Every setting that gives a rejected attempt another chance, by the name the playbook files give it, with the
    value this run reads: a run judged by its log shows how much repair it had."""

    frames = _render_settings("framesProcessing", config.settings.frames_processing,
                              ("repair_calls", "single_frame_attempts", "is_critique_repair_on"))
    budgets = _render_settings("budgets", config.settings.budgets,
                               ("max_attempts", "ask_why_after_rejection", "chapter_dry_runs", "write_chapter_attempts",
                                "fail_on_frame_cut_fail"))

    return f"{frames} · {budgets}"


def _merge_known_phases(paths: RunPaths,
                        recorded: dict[str, str]) -> dict[str, str]:
    """The phases a run record holds, on top of the ones the folder's phase files show finished."""

    # A run begun at a later phase (`--from 4`) records only the phases it ran: the earlier ones the folder
    # holds are taken from their files, so a plain `--resume` goes on where the work stands and never runs
    # phase 1 again over a transcript that is there (it once re-made the transcript and emptied every frame's
    # notes). What the run itself recorded stands.
    return {**run_resume_service.describe_finished_phases(paths), **recorded}


def _read_known_phases(paths: RunPaths,
                       is_resuming: bool) -> dict[str, str]:
    """The phases a run about to start counts as done, read without writing anything: none for a new run."""

    if not is_resuming:
        return {}
    latest = run_store_service.RunStore.load_latest(paths.runs_root)

    return _merge_known_phases(paths, latest.data.get("phases", {}) if latest is not None else {})


def _describe_settings_read() -> dict:
    """What a run records of the settings it read: its budgets, its models, when this process read them and under
    which profile (the page says a running run when the files changed after)."""

    # A resume runs on the settings as they are now, its budgets included, as every other setting is read at the start
    # of the process (the owner: a run stopped and run again takes the new settings)
    return {"budgets": _read_budgets(), "models": chat_model_service.describe_phase_models(),
            "settingsReadAt": time.time(), "settingsProfile": config.PROFILE_NAME}


def _open_run(paths: RunPaths,
              is_resuming: bool,
              is_dry_run: bool,
              log: logging.Logger) -> run_store_service.RunStore:
    """The run this command drives: the newest one continued, or a new one minted. `--resume` is
    opt-in, and it refuses when there is nothing to resume."""

    # STEP 1: a resume of a run whose record is there continues it
    if is_resuming:
        store = run_store_service.RunStore.load_latest(paths.runs_root)
        if store is not None:
            store.data["phases"] = _merge_known_phases(paths, store.data.get("phases", {}))
            store.data.update(_describe_settings_read())
            store.mark_alive()

            return store

    # STEP 2: a resume with no run record adopts the phases the folder's files hold: `reset-to N` forgets the later
    # phases' progress, and a folder can outlive its logs. The resume then runs the first one it does not, never phase 1
    # again over a transcript that is already there.
    adopted = {}
    if is_resuming:
        adopted = run_resume_service.describe_finished_phases(paths)
        if not adopted:
            raise BadTarget(f"no run to resume under {display_path_utils.render_path(paths.runs_root)}, and no "
                            f"finished phase in {display_path_utils.render_path(paths.output_root)}. Drop --resume to "
                            f"start one.")

    # STEP 3: a new run record, holding the adopted phases when there are any
    run_id = run_store_service.new_run_id(is_dry_run=is_dry_run)
    settings_read = _describe_settings_read()
    store = run_store_service.RunStore.create(paths.run_state(run_id), RunIdentity.for_run(run_id, paths),
                                              budgets=settings_read["budgets"], models=settings_read["models"])
    store.data.update({key: value for key, value in settings_read.items() if key not in ("budgets", "models")})
    if adopted:
        store.data["phases"] = dict(adopted)
        store.data["resumedFrom"] = "the phase files in the run folder"
        numbers = ", ".join(str(output_contract.PHASE_NUMBERS[name]) for name in adopted)
        log.info(f"resuming: phase(s) {numbers} are read from disk, the run's own record is gone")
    store.save()

    return store


def _select_window_model(phase: str,
                         name: str,
                         is_fast: bool) -> str:
    """The configured model whose window a phase's calls are measured against: on a fast-processing run, phases 3 and
    4 call the models `Settings.resolve_fast_model_name` names (`video.fastProcessing`'s own, else the set's job models)."""

    if not is_fast or phase not in _FAST_MODEL_PHASES:
        return name

    return config.settings.resolve_fast_model_name(phase)


def read_phase_windows(log: logging.Logger,
                       is_fast: bool) -> dict[str, int]:
    """The window each phase's model is loaded with: what the server reports for the model, else the configured
    `num_ctx`. Read once, so every call's context share is measured against the window the host really holds."""

    windows = {}
    # One question per model, not per phase: phases 2, 4, 5 and 6 share a default model
    loaded_by_model: dict[tuple[str | None, str], int | None] = {}
    for row in chat_model_service.describe_phase_models():
        name = _select_window_model(row["phase"], row["name"], is_fast)
        model = config.settings.resolve_model(name)
        provider, served = chat_model_service.split_spec(model.spec)
        loaded = None
        host_url = chat_model_service.resolve_host_url(model)
        if provider in ("lmstudio", "openai-compatible") and host_url:
            if (host_url, served) not in loaded_by_model:
                loaded_by_model[(host_url, served)] = host_window_service.read_loaded_window(host_url, served)
                if not loaded_by_model[(host_url, served)]:
                    log.warning(f"  {row['phase']}: the server reports no loaded window for {served}; its share of "
                                f"the context is measured against the configured {model.num_ctx or 'none'}")
            loaded = loaded_by_model[(host_url, served)]
        window = loaded or model.num_ctx
        if window:
            windows[row["phase"]] = int(window)

    return windows


def _is_fast_run(output_root: Path,
                 is_audio: bool) -> bool:
    """Whether this run takes the fast-processing video workflow: an audio recording takes the audio workflow
    whatever the run's workflow says."""

    return not is_audio and video_workflow_service.is_fast_processing(output_root)


def _has_no_critic_run(output_root: Path,
                       is_audio: bool) -> bool:
    """Whether this run takes a video workflow with no review and no critique collection (a Fast speed's): an audio
    recording takes the audio workflow whatever the run's workflow says."""

    return not is_audio and video_workflow_service.has_no_critic(output_root)


def _is_direct_draft_run(output_root: Path,
                         is_audio: bool) -> bool:
    """Whether this run takes the direct-draft video workflow (the Fast speed's), whose phases 3 and 4 have names of
    their own: an audio recording takes the audio workflow whatever the run's workflow says."""

    return not is_audio and video_workflow_service.is_direct_draft(output_root)


def _build_context(paths: RunPaths,
                   store: run_store_service.RunStore,
                   selection: PhaseRequest,
                   is_dry_run: bool,
                   is_audio: bool,
                   log: logging.Logger) -> RunContext:
    """Everything a node is handed that is not state, assembled once."""

    run_dir = store.path.parent
    # The recording's text is read in its own language too, from phase 1's transcript when a run resumes past it
    transcript = paths.output_root / output_contract.TRANSCRIPT_FILE
    if transcript.is_file():
        text_detection_service.set_recording_language(str(json_store_utils.read_json(transcript).get("language") or ""))

    return RunContext(
        repository=paths.repository, module=paths.module, paths=paths, store=store,
        run_dir=run_dir, log=log,
        ignore=ignore_list_service.load_ignore_list(),
        is_dry_run=is_dry_run,
        selected_slugs=list(selection.selected_slugs),
        control_check=lambda: control_signal_service.check(run_dir, log),     # waits out a pause, so phase 3 holds between batches
        phase_windows=read_phase_windows(log, _is_fast_run(paths.output_root, is_audio)),
    )


def _log_run_totals(ctx: RunContext,
                    final: dict,
                    calls_before: int,
                    is_audio: bool) -> None:
    """What came out, and what this process's calls cost (`calls_before` is how many the run had when it started). An
    audio recording has no frame to count."""

    if not is_audio:
        counts = ctx.store.count_by_status()
        ctx.log.info("")
        ctx.log.info(f"written {len(final.get('written', []))}  blocked {len(final.get('blocked', []))}  "
                     f"skipped {counts.get('skipped', 0)}  of {counts.get('total', 0)} frame(s)")
    if not ctx.is_dry_run:
        ctx.log.info("")
        ctx.log.info(call_log_service.render_metrics_line(call_log_service.read_call_metrics(ctx.run_dir)[calls_before:],
                                                          display_path_utils.render_path(ctx.run_dir / call_log_service.METRICS_FILE)))


def _record_run_outcome(ctx: RunContext,
                        final: dict,
                        calls_before: int,
                        is_audio: bool) -> int:
    """The run's totals logged, then its status recorded; the exit code that status reports."""

    store = ctx.store
    _log_run_totals(ctx, final, calls_before, is_audio)
    phases = final.get("phases", {})
    blocked = final.get("blocked", [])
    if "failed" in phases.values():
        store.set_status("failed")

        return 1
    if blocked or "blocked" in phases.values():
        store.set_status("incomplete")
        for slug in blocked:
            ctx.log.warning(f"  blocked: {slug}")

        return 1
    store.set_status("complete")

    return 0


def describe_workflow(run_folder: Path) -> str:
    """The header's workflow line: the video workflow the run takes and, for direct-draft and fast-processing, what
    each of its phases does."""

    workflow = video_workflow_service.resolve_workflow(run_folder)
    title = video_workflow_vocabulary.WORKFLOW_TITLES[workflow]
    if workflow == video_workflow_vocabulary.DIRECT_DRAFT:
        frames_per_batch = config.settings.resolve_frames_per_call()

        return (f"{title} (phase 3 writes {frames_per_batch} frames a call straight into the draft, no check; phase 4 "
                f"reshapes the draft into the document in one call; phases 5 and 6 off)")
    if workflow != video_workflow_vocabulary.FAST_PROCESSING:
        return title
    fast = config.settings.video.fast_processing

    frame_model = config.settings.resolve_fast_model_name(config.FRAMES_PROCESSING_PHASE)
    document_model = config.settings.resolve_fast_model_name(config.FINALIZE_PHASE)

    return (f"{title} (phase 3 {frame_model}, {fast.frame_follow_ups} frames a session, no check; phase 4 one call "
            f"of {document_model}; phases 5 and 6 off)")


def _describe_fast_model(phase: str,
                         windows: dict[str, int]) -> str:
    """A fast-processing run's line for phase 3 or 4: the model that workflow calls there, its spec and what it does."""

    fast = config.settings.video.fast_processing
    name = config.settings.resolve_fast_model_name(phase)
    spec = config.settings.resolve_model(name).spec
    if phase == config.FRAMES_PROCESSING_PHASE:

        return f"{name}: {spec} ({fast.frame_follow_ups} frames a session, each described alone)"
    window = windows.get(phase)
    loaded = f", window {window:,}" if window else ""

    return f"{name}: {spec} (one call{loaded})"


def describe_redundancy_model() -> str:
    """What a video's phase 2 calls: the model and frames a call of each of its model stages that is on (the
    redundancy filter, the similarity recheck), or that it calls no model while both are off. Its `document` model is
    an audio recording's alone (the speech paragraphs)."""

    frames = config.settings.frames
    stages = []
    if frames.is_redundancy_filter_on:
        model = config.settings.resolve_model(frames.redundancy_model)
        stages.append(f"redundancy filter {frames.redundancy_model}: {model.spec} "
                      f"({frames.redundancy_frames_per_call} frames a call, {frames.redundancy_frame_width} px wide)")
    if frames.is_similarity_recheck_on:
        name = frames.similarity_model or frames.redundancy_model
        stages.append(f"similarity recheck {name}: {config.settings.resolve_model(name).spec} "
                      f"({frames.similarity_frames_per_call} frames a call)")

    return "; ".join(stages) or "no model (the redundancy filter and the similarity recheck are off)"


def _render_header_line(label: str,
                        text: str,
                        width: int = _HEADER_LABEL_WIDTH) -> str:
    """One line of the header's model block: the label padded to the widest one, then its text."""

    return f"  {label:{width}} {text}"


def render_model_lines(checks: RunStartChecksResult,
                       windows: dict[str, int],
                       is_audio: bool,
                       is_fast: bool = False,
                       has_no_critic: bool = False,
                       is_direct_draft: bool = False) -> list[str]:
    """The header's lines of what each phase runs, in phase order: phase 1's transcription and diarization, then each
    phase's model with its context and the window its host loaded (`windows`), a video's phase 2 naming the model its
    redundancy filter calls, a fast-processing run's phases 3 and 4 naming the models that workflow calls
    (`video.fastProcessing`), a run with no critic (fast-processing, direct-draft) showing phases 5 and 6 off, then
    the capability lines. A direct-draft run's phases 3 and 4 carry its own names, `processing_and_draft_writing` and
    `document_reshaping`, so the label column widens to fit them."""

    names = {phase: phase_guide.DIRECT_DRAFT_PHASE_TITLES[number].replace(" ", "_")
             for phase, number in output_contract.PHASE_NUMBERS.items()
             if is_direct_draft and number in phase_guide.DIRECT_DRAFT_PHASE_TITLES}
    width = max([_HEADER_LABEL_WIDTH, *(len(name) for name in names.values())])
    lines = [_render_header_line("transcribe", f"{checks.transcription} · {checks.diarization}", width)]
    for row in chat_model_service.describe_phase_models():
        phase = row["phase"]
        label = names.get(phase, phase)
        # a video's phase 2 calls no phase model: its header line names the redundancy filter's
        if phase == config.FRAMES_PREPARING_PHASE and not is_audio:
            lines.append(_render_header_line(label, describe_redundancy_model(), width))
            continue
        if has_no_critic and output_contract.PHASE_NUMBERS[phase] in video_workflow_vocabulary.FAST_OFF_PHASES:
            lines.append(_render_header_line(label, f"off (a fast run has no {phase})", width))
            continue
        if is_fast and phase in _FAST_MODEL_PHASES:
            lines.append(_render_header_line(label, _describe_fast_model(phase, windows), width))
            continue
        window = windows.get(phase)
        loaded = f", window {window:,}" if window else ""
        # phase 3's frames a call, which its model may set for itself
        batch = (f", {config.settings.resolve_frames_per_call()} frames a call"
                 if phase == config.FRAMES_PROCESSING_PHASE else "")
        lines.append(_render_header_line(label, f"{row['name']}: {row['provider']}:{row['model']} "
                                                f"(num_ctx={row['num_ctx']}{loaded}{batch})", width))

    return lines + [f"  {line}" for line in checks.capabilities]


def describe_run_line(run_id: str,
                      first: int,
                      last: int,
                      medium: str,
                      run_folder: Path) -> str:
    """The header's first line: the run, its phases and the workflow it takes, which every later line depends on.
    `medium` is "audio", "pdf" or "video"; a video's workflow is the one its run folder chose."""

    workflow = (_FIXED_WORKFLOWS[medium] if medium in _FIXED_WORKFLOWS
                else video_workflow_vocabulary.WORKFLOW_TITLES[video_workflow_service.resolve_workflow(run_folder)])

    return f"run {run_id}  phases {first}..{last} · {workflow} workflow"


# The workflow a recording's medium fixes, whatever the run's video workflow says: a PDF's header said "audio
# workflow" and named the audio engine (the Wikipedia PDF's run)
_FIXED_WORKFLOWS = {"audio": "audio", "pdf": "PDF", "office": "office", "ppt": "PPT"}


def describe_medium(video: Path,
                    is_audio: bool) -> str:
    """The recording's medium as the header names it: "pdf", "office", "audio" or "video"."""

    if pdf_source_service.is_pdf(video):
        return "pdf"
    if office_source_service.is_office(video):
        return "office"
    if slides_source_service.is_slides(video):
        return "ppt"

    return "audio" if is_audio else "video"


def _log_run_header(ctx: RunContext,
                    selection: PhaseRequest,
                    log_path: str,
                    checks: RunStartChecksResult,
                    video: Path,
                    is_audio: bool) -> None:
    """What this run is, at the top of its log, so the log says which model wrote which record."""

    medium = describe_medium(video, is_audio)
    ctx.log.info(describe_run_line(ctx.store.data["runId"], selection.first, selection.last, medium,
                                   ctx.paths.output_root))
    ctx.log.info(f"  {medium:5} : {display_path_utils.render_path(video)}")
    ctx.log.info(f"  output: {display_path_utils.render_path(ctx.paths.output_root)}")
    ctx.log.info(f"{output_contract.RUN_LOG_LINE}{display_path_utils.render_path(log_path)}")
    # Which settings this run reads, in its own log: the default settings alone, or a profile on top of them. A run
    # judged later by its log cannot be told apart otherwise - a repaired frame and a blocked one are the same
    # settings away.
    profile_part = ""
    if config.PROFILE_NAME:
        profile_part = f" + {config.build_profile_path(config.PROFILE_NAME).name} (--profile {config.PROFILE_NAME})"
    ctx.log.info(f"  config: {config.DEFAULT_CONFIG_FILE.name} + {config.LOCAL_CONFIG_FILE.name}{profile_part}")
    ctx.log.info(f"  provider set: {config.PROVIDER_SET_NAME}")
    is_fast = _is_fast_run(ctx.paths.output_root, is_audio)
    has_no_critic = _has_no_critic_run(ctx.paths.output_root, is_audio)
    # A fast run repairs nothing: the quality workflow's retry settings would only mislead
    if not has_no_critic:
        ctx.log.info(f"  repair: {_describe_repair_settings()}")
    # The video workflow from phase 3 on; an audio recording takes the audio workflow whatever it says
    if not is_audio:
        ctx.log.info(f"  workflow: {describe_workflow(ctx.paths.output_root)}")
    is_direct_draft = _is_direct_draft_run(ctx.paths.output_root, is_audio)
    for line in render_model_lines(checks, ctx.phase_windows, is_audio, is_fast, has_no_critic, is_direct_draft):
        ctx.log.info(line)
    if ctx.is_dry_run:
        ctx.log.info("  DRY RUN: prompts are rendered, no model is called")


def _finish_done_run(ctx: RunContext,
                     video: Path) -> None:
    """A run 100% done keeps its inbox recording in its own folder, and its last step
    moves the cuts no document line names out of the way. Only a done run: what a run still going cut, its next resume
    may still place."""

    kept = run_recording_service.keep_recording_if_done(ctx.store.data, config.INBOX_DIR, ctx.paths.output_root, video)
    if kept is not None:
        ctx.log.info(f"recording kept in {display_path_utils.render_path(kept)}")
    if not run_recording_service.is_run_done(ctx.store.data):
        return
    moved = cut_tidy_service.set_aside_unused_cuts(ctx.paths)
    if moved:
        listed = ", ".join(row["cut"] for row in moved[:_CUTS_LISTED])
        ctx.log.info(f"{len(moved)} cut(s) no figure uses moved to {cut_tidy_service.TRASH_CUTS_DIR}: "
                     f"{listed}{' …' if len(moved) > _CUTS_LISTED else ''}")


def _queue_on_server(args: argparse.Namespace) -> int:
    """`run --queue`: the run handed to the server's queue, so it waits its turn behind the page's
    jobs and the page and an agent see it; 0 when queued, 2 when the server refuses it or none answers."""

    if args.is_forced:
        args.logger.error("--force cannot be queued from the command line: run without --queue to force it")

        return 2
    # What `run` refuses before it starts, refused before it is queued: a queued job that only fails in its console is
    # found when its turn comes
    try:
        phase_models = read_phase_models(args.phase_models)
        build_phase_request(args)  # refused here as `run` would refuse it; its request is not needed
        read_input_range(args)
        paths = resolve_paths(args)
    except BadTarget as error:
        args.logger.error(str(error))

        return 2
    body = {"items": [{"video": str(paths.repository / paths.module), "output": str(paths.output_root)}],
            "only_phase": args.only_phase, "from_phase": args.from_phase, "to_phase": args.to_phase,
            "worker_indexes": list(args.worker_indexes), "is_resuming": args.is_resuming, "is_dry_run": args.is_dry_run,
            "profile": args.profile_name, "tags": list(args.tags), "ingesting_profile": args.ingesting_profile,
            "is_run_as_audio": args.is_run_as_audio, "pdf_mode": getattr(args, "pdf_mode", ""),
            "pdf_ocr_engine": getattr(args, "pdf_ocr_engine", ""),
            "document_pass": getattr(args, "document_pass", ""), "document_wishes": getattr(args, "document_wishes", ""),
            "document_source": getattr(args, "document_source", ""),
            "knowledge_filter_model": getattr(args, "knowledge_filter_model", ""),
            "range_start": getattr(args, "range_start", ""), "range_end": getattr(args, "range_end", ""),
            "range_pages": getattr(args, "range_pages", ""),
            "phase_models": phase_models, "workflow": args.workflow, "provider_set": getattr(args, "provider_set", "")}
    try:
        jobs = server_client_service.call_server("POST", "/jobs", json=body)
    except RuntimeError as error:
        args.logger.error(str(error))

        return 2
    for job in jobs:
        # The job's command line without the interpreter and main.py
        args.logger.info(f"queued as job {job['job_id']}: {' '.join(job.get('command', [])[2:])}")

    return 0


# A MISSING TARGET IS A QUESTION, NOT AN ERROR PATH. Neither flag has a default: a run line that
# names no input or no output folder cannot be typed, where a defaulted one would start real model
# calls over whatever the default happened to point at.
def _add_target(parser: argparse.ArgumentParser) -> None:
    """Which file to work on, and where everything this run writes goes. Both required."""

    parser.add_argument("--video", dest="target_file", type=Path, required=True,
                        help="the recording to work on: a video, an audio file, or a folder of its parts")
    parser.add_argument("--output", dest="output_root", type=Path, required=True,
                        help="the folder every phase file, record and log of this target is written to")


def _add_phase_selection(parser: argparse.ArgumentParser) -> None:
    """Which numbered phases to run. A phase that is not run is read from its file on disk."""

    parser.add_argument("--phase", dest="only_phase", type=int, default=None, metavar="N",
                        help="run exactly phase N and stop")
    parser.add_argument("--from", dest="from_phase", type=int, default=None, metavar="N",
                        help="run phase N and every phase after it")
    parser.add_argument("--to", dest="to_phase", type=int, default=None, metavar="N",
                        help="stop after phase N")
    parser.add_argument("--worker", dest="worker_indexes", type=int, action="append", default=[], metavar="N",
                        help="re-run phase 3 for frame N only, rewriting its notes; repeatable")


def resolve_paths(args: argparse.Namespace) -> RunPaths:
    """The one place the command line becomes a layout. Nothing is guessed."""

    # `RunPaths` names a target as a folder plus a name inside it. A file target is exactly that pair: the folder is the
    # file's own, the name is the file's, and everything this run writes goes under the output folder the line names.

    target_file = args.target_file.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    # A done run keeps its recording in its folder: the inbox path its record names
    # finds it there
    if not target_file.exists():
        target_file = run_recording_service.find_recording_by_origin(str(target_file), [output_root.parent]) or target_file
    if not target_file.exists():
        raise BadTarget(f"{display_path_utils.render_path(target_file)} does not exist, and no run folder beside "
                        f"{display_path_utils.render_path(output_root)} keeps it")
    # A folder of two or more videos is one meeting recorded in parts
    if not target_file.is_file() and not recording_parts_service.is_multi_part(target_file):
        raise BadTarget(f"{display_path_utils.render_path(target_file)} is neither a video file nor a folder of two or "
                        f"more videos")

    return RunPaths(repository=target_file.parent, module=target_file.name, output_root_override=output_root)


def _list_branch_ancestors(output_root: Path) -> frozenset[str]:
    """The names of the run folders `output_root` was branched from, each `branch.json` followed back in turn."""

    names: list[str] = []
    folder = output_root
    while (branch := run_branch_service.read_branch(folder)) and branch.get("from") and branch["from"] not in names:
        names.append(str(branch["from"]))
        folder = output_root.parent / str(branch["from"])

    return frozenset(names)


def find_duplicate_refusal(video: Path,
                           output_root: Path,
                           is_forced: bool,
                           input_range: InputRangeSpec | None = None) -> str | None:
    """Why this recording is not processed again: a done run in another folder processed the same part of the same
    recording (its SHA-256 and its range, `input_range`, or the one the folder keeps); None when none did, or when
    `--force` asks for it anyway. A run of the same folder is a rerun asked for."""

    if is_forced:
        return None
    chosen_range = input_range or input_range_service.read_range(folder=output_root)
    # A branch runs its phases again on purpose: the run it was branched from, done, refused the run line the branch
    # itself printed (froscon talk 3211, DEFECTS 270)
    done = recording_hash_service.find_done_run(recording_hash_service.compute_recording_hash(video), excluded=output_root,
                                                excluded_names=_list_branch_ancestors(output_root),
                                                input_range=chosen_range)
    if done is None:
        return None
    part = f" ({input_range_service.describe_range(input_range=chosen_range)})" if chosen_range is not None else ""

    return (f"{video.name}{part} was processed already: {done['root']}/{done['name']} is done with the same recording "
            f"(SHA-256) and the same part of it. Add --force to process it again")


def build_phase_request(args: argparse.Namespace) -> PhaseRequest:
    """`--phase`, `--from`, `--to` and `--worker`, as the range of phases this run is asked for."""

    numbers = sorted(output_contract.PHASE_FILES)
    first, last = numbers[0], numbers[-1]
    if args.only_phase is not None and (args.from_phase is not None or args.to_phase is not None):
        raise BadTarget("--phase runs exactly one phase, so it cannot be combined with --from or --to")
    if args.worker_indexes and (args.only_phase, args.from_phase, args.to_phase) != (None, None, None):
        raise BadTarget(f"--worker re-runs phase {routing.FRAMES_PROCESSING_PHASE_NUMBER} for the named frame(s), so it cannot be "
                        "combined with --phase, --from or --to")
    if args.worker_indexes:
        first = last = routing.FRAMES_PROCESSING_PHASE_NUMBER
    if args.only_phase is not None:
        first = last = args.only_phase
    if args.from_phase is not None:
        first = args.from_phase
    if args.to_phase is not None:
        last = args.to_phase
    for number in (first, last):
        if number not in output_contract.PHASE_FILES:
            raise BadTarget(f"there is no phase {number}. The phases are: "
                            + ", ".join(f"{n} ({output_contract.PHASE_FILES[n]['phase']})" for n in numbers))
    if first > last:
        raise BadTarget(f"--from {first} is after --to {last}, so no phase is selected")

    selected_slugs = tuple(frame_slug_utils.build_frame_slug(index) for index in args.worker_indexes)

    return PhaseRequest(first=first, last=last, selected_slugs=selected_slugs)


def add_run_switches(parser: argparse.ArgumentParser) -> None:
    """`--dry-run` and `--force`, which `inbox` hands on to every `run` line it builds."""

    parser.add_argument("--dry-run", dest="is_dry_run", action="store_true",
                        help="render what each call would be sent, and call no model")
    parser.add_argument("--force", dest="is_forced", action="store_true",
                        help="process the recording even when a done run in another folder processed the same one")


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """The flags of the `run` command, for its own parser and for the `run` line `inbox` builds for each video."""

    _add_target(parser)
    _add_phase_selection(parser)
    add_run_switches(parser)
    parser.add_argument("--queue", dest="is_queued", action="store_true",
                        help="hand the run to the running `i2k serve`'s queue, as the page's Run form does, and return")
    parser.add_argument("--tag", dest="tags", action="append", default=[], metavar="TEXT",
                        help="a tag for the run, added to its tags before phase 1; repeat for several. A tag naming an "
                             "ingesting profile picks it")
    parser.add_argument("--ingesting-profile", dest="ingesting_profile", default="",
                        choices=ingesting_profile_service.list_profile_names(), metavar="NAME",
                        help="the ingesting profile the document is written in, chosen by hand: no tag and no model "
                             "call changes it. One of: " + ", ".join(ingesting_profile_service.list_profile_names()))
    parser.add_argument("--workflow", dest="workflow", default="", choices=video_workflow_vocabulary.WORKFLOWS,
                        help="the video workflow from phase 3 on: quality (checked batches, the document conversation, "
                             "the review), direct-draft (the Fast speed's: frames written straight into a draft in "
                             "chapters, one call titles and summarises it, no review) or fast-processing (the earlier "
                             "fast workflow: each frame described alone, one call writes the document, no review). Kept "
                             "in the run folder, so a resume needs no flag; none takes video.workflow")
    parser.add_argument("--as-audio", dest="is_run_as_audio", action="store_true",
                        help="process a video as an audio recording: its sound is transcribed and runs the audio "
                             "workflow, and no frame is taken. Kept in the run folder, so a resume needs no flag")
    parser.add_argument("--pdf-mode", dest="pdf_mode", default="", choices=output_contract.PDF_MODES,
                        help="how a PDF is read: pages-as-images (each page shown to the model as a picture, read in "
                             "batches by phase 3) or text-only (its text alone, written into paragraphs with no model "
                             "call; phase 3 reads nothing and phases 4 on run as for an audio recording) or "
                             "markdown-conversion (converted to markdown with no model at all: the document is the PDF's own text, each "
                             "figure where it stood). Kept in the run folder, so a resume needs no flag")
    parser.add_argument("--document-pass", dest="document_pass", default="", choices=output_contract.DOCUMENT_PASSES,
                        help="phase 7, after the document is published: a model cleans it up (removes the export's "
                             "noise) or rewrites it (cleans up and improves its structure and writing), in batches. "
                             "Kept in the run folder, so a resume needs no flag; none leaves phase 7 off")
    parser.add_argument("--document-wishes", dest="document_wishes", default="",
                        help="the person's own wishes for phase 7's clean up or rewrite, such as \"remove every Jira "
                             "link\"; read with --document-pass")
    parser.add_argument("--document-source", dest="document_source", default="",
                        choices=document_pass_service.DOCUMENT_SOURCES,
                        help="what phase 7's pass starts from: original (the document before phase 7's first pass, the "
                             "default) or current (the document the last pass left, so passes stack); read with "
                             "--document-pass")
    parser.add_argument("--pdf-ocr-engine", dest="pdf_ocr_engine", default="", choices=("tesseract", "unlimited-ocr"),
                        help="what reads a PDF's scanned pages in phase 1: tesseract "
                             "(about 1.7 s a page) or unlimited-ocr (Unlimited-OCR-2, about 8 to 20 s a page, nearly "
                             "every word of a scan kept; tools/unlimited-ocr/setup.sh --download). Kept in the run "
                             "folder, so a resume needs no flag; none takes pdf.ocrEngine")
    parser.add_argument("--knowledge-filter-model", dest="knowledge_filter_model", default="",
                        choices=decision_filter_service.FILTER_MODELS,
                        help="which model drops the frames with no knowledge in phase 2: google-SigLIP-2 (the frame "
                             "classifier trained here) or liquidai-d1-2B (Liquid AI's decision model, no training; "
                             "tools/d1-knowledge-filter/setup.sh --download). Kept in the run folder, so a resume needs "
                             "no flag; none takes frames.knowledgeFilterModel")
    parser.add_argument("--start", dest="range_start", default="", metavar="TIME",
                        help="process a video or an audio recording from this time on (seconds, m:ss or h:mm:ss); "
                             "times stay the recording's own. Kept in the run folder, so a resume needs no flag")
    parser.add_argument("--end", dest="range_end", default="", metavar="TIME",
                        help="process a video or an audio recording up to this time; read with or without --start")
    parser.add_argument("--pages", dest="range_pages", default="", metavar="FIRST-LAST",
                        help="process only these pages of a PDF, such as 12-40; pages keep their own numbers. Kept in "
                             "the run folder, so a resume needs no flag")
    parser.add_argument("--resume", dest="is_resuming", action="store_true",
                        help="continue the newest run of this output folder instead of starting a new one")
    parser.add_argument("--provider-set", "--worker-set", dest="provider_set", default="", metavar="NAME",
                        help="the provider set the run is on: the entry of the settings' `providerSets` whose models and "
                             "hosts it uses, or `default` for the settings as they are; kept in the run folder, so a "
                             "resume needs no flag")
    parser.add_argument("--speed-preset", dest="speed_preset", default="", metavar="NAME",
                        help="the Workflow speed preset of a video run (quality, balanced, fast, or one the settings' "
                             "speedPresets add); kept in the run folder, so a resume needs no flag")
    parser.add_argument("--speed-setting", dest="speed_settings", action="append", default=[], metavar="KEY=VALUE",
                        help="one Workflow speed setting changed from the preset, such as "
                             "framesProcessing.isCritiqueOn=false; repeat for several")
    parser.add_argument("--phase-model", dest="phase_models", action="append", default=[], metavar="PHASE=NAME",
                        help="the configured model (an entry of the settings' `models`) a phase calls in this run; "
                             "repeat for several. Phases: " + ", ".join(config.PHASE_DEFAULT_MODELS))


def read_phase_models(pairs: list[str]) -> dict[str, str]:
    """`--phase-model PHASE=NAME` pairs as `phaseModels`, each checked: a phase that calls a model, a configured model.
    Raises `BadTarget` naming the pair, before the run record is written."""

    phase_models = {}
    for pair in pairs:
        phase, separator, name = pair.partition("=")
        phase, name = phase.strip(), name.strip()
        if not separator or not phase or not name:
            raise BadTarget(f"--phase-model {pair!r} is not PHASE=NAME")
        try:
            config.check_phase_model(phase, name, config.settings.models)
        except ValueError as exc:
            raise BadTarget(f"--phase-model {pair!r}: {exc}") from exc
        phase_models[phase] = name

    return phase_models


def read_input_range(args: argparse.Namespace) -> InputRangeSpec | None:
    """`--start`, `--end` and `--pages` as the part of the input the run processes, checked; None when none is given.
    Raises `BadTarget` naming what is wrong, before the run record is written."""

    try:
        return input_range_service.build_range(start=getattr(args, "range_start", ""),
                                               end=getattr(args, "range_end", ""),
                                               pages=getattr(args, "range_pages", ""))
    except ValueError as exc:
        raise BadTarget(str(exc)) from exc


def check_range_fits(video: Path,
                     input_range: InputRangeSpec | None) -> None:
    """Refuse, by `BadTarget`, a range that does not fit its input: pages for a recording, times for a PDF, a first page
    past the PDF's last, a start past the recording's end."""

    if input_range is None:
        return
    if office_source_service.is_office(video) or slides_source_service.is_slides(video):
        raise BadTarget(f"{video.name} is a Word, OpenDocument or slide file: a part of it cannot be chosen")
    is_pdf = pdf_source_service.is_pdf(video)
    if input_range.is_pages and not is_pdf:
        raise BadTarget(f"{video.name} is a recording: give its times with --start and --end, not pages")
    if is_pdf and not input_range.is_pages:
        raise BadTarget(f"{video.name} is a PDF: give its pages with --pages, not times")
    if is_pdf:
        try:
            input_range_service.list_page_indexes(input_range=input_range, page_count=pdf_source_service.count_pages(video))
        except ValueError as exc:
            raise BadTarget(str(exc)) from exc

        return
    duration_s = frame_extraction_service.read_duration_s(video)
    if duration_s is not None and (input_range.start_s or 0.0) >= duration_s:
        raise BadTarget(f"the range starts at {input_range_service.format_time(seconds=input_range.start_s or 0.0)}, "
                        f"past the recording's end at {input_range_service.format_time(seconds=duration_s)}")


def write_chosen_range(run_folder: Path,
                       input_range: InputRangeSpec | None,
                       is_dry_run: bool) -> None:
    """The run line's range written into the run folder before phase 1; no range leaves the folder's as it is, and a
    dry run writes nothing."""

    if input_range is not None and not is_dry_run:
        input_range_service.write_range(folder=run_folder, input_range=input_range)


def add_given_tags(run_folder: Path,
                   tags: list[str],
                   is_dry_run: bool) -> None:
    """The run line's `--tag`s added after the tags the run folder has, before phase 1 reads them; a dry run writes
    nothing."""

    if tags and not is_dry_run:
        run_tags_service.apply_tag_changes(run_folder, tags, [])


def write_chosen_profile(run_folder: Path,
                         profile: str,
                         is_dry_run: bool) -> None:
    """The run line's `--ingesting-profile` written into the run folder as the person's choice, before phase 1; no
    flag leaves the folder's profile as it is, and a dry run writes nothing."""

    if profile and not is_dry_run:
        ingesting_profile_service.write_profile(run_folder, profile, "user")


def write_chosen_workflow(run_folder: Path,
                          workflow: str,
                          is_dry_run: bool) -> None:
    """The run line's `--workflow` written into the run folder as the person's choice, before phase 1; no flag leaves
    the folder's workflow as it is, and a dry run writes nothing."""

    if workflow and not is_dry_run:
        video_workflow_service.write_chosen_workflow(run_folder, workflow, "user")


def choose_speed(run_folder: Path,
                 preset: str,
                 pairs: list[str]) -> dict | None:
    """The speed the run line's `--speed-preset` and `--speed-setting` pairs name, None with no flag; a change with no
    preset starts from the preset the folder kept, else the one the settings hold. Nothing is written: a run refused
    after this must leave no folder behind (DEFECTS 718)."""

    if not (preset or pairs):
        return None
    kept = run_speed_service.read_chosen_speed(run_folder) or {}
    base = preset or kept.get("preset") or run_speed_service.describe_speed_options()["default_preset"]
    changes = dict(run_speed_service.parse_speed_setting(pair) for pair in pairs)

    return run_speed_service.resolve_speed(base, changes)


def write_chosen_medium(run_folder: Path,
                        is_run_as_audio: bool,
                        is_dry_run: bool,
                        pdf_mode: str = "") -> None:
    """The run line's `--as-audio` or `--pdf-mode text-only` written into the run folder before phase 1; no flag leaves
    the folder's choice as it is, and a dry run writes nothing."""

    if is_dry_run:
        return
    if is_run_as_audio:
        recording_medium_service.write_chosen_medium(run_folder, output_contract.AUDIO_MEDIUM)
    elif pdf_mode in output_contract.PDF_MODE_MEDIUMS:
        recording_medium_service.write_chosen_medium(run_folder, output_contract.PDF_MODE_MEDIUMS[pdf_mode])


def _read_default_pdf_mode(paths: RunPaths,
                           video: Path,
                           input_range: InputRangeSpec | None) -> str:
    """`pdf.defaultMode` for a PDF whose run chose no mode and has not read it yet, by its page count under "by-size",
    the pages of its range alone when it has one; "" otherwise, so a run made before the default changed keeps the pages
    it was read as."""

    if (not inbox_service.is_document(video) or recording_medium_service.read_chosen_medium(paths.output_root)
            or phase_file_service.has_phase_file(paths, output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE])):
        return ""
    # A Word or OpenDocument file is converted with no model unless the run asks for more
    if office_source_service.is_office(video):
        return output_contract.PDF_MODE_MARKDOWN_CONVERSION
    # A deck's slides are always read as pictures (the owner: "only image to text")
    if slides_source_service.is_slides(video):
        return output_contract.PDF_MODE_PAGES
    chosen_range = input_range or input_range_service.read_range(folder=paths.output_root)
    if chosen_range is None:
        return pdf_default_mode_service.choose_default_mode(path=video, settings=config.settings.pdf)
    page_indexes = input_range_service.list_page_indexes(input_range=chosen_range,
                                                         page_count=pdf_source_service.count_pages(video))

    return pdf_default_mode_service.choose_mode_for_pages(page_count=len(page_indexes), settings=config.settings.pdf)


def _is_new_video_run(paths: RunPaths,
                      video: Path) -> bool:
    """Whether this is a video run that chose no speed and has not transcribed yet, which takes the default speed; a
    run that began without a speed keeps running as it began."""

    return (run_speed_service.read_chosen_speed(paths.output_root) is None
            and not inbox_service.is_audio_recording(video)
            and not inbox_service.is_document(video)
            and not recording_medium_service.is_audio_run_recording(paths.output_root, video)
            and not phase_file_service.has_phase_file(
                paths, output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE]))


def _run_selected_phases(ctx: RunContext,
                         selection: PhaseRequest,
                         checks: RunStartChecksResult,
                         video: Path,
                         is_audio: bool) -> int:
    """Steps 3 to 6 of `cmd_run`: a stale control signal cleared, the log header, the graph driven to the end, and the
    report. The exit code `cmd_run` returns."""

    store = ctx.store

    # STEP 3: clear a control signal an earlier session left behind, or it stops this run at once
    stale = control_signal_service.read_signal(ctx.run_dir)
    if stale:
        control_signal_service.clear(ctx.run_dir)
        ctx.log.info(f"cleared a leftover {stale!r} signal from the previous session")

    # STEP 4: announce what this run is, so the log says which model wrote which record
    # The logger the console writes to is passed: a handler attached to a logger nobody writes to saves an empty file.
    cli_log = cli_log_service.open_cli_log(ctx.run_dir, ctx.log)
    try:
        _log_run_header(ctx, selection, cli_log.baseFilename, checks, video, is_audio)
        # Every frame, group, document part and phase writes its time and cost to `performance.jsonl` as it ends;
        # the session line opened here is what tells a resumed run's lines apart.
        session = performance_log_service.open_session(ctx, selection.first, selection.last)

        # STEP 5: build the graph and the state it starts from, and drive it to the end
        graph = graph_assembly.build_graph(ctx)
        calls_before = len(call_log_service.read_call_metrics(ctx.run_dir))
        state = graph_state.new_run_state(selection.first, selection.last, is_dry_run=ctx.is_dry_run,
                                          is_audio=is_audio)
        duration_s = frame_extraction_service.read_duration_s(video)
        # The run's OWN step budget, never one attempt's: the graph takes one step per item plus one
        # per phase, so its steps grow with the work. Handed `recursion_limit`, every run over about
        # 75 items stopped before its first model call.
        try:
            final = graph.invoke(state, {"recursion_limit": config.settings.budgets.run_steps})
        except (ControlStop, KeyboardInterrupt) as exc:
            # Ctrl-C is an operator's stop like the page's: no `Exception`, it would leave the record with no end
            ctx.log.warning(f"stopped: {str(exc) or 'Ctrl-C'}")
            store.set_status("interrupted")
            performance_log_service.record_video(ctx, session, "interrupted", duration_s, is_audio,
                                                 _is_direct_draft_run(ctx.paths.output_root, is_audio))

            return run_job.EXIT_INTERRUPTED

        # STEP 6: report what came out, the status the exit code reports, and what the session cost against the
        # recording's length
        code = _record_run_outcome(ctx, final, calls_before, is_audio)
        performance_log_service.record_video(ctx, session, store.data.get("status", "unknown"), duration_s, is_audio,
                                             _is_direct_draft_run(ctx.paths.output_root, is_audio))
        if not ctx.is_dry_run:
            _finish_done_run(ctx, video)

        return code
    except Exception:
        # An error nothing caught went to stderr alone, and the console log ended mid-run with no word of why (a
        # sampling field the host refuses)
        ctx.log.exception("the run stopped on an error")
        raise
    finally:
        cli_log_service.close_cli_log(cli_log, logger=ctx.log)


def cmd_run(args: argparse.Namespace) -> int:
    """Run the selected phases; 0, 1, 2 or 130, as the module docstring lists them."""

    if args.is_queued:
        return _queue_on_server(args)

    # STEP 1: resolve what this run is about, and refuse instead of guessing. Every refusal is decided before the run
    # record is written: a refused run that left one behind would be continued by the next `--resume` as if it had
    # started
    try:
        paths = resolve_paths(args)
        video = paths.repository / paths.module
        input_range = read_input_range(args)
        check_range_fits(video, input_range)
        duplicate = find_duplicate_refusal(video, paths.output_root, args.is_forced, input_range)
        if duplicate is not None:
            raise BadTarget(duplicate)
        selection = build_phase_request(args)
        # This run's provider set: the run line's, else the one the folder kept, else the settings' default; laid over the
        # files and the profile before the speed values, which go on top of it. The folder keeps it in step 2
        try:
            provider_set, is_provider_set_to_keep = run_provider_set_service.resolve_provider_set(
                paths.output_root, getattr(args, "provider_set", ""))
            config.apply_provider_set(provider_set)
        except ValueError as exc:
            raise BadTarget(str(exc)) from exc
        # This run's Workflow speed: the one the run line names, else the one kept in the folder; laid over the settings
        # before anything reads them, so a resume runs as the run began. The folder keeps a new one only in step 2, once
        # nothing can refuse the run: written here, a refused resume under a renamed folder's old name made an empty
        # folder of that name, which the page listed as an unfinished run (DEFECTS 718)
        chosen_speed = choose_speed(paths.output_root, getattr(args, "speed_preset", ""),
                                    getattr(args, "speed_settings", []))
        # A new video run nobody chose a speed for takes the default preset (the owner: Balanced); a run that began
        # without one keeps running as it began
        if chosen_speed is None and _is_new_video_run(paths, video):
            chosen_speed = run_speed_service.resolve_speed(run_speed_service.DEFAULT_PRESET, {})
        speed = chosen_speed or run_speed_service.read_chosen_speed(paths.output_root)
        if speed:
            config.apply_speed_values(speed.get("values") or {})
        # This run's own choice of model per phase, on top of the settings' `phaseModels`, before any model is built
        config.settings.phase_models = {**config.settings.phase_models, **read_phase_models(args.phase_models)}
        # Phases 1 and 2 read the recording, so they wait while a done run keeps it in its folder; a branch made to run
        # them again (a PDF's processing mode changed, another part of the input) has none of their outputs to keep
        stored = run_recording_service.find_stored_recording(paths.output_root)
        is_branch_rereading = run_branch_service.is_rereading_recording(paths.output_root)
        if stored is not None and not is_branch_rereading and run_recording_service.would_run_recording_phases(
                selection.first, _read_known_phases(paths, args.is_resuming), args.is_resuming):
            raise BadTarget(f"phases 1 and 2 read the recording, kept in {display_path_utils.render_path(stored)}: move "
                            f"it back to the inbox first (`i2k recording-to-inbox --output "
                            f"{display_path_utils.render_path(paths.output_root)}`)")
        # The transcription engine is checked HERE, before the audio is extracted: an optional
        # package that is missing is a refusal at the start of a run, never a failure after the
        # expensive part is already paid for.
        # A PDF is read, never transcribed: its line names its reader, and it has no speakers to tell apart
        is_pdf = pdf_source_service.is_pdf(video)
        checks = RunStartChecksResult(
            transcription=("pdf: read by pymupdf4llm" if is_pdf
                           else transcription_service.describe_available_engine(config.settings.audio)),
            diarization=("no speakers" if is_pdf
                         else diarization_service.describe_diarization(config.settings.audio.diarization)),
            capabilities=[] if args.is_dry_run else model_capability_service.check_phase_capabilities())
        store = _open_run(paths, args.is_resuming, args.is_dry_run, args.logger)
    except (BadTarget, ModelUnavailable, MissingModelCapability, EngineUnavailable, FileNotFoundError,
            RuntimeError) as exc:
        args.logger.error(str(exc))

        return 2

    # A PDF run given no mode takes the settings' before it reads its PDF; one already read keeps how it was read
    pdf_mode = getattr(args, "pdf_mode", "") or _read_default_pdf_mode(paths, video, input_range)
    # A Word file has no pages to show as pictures; a deck's slides are read as pictures alone
    if office_source_service.is_office(video) and pdf_mode == output_contract.PDF_MODE_PAGES:
        args.logger.error(f"{video.name} is a Word or OpenDocument file: it is read as text (--pdf-mode text-only or "
                          f"markdown-conversion), never as pages")

        return 2
    if slides_source_service.is_slides(video) and pdf_mode not in ("", output_contract.PDF_MODE_PAGES):
        args.logger.error(f"{video.name} is a slide deck: its slides are read as pictures (--pdf-mode "
                          f"pages-as-images), never as text")

        return 2
    # STEP 2 here, then steps 3 to 6 in `_run_selected_phases`. An error nothing caught ends it recorded failed: a
    # record left "running" by a dead process reads as a live run on the page
    try:
        # STEP 2: create the directories this run owns. Idempotent, and done on a resume too.
        run_paths_service.ensure_run_paths(paths)
        # the naming rules' tags with the run line's: a recording named for a client or a series is tagged by its name;
        # and the format of the file the run reads
        rule_tags = [*run_tags_service.list_rule_tags(args.target_file.name),
                     run_tags_service.describe_format_tag(args.target_file)]
        add_given_tags(paths.output_root, [*args.tags, *rule_tags], args.is_dry_run)
        write_chosen_profile(paths.output_root, args.ingesting_profile, args.is_dry_run)
        write_chosen_medium(paths.output_root, args.is_run_as_audio, args.is_dry_run, pdf_mode)
        write_chosen_range(paths.output_root, input_range, args.is_dry_run)
        if getattr(args, "document_pass", "") and not args.is_dry_run:
            document_pass_service.write_document_pass(paths.output_root, args.document_pass,
                                                      getattr(args, "document_wishes", ""),
                                                      getattr(args, "document_source", ""))
        if getattr(args, "pdf_ocr_engine", "") and not args.is_dry_run:
            pdf_source_service.write_chosen_ocr_engine(paths.output_root, args.pdf_ocr_engine)
        if getattr(args, "knowledge_filter_model", "") and not args.is_dry_run:
            decision_filter_service.write_chosen_model(paths.output_root, args.knowledge_filter_model)
        if is_provider_set_to_keep and not args.is_dry_run:
            run_provider_set_service.write_chosen_provider_set(paths.output_root, provider_set)
        if chosen_speed and not args.is_dry_run:
            run_speed_service.write_chosen_speed(paths.output_root, chosen_speed)
        write_chosen_workflow(paths.output_root, args.workflow, args.is_dry_run)
        # A takeaway always takes the audio workflow; said here too, since a dry run writes no profile file to read.
        # `--as-audio` itself as well as its file: a dry run writes no file and still rehearses the audio workflow
        is_audio = (args.is_run_as_audio or pdf_mode in output_contract.PDF_MODE_MEDIUMS
                    or args.ingesting_profile in ingesting_profile_vocabulary.ONE_CALL_PROFILES
                    or recording_medium_service.is_audio_run_recording(paths.output_root, video))
        ctx = _build_context(paths, store, selection, args.is_dry_run, is_audio, args.logger)
        # Every prompt this run builds reads its own workflow's instruction where a step has one per workflow; an office
        # document reads the PDF's, both being documents read as text
        medium = describe_medium(video, is_audio)
        system_prompt_builder.set_run_workflow("pdf" if medium in ("office", "ppt") else medium)

        return _run_selected_phases(ctx, selection, checks, video, is_audio)
    except KeyboardInterrupt:
        # Ctrl-C outside the graph's run (the host probe, the graph's build, the report) is the operator's stop as well
        store.set_status("interrupted")

        return run_job.EXIT_INTERRUPTED
    except Exception:
        store.set_status("failed")
        raise
