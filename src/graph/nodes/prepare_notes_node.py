"""Phase 3 for an audio recording whose ingesting profile declares preparation steps:
the speech too dense for phase 4 alone is prepared in steps, the
items first, then each item in detail, then any view of the whole.

Each step is one call over the whole speech, or one call per item an earlier step found, handed only that item's
paragraphs and what the earlier step wrote about it. Each call's output is checked and written under `03_prepared/`.
A call whose file is there and passes its check is not asked again, so a resume asks only what is missing; a call that
spends its attempts blocks phase 3 and the run stops there, as a blocked frame stops a video's.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

import config as app_config
from graph.graph_state import RunState
from graph.nodes.chain_attempt_loop import chain_attempt_loop_runner
from graph.nodes.retry_loop import attempt_request_builder
from models import output_contract
from models.job_retry_context import JobRetryContext
from models.run_context import RunContext
from models.timing_handles import TimingHandles
from services import call_log_service
from services import chain_dispatch_service
from services import dry_run_service
from services import ingesting_profile_service
from services import performance_log_service
from services import prepared_file_service
from services import preprocessing_call_service
from services import preprocessing_step_service
from services import run_tags_service
from services import speech_file_service
from utils import chain_response_utils

PHASE = "frames_processing"
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]
KIND = "preprocessing"


def _mark_phase(ctx: RunContext,
                status: str,
                handles: TimingHandles | None) -> dict:
    performance_log_service.record_phase_end(ctx, PHASE, status, handles)

    return {"phases": {PHASE: status}}


def _run_call(ctx: RunContext,
              inputs: dict[str, Any],
              step_id: str,
              item_number: int | None) -> bool:
    """One call of step `step_id` (for item `item_number`, or the whole speech when None), up to `budgets.maxAttempts`
    attempts; whether its attempt was accepted."""

    slug = prepared_file_service.build_call_slug(step_id, item_number)

    job = JobRetryContext(phase=PHASE, job_kind=KIND,
                          snapshot_targets=[prepared_file_service.build_prepared_path(ctx.paths, step_id, item_number),
                                            call_log_service.build_call_log_path(ctx.paths, slug)],
                          inputs=inputs)
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=slug,
                                                               max_attempts=app_config.settings.budgets.max_attempts)

    return outcome.is_accepted


def _build_dry_run_result() -> dict:
    """A rehearsal's answer to the graph: phase 3 passed over, nothing written into the run record."""

    return {"phases": {PHASE: "complete"}}


def _render_first_due_call(ctx: RunContext,
                           step_id: str,
                           due_calls: list[dict],
                           step_inputs: dict[str, Any]) -> dict:
    """On a dry run, what the first call still due WOULD be sent, rendered, and no model called: a later call is built
    from the files the calls before it wrote."""

    call = due_calls[0]
    messages = chain_dispatch_service.build_messages(
        attempt_request_builder.build_standalone_request(ctx, KIND, inputs={**step_inputs, **call}))
    system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
    dry_run_service.describe_dry_run(ctx, PHASE, prepared_file_service.build_call_slug(step_id, call["item_number"]),
                                     system_text, human_text)

    return _build_dry_run_result()


def _run_step_calls(ctx: RunContext,
                    step_id: str,
                    due_calls: list[dict],
                    step_inputs: dict[str, Any],
                    handles: TimingHandles | None) -> dict | None:
    """Each call of one step that is still due, in order. The update that ends the phase early: a call was not accepted
    and blocks the phase. None when every call of the step is accepted."""

    for call in due_calls:
        item_number = call["item_number"]
        slug = prepared_file_service.build_call_slug(step_id, item_number)
        inputs = {**step_inputs, **call}
        if item_number is not None:
            ctx.log.info(f"  item {item_number} of {call['item_count']}: {call['item_title']}")
        if not _run_call(ctx, inputs, step_id, item_number):
            ctx.log.error(f"phase {PHASE_NUMBER} blocked: {slug} was not accepted; `--resume` asks it again")

            return {**_mark_phase(ctx, "blocked", handles), "blocked": [slug]}

    return None


def prepare_notes_node(state: RunState,
                       config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    is_dry_run = bool(state.get("is_dry_run"))

    # STEP 1: the steps the run's profile declares, over the paragraphs phase 2 wrote
    speech = speech_file_service.read_speech(ctx.paths)
    if speech is None:
        if is_dry_run:
            ctx.log.info(f"phase {PHASE_NUMBER}: nothing to render, phase 2 wrote no {output_contract.SPEECH_FILE} yet")

            return _build_dry_run_result()
        ctx.log.error(f"phase {PHASE_NUMBER} failed: {output_contract.SPEECH_FILE} is missing or unreadable. It is "
                      f"written by phase 2: run `--phase 2` first.")

        return _mark_phase(ctx, "failed", None)
    steps = preprocessing_step_service.list_due_steps(ctx.paths)
    profile = ingesting_profile_service.resolve_profile(ctx.paths.output_root)
    if not steps:
        ctx.log.info(f"phase {PHASE_NUMBER}: the ingesting profile {profile} prepares nothing for "
                     f"{len(speech['paragraphs'])} paragraph(s)")

        # A rehearsal records nothing: a real run would read the phase as done
        return _build_dry_run_result() if is_dry_run else _mark_phase(ctx, "complete", None)
    ctx.log.info("")
    ctx.log.info(f"phase {PHASE_NUMBER}: preparing the notes of {profile}, {len(steps)} step(s)")
    handles = None if is_dry_run else performance_log_service.start_unit(ctx, PHASE, None)
    detailed_step_ids = preprocessing_step_service.list_detailed_step_ids(steps)
    shared_inputs = {"profile": profile, "step_count": len(steps),
                     "tags": run_tags_service.read_tags(ctx.paths.output_root), "recording": ctx.module}

    # STEP 2: each step in order, each of its calls in order; a call already done is passed over
    for step_index, step in enumerate(steps):
        calls = preprocessing_call_service.list_step_calls(ctx.paths, steps, step_index, speech["paragraphs"])
        ctx.log.info(f"phase {PHASE_NUMBER}: {step.title}, step {step_index + 1} of {len(steps)}, {len(calls)} call(s)")
        step_inputs = {**shared_inputs, "step": preprocessing_step_service.describe_steps((step,))[0],
                       "step_number": step_index + 1, "is_every_paragraph_due": step.step_id in detailed_step_ids}
        due_calls = [call for call in calls if not preprocessing_call_service.is_call_done(ctx.paths, steps, step, call)]
        if is_dry_run and due_calls:
            return _render_first_due_call(ctx, step.step_id, due_calls, step_inputs)
        early_exit = _run_step_calls(ctx, step.step_id, due_calls, step_inputs, handles)
        if early_exit is not None:
            return early_exit

    # STEP 3: every call of every step accepted; a dry run over a phase already done records nothing
    if is_dry_run:
        ctx.log.info(f"phase {PHASE_NUMBER}: nothing to render, every call is done")

        return _build_dry_run_result()
    ctx.log.info(f"phase {PHASE_NUMBER}: the notes are prepared in {output_contract.PREPARED_DIR}/")

    return _mark_phase(ctx, "complete", handles)
