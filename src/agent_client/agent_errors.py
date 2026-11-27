# <!-- sync: seed/agent_client.agent_errors v1 -->
"""What can go wrong inside a worker, as types. The toolkit's own vocabulary.

Separate from the playbook's `models/run_errors.py` on purpose: `agent_client/` is the portable half -
it has to drop into the next playbook unchanged, so it cannot reach into a host program's `models/`.

`ControlStop` is not a failure at all: it is an operator's decision, and it ends an attempt without
meaning the artifact is wrong. It lives here because a caller has to name it to catch it.
"""

from __future__ import annotations

__all__ = ["SandboxError", "ControlStop"]


class SandboxError(Exception):
    """A path escaped its permitted root. Raised before anything is opened or written."""


class ControlStop(Exception):
    """An operator asked the run to stop. Not an attempt - nothing is validated or recorded."""
