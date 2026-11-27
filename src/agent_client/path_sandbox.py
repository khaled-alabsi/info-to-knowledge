# <!-- sync: seed/agent_client.path_sandbox v1 -->
"""Path resolution for every file the program opens by a name it built.

One function: a path is refused when it resolves outside the folder it is built under. No model-facing tool takes a
path.

The rule that matters: **resolve before you compare.** A prefix check on the unresolved string is
defeated by `../`.
"""

from __future__ import annotations

from pathlib import Path

from agent_client.agent_errors import SandboxError

__all__ = ["resolve_within"]


def resolve_within(root: Path,
                   path: str) -> Path:
    """Resolve `path` under `root` and refuse anything that lands outside."""

    if not path.strip():
        raise SandboxError("no path was given")

    root_resolved = root.resolve()
    # An absolute `path` replaces the root in the join. `strict=False`: a write target does not
    # exist yet. `..` is still collapsed, which is the point.
    target = (root_resolved / path).resolve()

    # An absolute path that lands INSIDE the root is accepted; one that lands outside is refused by this same
    # containment check. The boundary is containment: demanding a particular spelling of a contained path protects
    # nothing.
    if not target.is_relative_to(root_resolved):
        raise SandboxError(f"{path!r} resolves outside the permitted root")

    return target

