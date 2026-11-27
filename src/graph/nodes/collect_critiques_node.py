"""Phase 6, the critique collection: every batch's critique of this run, collected into one rule set.

The program reads every `03_processed-frames/<batch>-critique.md`, groups the rules that repeat and counts the batches
and frames each one hit (`services/critique_collection_service.py`); ONE model call then words each group as a rule
for any meeting, and may drop a group that is one meeting's content. The program writes
`06_critique-collection/critique-collection.md` from the counts it worked out and the wording that came back.

It is the one phase that reads no meeting: a rule arrives generic, because the check that accepted its critique
refused a frame number inside the rule and a quote off the screens. That is what makes the file safe to read straight
into `agent-context/instructions/frames_processing.md`, which is the only reason the phase exists.

Held, never complete, while an earlier phase or any frame is blocked or failed, as phase 5 is: rules learned from a
run with a known hole teach the hole. With no critique on disk - the critique switched off, or every batch finding
nothing - the phase completes having written nothing, which is not a failure.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

import config as app_config
from graph.graph_state import RunState
from graph.nodes.retry_loop import attempt_request_builder
from graph.nodes.chain_attempt_loop import chain_attempt_loop_runner
from models import output_contract
from models import video_workflow_vocabulary
from models.job_retry_context import JobRetryContext
from models.run_context import RunContext
from models.timing_handles import TimingHandles
from services import call_log_service
from services import chain_dispatch_service
from services import dry_run_service
from services import critique_collection_service
from services import phase_file_service
from services import phase_hold_service
from services import performance_log_service
from services import video_workflow_service
from utils import chain_response_utils

PHASE = output_contract.CRITIQUE_COLLECTION_PHASE
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]
KIND = output_contract.CRITIQUE_COLLECTION_PHASE
SLUG = output_contract.CRITIQUE_COLLECTION_SLUG


def _mark_phase(ctx: RunContext,
                status: str,
                handles: TimingHandles | None) -> dict:
    performance_log_service.record_phase_end(ctx, PHASE, status, handles)

    return {"phases": {PHASE: status}}


def _record_off(ctx: RunContext,
                reason: str) -> dict:
    ctx.log.info(f"phase {PHASE_NUMBER}: off, {reason}")
    performance_log_service.record_phase_end(ctx, PHASE, "off", None)

    return {"phases": {PHASE: "off"}}


def collect_critiques_node(state: RunState,
                           config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    is_dry_run = bool(state.get("is_dry_run"))

    # STEP 1: an audio recording's flow, the switch, and the hold. "held" is no word a resume treats as done
    if PHASE_NUMBER in output_contract.AUDIO_OFF_PHASES and phase_file_service.is_audio_run(ctx.paths):
        return _record_off(ctx, f"{phase_file_service.describe_frameless_run(ctx.paths)} has no critique collection")
    if (PHASE_NUMBER in video_workflow_vocabulary.FAST_OFF_PHASES
            and video_workflow_service.is_fast_processing(ctx.paths.output_root)):
        return _record_off(ctx, "a fast-processing run has no critique collection")
    if not app_config.settings.critique_collection.is_on:
        return _record_off(ctx, "critiqueCollection.isOn is off")
    hold_reasons = phase_hold_service.list_hold_reasons(ctx.store, PHASE_NUMBER)
    if hold_reasons:
        ctx.log.info(f"phase {PHASE_NUMBER}: not started, because {'; '.join(hold_reasons)}")

        return {"phases": {PHASE: "held"}}

    # STEP 2: every critique of this run, grouped and counted by the PROGRAM
    handles = None if is_dry_run else performance_log_service.start_unit(ctx, PHASE, None)
    rules = critique_collection_service.read_run_rules(ctx.paths)
    groups = critique_collection_service.group_rules(rules)
    if not groups:
        ctx.log.info(f"phase {PHASE_NUMBER}: no critique of this run holds a rule; nothing to collect")
        # A rehearsal over a real run's record leaves it as it was
        if is_dry_run:
            return {"phases": {PHASE: "complete"}}

        return _mark_phase(ctx, "complete", handles)
    ctx.log.info(f"phase {PHASE_NUMBER}: {len(rules)} rule(s) from "
                 f"{len(critique_collection_service.list_critique_files(ctx.paths))} critique(s), {len(groups)} group(s)")

    # STEP 3: on a dry run, render what the call WOULD be sent, and call no model
    inputs = {"groups": groups}
    if is_dry_run:
        messages = chain_dispatch_service.build_messages(
            attempt_request_builder.build_standalone_request(ctx, KIND, inputs=inputs))
        system_text, human_text = chain_response_utils.render_prompt_as_text(messages)
        dry_run_service.describe_dry_run(ctx, PHASE, SLUG, system_text, human_text)

        # Complete in the graph only, as phases 4 and 5 do: a rehearsal over a real run's record leaves it as it was
        return {"phases": {PHASE: "complete"}}

    # STEP 4: one call per attempt, up to `budgets.maxAttempts`. A rule set not accepted blocks the phase: nothing later
    # reads it, so the run ends here with the critiques still on disk for the next try
    rule_set_path = critique_collection_service.build_rule_set_path(ctx.paths)
    job = JobRetryContext(phase=PHASE, job_kind=KIND,
                          snapshot_targets=[rule_set_path,
                                            call_log_service.build_call_log_path(ctx.paths, SLUG)],
                          inputs=inputs)
    # The call is the phase's one step, a unit of its own, so the phase line counts its call: without it the line said
    # "no model call" after a ten-minute call
    call_handles = performance_log_service.start_unit(ctx, PHASE, SLUG)
    outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=SLUG,
                                                               max_attempts=app_config.settings.budgets.max_attempts)
    performance_log_service.record_unit(ctx, call_handles, performance_log_service.PART_LEVEL,
                                        "accepted" if outcome.is_accepted else "blocked", [])
    if not outcome.is_accepted:
        return _mark_phase(ctx, "blocked", handles)
    kept = critique_collection_service.count_kept_rules(rule_set_path)
    ctx.log.info(f"phase {PHASE_NUMBER}: {rule_set_path.name} written, {kept} rule(s) kept")

    return _mark_phase(ctx, "complete", handles)
