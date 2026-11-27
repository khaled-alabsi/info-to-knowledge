"""How one chain call ended, as its result records it and the response check reads it: the host replied, or the call
raised. Never whether the reply is good: a validator judges that, from disk."""

from __future__ import annotations

__all__ = ["CALL_COMPLETED", "CALL_ERROR"]

CALL_COMPLETED = "completed"
CALL_ERROR = "error"
