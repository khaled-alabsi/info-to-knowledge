# <!-- sync: seed/models.run_identity v1 -->
"""The four strings that say WHICH run this is: the identity block `RunStore.create` writes at the top of `run.json`.

No I/O, no behaviour: a value object, frozen, so it cannot be edited halfway through a run by
something holding a reference to it.

Three of its four fields are `RunPaths`' own - which is why `for_run` exists and why nothing should
construct one field by field. Re-typing `repository`, `module` and `output_root` beside a `RunPaths`
already in hand is a second definition of which module the run is about, and the two can disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.run_paths import RunPaths

__all__ = ["RunIdentity"]


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    repository: str
    module: str
    output_root: str

    @classmethod
    def for_run(cls,
                run_id: str,
                paths: RunPaths) -> RunIdentity:
        """The identity of a run over `paths`. The one place the three shared fields are stringified."""

        return cls(run_id=run_id, repository=str(paths.repository), module=paths.module,
                   output_root=str(paths.output_root))

    def as_dict(self) -> dict[str, str]:
        """The camelCase form the store writes, so the JSON key names have one definition."""

        return {"runId": self.run_id, "repository": self.repository,
                "module": self.module, "outputRoot": self.output_root}
