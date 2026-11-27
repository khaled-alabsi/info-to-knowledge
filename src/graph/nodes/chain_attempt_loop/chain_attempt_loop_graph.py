"""Assembly of the chain attempt subgraph. Wiring only; `chain_attempt_loop_runner.py` drives it.

    START -> snapshot -> invoke_chain -> capture -> validate -> record -> END

Four of the five nodes are the retry loop's (`graph/nodes/retry_loop/nodes/`): claiming the attempt directory,
capturing what the attempt wrote, judging bytes on disk and recording the decision do not depend on WHO did the work.
The middle node is this loop's own: one model call the program makes itself, with no tools.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.graph_state import AttemptState
from graph.nodes.chain_attempt_loop.nodes import invoke_chain_node
from graph.nodes.retry_loop.nodes import capture_node
from graph.nodes.retry_loop.nodes import record_node
from graph.nodes.retry_loop.nodes import snapshot_node
from graph.nodes.retry_loop.nodes import validate_node
from models.job_retry_context import JobRetryContext
from models.run_context import RunContext

__all__ = ["build_chain_attempt_graph"]


def build_chain_attempt_graph(ctx: RunContext,
                              job: JobRetryContext):
    """Compile the subgraph for the attempts of one job, over that job's handles."""

    builder = StateGraph(AttemptState)

    builder.add_node("snapshot", snapshot_node.snapshot_node)                # claim the log dir, copy job.snapshot_targets
    builder.add_node("invoke_chain", invoke_chain_node.invoke_chain_node)    # one model call, the call log, the record
    builder.add_node("capture", capture_node.capture_node)                   # copy what the attempt wrote into its log dir
    builder.add_node("validate", validate_node.validate_node)                # the kind's validator, the only decision
    builder.add_node("record", record_node.record_node)                      # decision.json, and the attempt into the store

    builder.add_edge(START, "snapshot")
    builder.add_edge("snapshot", "invoke_chain")
    builder.add_edge("invoke_chain", "capture")
    builder.add_edge("capture", "validate")
    builder.add_edge("validate", "record")
    builder.add_edge("record", END)

    return builder.compile().with_config({"configurable": {"ctx": ctx, "job": job}})
