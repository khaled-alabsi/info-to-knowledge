# <!-- sync: seed/graph.nodes.retry_loop.nodes.capture_node v1 -->
"""Copy what this attempt produced into its log directory.

Before validation: this is the only record of what a rejected attempt actually wrote.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from models.job_retry_context import JobRetryContext
from graph.graph_state import AttemptState


def capture_node(state: AttemptState,
                 config: RunnableConfig) -> dict:
    job: JobRetryContext = config["configurable"]["job"]

    job.handles.log.capture_output(job.snapshot_targets)

    return {}
