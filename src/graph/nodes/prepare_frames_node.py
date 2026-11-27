"""Phase 2: produce the frame list in PLAIN CODE, write it to its numbered file, and freeze it into
the store.

No model, no attempt loop, no retry budget: frame preparation is deterministic, so a failure is a
defect to fix and running it again would fail the same way. It is still validated hardest, and its
failure STOPS the run, because every later phase is built on this list.

A phase that this run was not asked for is READ from its file instead of run, and a missing file is
refused by name. That is the whole mechanism behind `--phase` and `--from`.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from graph import routing
from graph.graph_state import RunState
from graph.nodes import process_frames_node
from graph.nodes.retry_loop import attempt_request_builder
from models import output_contract
from models.run_context import RunContext
from services import frame_preparation_service
from services import frames_preparing_progress_service as progress_service
from services import performance_log_service
from services import phase_file_service
from services import run_resume_service
from services import validation_service
from services import video_workflow_service
from utils import display_path_utils
from validators import loop_items_validator

PHASE = output_contract.FRAMES_PREPARING_PHASE
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]
# The frame loop is the one reader of this phase's queue.
FRAMES_PROCESSING_PHASE = output_contract.FRAMES_PROCESSING_PHASE

# Which validator judges this phase's file. NOT the phase: see `services/validation_service.py`.
FRAMES_PREPARING_KIND = "frames_preparing"


def _mark_failed(ctx: RunContext,
                 reason: str) -> dict:
    ctx.log.error(f"phase {PHASE_NUMBER} failed: {reason}")
    # Its line in the performance log is written by the caller that timed it
    performance_log_service.record_phase_end(ctx, PHASE, "failed", None)

    return {"phases": {PHASE: "failed"}}


def _close_frames_processing_once_all_finished(ctx: RunContext) -> list[str]:
    """Phase 3 recorded blocked or complete, with every frame finished since (written or skipped), is ended as the frame
    loop ends it: with nothing queued its node never runs to say so, and the stale "blocked" held phase 5. The batches
    whose accepted frames never had their follow-ups are finished first (DEFECTS 285): a run stopped after a batch's
    answer was recorded complete over them. The frames blocked, when one of those follow-ups blocks."""

    if ctx.selected_slugs or ctx.store.get_phase(FRAMES_PROCESSING_PHASE) not in ("blocked", "complete"):
        return []
    if not all(item.get("status", "pending") in output_contract.TERMINAL_ITEM_STATUSES
               for item in ctx.store.list_items().values()):
        return []
    if ctx.store.get_phase(FRAMES_PROCESSING_PHASE) == "complete" and not process_frames_node.list_unfinished_batches(ctx):
        return []

    return process_frames_node.end_phase_on_empty_queue(ctx)


def finish_phase(ctx: RunContext) -> dict:
    """Freeze what is on disk into the store and hand the frame loop its queue. Both ways into a
    finished phase 2 end here, the fresh preparation and the file read back, because there is one
    place that decides what follows a finished phase 2."""

    # STEP 1: the store is seeded ONCE per run. A resume finds its items already there, with the statuses
    # they reached, and seeding again would reset every finished item to pending.
    if not ctx.store.list_items():
        try:
            prepared = loop_items_validator.read_loop_items(ctx.paths)
        except (OSError, ValueError) as exc:
            return _mark_failed(ctx, f"re-reading the accepted frame list: {exc}")
        ctx.store.seed_items([{**entry, "status": "skipped" if entry["ignored"] else entry.get("status", "pending")}
                              for entry in ctx.ignore.apply(prepared.get("items", []))])

    # STEP 2: a frame whose notes are in the frames file is written, whatever the store says: its notes are what
    # phase 3 produces, and asking for them again pays for every one twice. A frame named on the command line is
    # asked again.
    items = ctx.store.list_items()
    for slug in run_resume_service.list_written_slugs(ctx.paths):
        if slug in items and items[slug].get("status") == "pending" and slug not in ctx.selected_slugs:
            ctx.store.set_item_status(slug, "written", attempts=0)

    # STEP 3: `--worker N` re-runs frame N and rewrites its entry, accepted or not (2026-10-05: frames accepted with
    # notes that copied the text reader's misreadings were named and nothing was asked)
    for slug in ctx.selected_slugs:
        if items.get(slug, {}).get("status") in ("written", "blocked"):
            ctx.store.set_item_status(slug, "pending", attempts=0)

    # STEP 4: this phase is done, in the run record before STEP 5 reads it; its performance line is the caller's
    performance_log_service.record_phase_end(ctx, PHASE, "complete", None)
    ctx.log.info(f"phase {PHASE_NUMBER}: {len(ctx.store.list_items())} frame(s) · "
                 f"{video_workflow_service.describe_frame_grouping(ctx.paths.output_root)}")

    # STEP 5: phase 3's own record, as the frames now stand
    blocked = _close_frames_processing_once_all_finished(ctx)
    if blocked:
        return {"phases": {PHASE: "complete"}, "queue": [], "blocked": blocked}

    # STEP 6: the queue. A phase 3 the run records complete is not run again unless frames are named: `--from 4` over
    # frames left without notes is the operator's decision, and a later plain `--resume` keeps it. A blocked frame is
    # never "done": frames named by --worker blocked under a phase 3 recorded complete by an earlier pass, and a plain
    # resume queued nothing and began phase 4 without their notes (2026-10-06)
    counts = ctx.store.count_by_status()
    is_frames_processing_done = (ctx.store.get_phase(FRAMES_PROCESSING_PHASE) == "complete" and not ctx.selected_slugs
                                 and not counts.get("blocked"))
    queue = [] if is_frames_processing_done else ctx.store.list_pending_queue(ctx.selected_slugs)

    return {"phases": {PHASE: "complete"}, "queue": queue}


def _build_empty_update() -> dict:
    """The graph's update when the phase has no item list to hand on: done, with nothing queued."""

    return {"phases": {PHASE: "complete"}, "queue": []}


