"""Whether a process is alive, asked the one way every service asks it."""

from __future__ import annotations

import os

__all__ = ["is_process_alive"]


def is_process_alive(pid: int | None) -> bool:
    """Whether a process of this id runs. A process another user owns is alive; no id, an id under 1 or one the system
    does not know is not."""

    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False

    return True
