"""Driving the chain attempt subgraph until the record is accepted or the budget is spent.

A loop here and not a cycle in the subgraph: attempts are counted from the store on disk, and a cycle would make a
checkpoint a second source of that count.

There is no repair path: a chain's reply is one object the model returns whole, so a rejected attempt is asked
again, with the findings of the attempt before it in the request for the message builder to state.
"""

from __future__ import annotations

from agent_client.agent_errors import ControlStop
from graph.nodes.chain_attempt_loop import chain_attempt_loop_graph
from graph.nodes.retry_loop import attempt_request_builder
from models.job_retry_context import JobRetryContext, JobRetryOutcome
from models.run_context import RunContext
from services import control_signal_service
from utils import finding_text_utils

__all__ = ["run_chain_attempt_loop"]


def run_chain_attempt_loop(ctx: RunContext,
                           job: JobRetryContext,
                           slug: str | None,
                           max_attempts: int) -> JobRetryOutcome:
    """Drive the subgraph until accepted or out of budget."""

    name = attempt_request_builder.describe_unit_name(job, slug)

    # STEP 1: the budget is per life of the run; the attempt numbers go on from the run's total, so no attempt's logs
    # are written over (a frame blocked in one life was once never asked again on the resume)
    total = ctx.store.count_attempts(job.phase, slug)
    used = ctx.store.count_attempts_this_life(job.phase, slug)
    # The last refusal goes with a spent budget too: a frame blocked on it keeps why
    if used >= max_attempts:
        return JobRetryOutcome(is_accepted=False, attempts=total, findings=ctx.store.read_last_findings(job.phase, slug),
                               summary="retry budget already spent", decision="exhausted")

    # STEP 2: a new life starts from the rejection that ended the last one, so the retry knows why
    is_new_life = used == 0 and total > 0
    findings: list[dict] = ctx.store.read_last_findings(job.phase, slug) if is_new_life else []
    summary = ""
    carry_over = ""

    # STEP 3: one graph for the attempts of this job: it holds no state of its own between invocations
    graph = chain_attempt_loop_graph.build_chain_attempt_graph(ctx, job)
    # STEP 4: the attempts, each asked again with the findings of the one before it
    last = total + max_attempts - used
    for attempt in range(total + 1, last + 1):
        # Safe to stop here: no attempt has started, so nothing is charged.
        if ctx.control_check is not None and ctx.control_check() == control_signal_service.STOP:
            raise ControlStop(f"stopped before attempt {attempt} of {name}")

        result = graph.invoke({"slug": slug, "attempt": attempt, "findings": findings, "carry_over": carry_over,
                               "is_last": attempt == last})

        findings = result["findings"]
        summary = result["summary"]
        # The newest compaction is kept: an attempt that is refused for another reason still settled nothing new
        carry_over = result.get("carry_over") or carry_over
        if result["is_accepted"]:
            return JobRetryOutcome(is_accepted=True, attempts=attempt, findings=[], summary=summary, decision="accepted")
        # Why it was rejected, on the console, for every phase that judges a call: without it a step asked three
        # times reads as a slow model rather than as a check to look at (the owner, 2026-09-28). A caller that names
        # the item itself prints it instead, so the same finding never appears twice.
        if not job.are_rejections_logged_by_caller:
            ctx.log.warning(f"  {name} attempt {attempt} rejected: {finding_text_utils.render_for_console(findings)}")

    # Every attempt was rejected: the budget is spent (the loop has run at least once, so `attempt` is the last one)
    return JobRetryOutcome(is_accepted=False, attempts=attempt, findings=findings, summary=summary, decision="exhausted")
