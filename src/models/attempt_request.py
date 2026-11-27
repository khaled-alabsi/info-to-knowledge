# <!-- sync: seed/models.attempt_request v1 -->
"""One attempt of one job, described entirely as data. Everything the prompt service needs to build a
prompt and the validation service needs to judge one: which kind of job it is, the run facts a prompt
is written from, what that phase computed, the attempt number and what the last attempt got wrong.

Nothing callable, and no live handles - the store, the logger and the model stay on `RunContext`, so
a prompt builder structurally cannot reach them."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from models.run_paths import RunPaths
from services.ignore_list_service import IgnoreList

__all__ = ["AttemptRequest"]


@dataclass(frozen=True)
class AttemptRequest:
    # Which prompt builder and which validator - see `src/prompts/` and `services/validation_service.py`. NOT the phase:
    # two steps of one phase share a phase name and have nothing else in common.
    job_kind: str
    attempt: int
    repository: Path
    module: str
    paths: RunPaths
    ignore: IgnoreList
    # The facts this phase computed that its prompt or its validator needs - a coverage table, an
    # element count, the item record, the previously-known slugs.
    inputs: dict = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    # What a phase 3 follow-up's cut attempt had settled, compacted from its thinking; empty otherwise
    carry_over: str = ""