def _prepare_fresh(ctx: RunContext) -> dict:
    """Prepare the frames in plain code, write the numbered file, judge it from disk, and freeze it into the store."""

    ctx.log.info("")
    ctx.log.info(f"phase {PHASE_NUMBER}: preparing frames")
    try:
        prepared = frame_preparation_service.prepare_frames(ctx)
    except Exception as exc:  # noqa: BLE001 (reported as a failed phase, never a bare traceback)
        # The traceback goes to the run's log at debug level, so the console keeps one line and the cause is kept
        ctx.log.debug("phase %s's traceback", PHASE_NUMBER, exc_info=True)
        # The stage that raised is closed as failed: left running, the page counted its clock under a failed phase
        for stage in progress_service.read_progress(ctx.run_dir):
            if stage["status"] == "running":
                progress_service.mark_stage_failed(ctx.run_dir, stage["name"])

        return _mark_failed(ctx, f"{type(exc).__name__}: {exc}")
    phase_file_service.write_phase_file(ctx.paths, PHASE_NUMBER,
                                        {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION, **prepared})
    # Judged from disk before anything is built on it, then frozen into the store so every later phase loops
    # over one set.
    report = validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_standalone_request(ctx, FRAMES_PREPARING_KIND))
    if not report.ok:
        return _mark_failed(ctx, report.render_summary())

    return finish_phase(ctx)


def prepare_frames_node(state: RunState,
                        config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    is_asked_for = routing.is_phase_selected(state, PHASE_NUMBER)
    is_dry_run = bool(state.get("is_dry_run"))
    is_finished_in_this_run = ctx.store.get_phase(PHASE) == "complete"

    # STEP 0: a run that asks for neither this phase nor the item loop has no use for the item list.
    # `--phase` on a LATER phase then needs only that phase's own input file, and is not refused for
    # a file it would never read.
    is_queue_needed = routing.is_phase_selected(state, routing.FRAMES_PROCESSING_PHASE_NUMBER)
    if not is_asked_for and not is_queue_needed:
        return _build_empty_update()

    # STEP 1: a phase this run was not asked for, or already finished, or is only rehearsing, is READ
    # from its file. What is on DISK decides, judged by exactly what a fresh preparation is judged by.
    if not is_asked_for or is_finished_in_this_run or is_dry_run:
        on_disk = validation_service.validate_or_report_crash(
            ctx, attempt_request_builder.build_standalone_request(ctx, FRAMES_PREPARING_KIND))
        if on_disk.ok:
            ctx.log.info(f"phase {PHASE_NUMBER}: reading {output_contract.LOOP_ITEMS_FILE} from disk")

            return finish_phase(ctx)
        if is_dry_run:
            ctx.log.info(f"phase {PHASE_NUMBER}: [dry-run] no {output_contract.LOOP_ITEMS_FILE} yet, nothing to rehearse")

            return _build_empty_update()

        phase_file = phase_file_service.build_phase_file_path(ctx.paths, PHASE_NUMBER)

        return _mark_failed(ctx,
                            f"{display_path_utils.render_path(phase_file)} is missing "
                            f"or invalid ({on_disk.render_summary()}). It is written by phase {PHASE_NUMBER}: "
                            f"run `--phase {PHASE_NUMBER}` first.")

    # STEP 2: prepare the frames in plain code, write and judge the numbered file, and log how long it took
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    update = _prepare_fresh(ctx)
    performance_log_service.record_phase(ctx, PHASE, update["phases"][PHASE], handles)

    return update
