"""The state contracts. The test for a field: does a LATER node or routing function read it?

If not, it is a local variable. Nothing here holds a formatted prompt, a model name, a temperature
or a path that could be derived: those belong to config, to the prompt layer, or to a local.

The chain-pipeline form of the base state. It adds the two numbers a phase-file pipeline routes on,
`first_phase` and `last_phase`, which the run command sets from `--phase`, `--from` and `--to`.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict


def merge_phase_statuses(statuses: dict[str, str],
                         update: dict[str, str]) -> dict[str, str]:
    """The reducer of `phases`: a node returns only the phases it changed."""

    return {**statuses, **update}


class RunState(TypedDict, total=False):
    """The outer graph's contract. Every field names its reader."""

    phases: Annotated[dict[str, str], merge_phase_statuses] # read by routing: which phases ended how
    queue: list[str]                                        # read by the frames_processing node and by routing
    cursor: int                                             # read by the frames_processing node and by routing
    first_phase: int                                        # read by routing and the first two phase nodes: run it?
    last_phase: int                                         # read by the same: where does this run stop?

    written: Annotated[list[str], add]                      # read by the run command's closing report
    blocked: Annotated[list[str], add]                      # read by routing and by the run command's closing report
    is_drained: bool                                        # read by routing
    is_dry_run: bool                                        # read by every phase node
    is_audio: bool                                          # read by routing: phase 2 structures the speech, no frames


def new_run_state(first_phase: int,
                  last_phase: int,
                  is_dry_run: bool = False,
                  is_audio: bool = False) -> RunState:
    """The state a run starts from. The contract's own module is the one place that knows it, so a
    field added here reaches every caller."""

    return RunState(
        phases={},
        queue=[],
        cursor=0,
        first_phase=first_phase,
        last_phase=last_phase,
        written=[],
        blocked=[],
        is_drained=False,
        is_dry_run=is_dry_run,
        is_audio=is_audio,
    )


class AttemptState(TypedDict, total=False):
    """One attempt's subgraph. `findings` is what the runner reads and the retry message quotes."""

    slug: str
    attempt: int
    findings: list[dict]
    checks_run: list[str]
    summary: str
    carry_over: str           # a cut phase 3 follow-up's compacted thinking, for the next attempt
    is_last: bool             # whether this attempt is the last the step's budget allows: no next one reads carry_over
    is_accepted: bool         # set by the validate node: whether the attempt's record has no finding
