# <!-- sync: seed/graph.nodes.retry_loop.nodes.snapshot_node v1 -->
"""Claim this attempt's log directory and snapshot what it may overwrite.

Taken BEFORE the worker runs, so the attempt's validator can see what the attempt changed.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from models.job_retry_context import JobRetryContext
from models.run_context import RunContext
from graph.graph_state import AttemptState
from services import attempt_log_service
from services.artifact_snapshot_service import Snapshot


def snapshot_node(state: AttemptState,
                  config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    job: JobRetryContext = config["configurable"]["job"]

    job.handles.log = attempt_log_service.claim_attempt_dir(ctx.run_dir, job.phase, state.get("slug"),
                                                            state["attempt"])
    job.handles.snapshot = (Snapshot.take(job.snapshot_targets, job.handles.log.input_snapshot)
                            if job.snapshot_targets else None)

    return {}
