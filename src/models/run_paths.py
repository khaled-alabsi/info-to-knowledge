# <!-- sync: seed/models.run_paths v2 -->
"""Resolving the one set of paths every other module needs.

Everything is derived from two inputs - the repository root and the target name - so no other module
has to know how the artifact directory is laid out, and a relocation is one constructor argument
rather than a search for string concatenations.

**Add this playbook's own artifacts as properties here**, one per file, deriving the name from a
constant in `output_contract.py`. A path built by concatenation at a call site is a second definition of the
layout, and the two drift the first time either changes.

Data only: this derives paths and creates none of them. Creating the directories is a side effect,
so it lives in `services/run_paths_service.py` - a model that makes directories is a service
wearing the wrong name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from models import output_contract

__all__ = ["RunPaths"]


@dataclass(frozen=True)
class RunPaths:
    repository: Path
    module: str
    # None (the default) keeps the standard layout. Set to relocate the whole artifact tree - the
    # caller must then pass the SAME value on every later command against this run (status, logs,
    # reset, ...), exactly as it already must for the target name: nothing here is persisted and
    # re-read, so a resume that drops it looks in the wrong place.
    output_root_override: Path | None = None

    @property
    def output_root(self) -> Path:
        if self.output_root_override is not None:
            return self.output_root_override

        return self.repository / output_contract.ARTIFACT_DIR_NAME / self.module

    @property
    def runs_root(self) -> Path:
        return self.output_root / output_contract.RUNS_DIR_NAME

    @property
    def loop_items(self) -> Path:
        return self.output_root / output_contract.LOOP_ITEMS_FILE

    @property
    def summary(self) -> Path:
        """The one document phase 4 writes, reading across every frame phase 2 produced."""

        return self.output_root / output_contract.SUMMARY_FILE

    def run_dir(self,
                run_id: str) -> Path:
        return self.runs_root / run_id

    def run_state(self,
                  run_id: str) -> Path:
        return self.run_dir(run_id) / output_contract.RUN_STATE_FILE
