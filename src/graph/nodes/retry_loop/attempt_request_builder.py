# <!-- sync: seed/graph.nodes.retry_loop.attempt_request_builder v1 -->
"""The one place an attempt's request is assembled from the run, the job and the attempt state.

Two callers - the worker node and the validate node - need the same `AttemptRequest` for the same
attempt, and a dry run or a `verify` needs one with no job behind it. Assembling it here means no
caller pairs one job's kind with another's inputs, and what was asked is one loggable object.
"""

from __future__ import annotations

from graph.graph_state import AttemptState
from models.attempt_request import AttemptRequest
from models.job_retry_context import JobRetryContext
from models.run_context import RunContext

__all__ = ["build_attempt_request", "build_standalone_request", "describe_unit_name"]


def describe_unit_name(job: JobRetryContext,
                       slug: str | None) -> str:
    """How the console names the job's unit: its slug, else its phase."""

    return slug or job.phase


def build_standalone_request(ctx: RunContext,
                             job_kind: str,
                             inputs: dict | None = None) -> AttemptRequest:
    """A one-off request with no job behind it - a dry run rendering what a worker WOULD be sent, or a
    verify judging disk right now. Attempt 1, no findings."""

    return AttemptRequest(job_kind=job_kind,
                          attempt=1,
                          repository=ctx.repository,
                          module=ctx.module,
                          paths=ctx.paths,
                          ignore=ctx.ignore,
                          inputs=inputs or {})


def build_attempt_request(ctx: RunContext,
                          job: JobRetryContext,
                          state: AttemptState) -> AttemptRequest:
    """This attempt, as data: the run facts a prompt is written from, the job's own description, and
    what the last attempt got wrong. The one place the three meet, so no caller assembles them by
    hand and none can pass a mismatched pair."""

    return AttemptRequest(job_kind=job.job_kind,
                          attempt=state["attempt"],
                          repository=ctx.repository,
                          module=ctx.module,
                          paths=ctx.paths,
                          ignore=ctx.ignore,
                          inputs=job.inputs,
                          findings=state.get("findings", []),
                          carry_over=state.get("carry_over", ""))
