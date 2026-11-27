# <!-- sync: seed/utils.json_store_utils v1 -->
"""Reading and writing the orchestrator's json state files.

`write_json` is atomic - a temp file in the same directory, fsynced, then `os.replace`. That is not
belt and braces: `run.json` is written after every attempt, and a half-written state file makes a
resume worse than no state file at all. Never append-build a state file; write it wholesale.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def format_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path,
               payload: Any,
               default: Callable[[Any], Any] | None = None) -> None:
    """Temp file in the same directory, fsync, then rename. `default` writes a value JSON has no form for."""

    # Wholesale and atomic, never append-built: a run interrupted mid-write must leave either the old
    # file or the new one, never half of either.

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8",
                                         suffix=".tmp")
    try:
        json.dump(payload, handle, indent=2, sort_keys=False, default=default)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
