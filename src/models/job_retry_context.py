# <!-- sync: seed/models.job_retry_context v1 -->
"""What ONE JOB's retry loop needs that `RunContext` does not already hold.

`JobRetryContext` describes the **job**, as DATA: which phase it belongs to (whose model runs it), which kind of
prompt and validator it uses, what to snapshot, and the facts its phase computed.
Nothing else, and nothing callable - the prompt builder is looked up by kind in `src/prompts/` and the validator in
`services/validation_service.py`.

**It never re-declares a field `RunContext` already holds.** The repository, the output root, the run
directory, the store, the control check and the model all live on the run context, and
the driver takes both - `chain_attempt_loop_graph.build_chain_attempt_graph(ctx, job)`. Copying those in here looks
like decoupling and is not: it is a second declaration of the same facts, retyped at every
construction site, and it makes this carrier answer two questions at once - "what handles does the
run have?" and "what is this step's job?".

`JobRetryHandles` is the per-attempt scratch: the attempt's log, its snapshot and the call result. The JOB
holds it, so a validator that needs this attempt's snapshot reads it from here. Reassigned every attempt,
discarded with the job.
It is deliberately NOT module-level state keyed by attempt number: that shape breaks the moment two
items run concurrently, and it makes a single node impossible to test in isolation.

Carriers only. The request an attempt is built from is assembled in
`graph/nodes/retry_loop/attempt_request_builder.py`, which is why this module can live in the
carrier layer and be imported by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from models.chain_call_result import ChainCallResult
from services.attempt_log_service import AttemptLog
from services.artifact_snapshot_service import Snapshot

__all__ = ["JobRetryContext", "JobRetryHandles", "JobRetryOutcome"]


@dataclass
class JobRetryHandles:
    """One attempt's non-state handles."""

    log: AttemptLog | None = None
    snapshot: Snapshot | None = None
    result: ChainCallResult | None = None


@dataclass
class JobRetryContext:
    """One job's description. Passed alongside `RunContext`, never built by copying out of it."""

    # the phase this job belongs to; its model is the one the phase calls (`config.Settings.resolve_phase_model`)
    phase: str
    # Which prompt builder and which validator this job uses - see `src/prompts/` and `services/validation_service.py`.
    # NOT the phase: two steps of one phase share a phase name and have nothing else in common.
    job_kind: str
    # what to snapshot before, and capture after, each attempt
    snapshot_targets: list[Path]
    # The facts this phase computed that its prompt or its validator needs. Data, never a
    # callback: what a prompt was built from is then loggable and comparable across attempts.
    inputs: dict = field(default_factory=dict)
    # This job's per-attempt handles: the log, the snapshot and the worker result the
    # attempt writes. A validator that needs the snapshot reads it from here.
    handles: JobRetryHandles = field(default_factory=JobRetryHandles)
    # Whether the caller prints its own rejection line per item (a batch of frames names the frame that failed). The
    # attempt loop then stays quiet about the findings, so one rejection is not printed twice.
    are_rejections_logged_by_caller: bool = False


@dataclass
class JobRetryOutcome:
    is_accepted: bool
    attempts: int
    findings: list[dict] = field(default_factory=list)
    summary: str = ""
    decision: str = "exhausted"
