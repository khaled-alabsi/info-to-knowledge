# <!-- sync: seed/graph.nodes.retry_loop.nodes.validate_node v1 -->
"""The only node that decides anything: bytes on disk, after the worker is gone.

A validator that raises is a REJECTION, not a dead run - `validation_service.validate_or_report_crash`
turns the exception into a finding under `validation_service.CRASH_CODE`, so the attempt is rejected and asked again.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from graph.nodes.retry_loop import attempt_request_builder
from models.job_retry_context import JobRetryContext
from graph.graph_state import AttemptState
from models.run_context import RunContext
from models.validation_report import ValidationReport
from services import validation_service


def validate_node(state: AttemptState,
                  config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    job: JobRetryContext = config["configurable"]["job"]

    report: ValidationReport = validation_service.validate_or_report_crash(
        ctx, attempt_request_builder.build_attempt_request(ctx, job, state), job.handles.snapshot)
    job.handles.log.write_validation(report.as_dict())

    return {"findings": [f.as_dict() for f in report.findings],
            "is_accepted": not report.findings,
            "checks_run": report.checks_run,
            "summary": report.render_summary()}
