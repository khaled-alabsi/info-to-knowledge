"""How a path is shown on the console and in a log: from the repository root, never absolute."""

from __future__ import annotations

from pathlib import Path

import config

__all__ = ["render_path"]


def render_path(path: Path | str) -> str:
    """`path` relative to the repository root; `~/...` outside it; as given when it is neither."""

    resolved = Path(path).expanduser().resolve()
    for root, prefix in ((config.WORKSPACE_ROOT, ""), (Path.home(), "~/")):
        if resolved.is_relative_to(root):
            return f"{prefix}{resolved.relative_to(root)}"

    return str(path)
