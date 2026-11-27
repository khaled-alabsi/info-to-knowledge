# <!-- sync: seed/graph.nodes.retry_loop.nodes.record_node v1 -->
"""Write the decision and append the attempt to the run store."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from models.job_retry_context import JobRetryContext
from models.run_context import RunContext
from graph.graph_state import AttemptState


def record_node(state: AttemptState,
                config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    job: JobRetryContext = config["configurable"]["job"]

    is_accepted = state["is_accepted"]
    job.handles.log.write_decision({**job.handles.result.as_dict(), "accepted": is_accepted,
                                    "attempt": state["attempt"]})
    ctx.store.record_attempt(job.phase, state.get("slug"), {
        "n": state["attempt"],
        "process": job.handles.result.as_dict(),
        "validation": {"ok": is_accepted, "findings": state.get("findings", []),
                       "checksRun": state.get("checks_run", [])},
        "accepted": is_accepted,
        "logDir": str(job.handles.log.directory.relative_to(ctx.run_dir)),
    })

    return {}
