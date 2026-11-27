"""The roles an account of `i2k serve` holds, each allowed everything the one before it is."""

from __future__ import annotations

__all__ = ["VIEWER", "OPERATOR", "ADMIN", "ROLES"]

# Reads every run, document, log, the inbox and the queue
VIEWER = "viewer"
# Also starts, stops and resets runs, uploads, edits documents, tags, branches and moves recordings
OPERATOR = "operator"
# Also changes the settings, the setup and the accounts
ADMIN = "admin"
# Weakest first: a role may do what every role before it may
ROLES = (VIEWER, OPERATOR, ADMIN)

