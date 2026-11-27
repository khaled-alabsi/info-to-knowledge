"""Which role each `/api` route of `i2k serve` needs: the one table the server checks
every request against, once any account exists."""

from __future__ import annotations

import re

from models import account_role_vocabulary

__all__ = ["OPEN_PATHS", "find_needed_role", "is_role_allowed", "find_request_run", "is_admin_path"]

# Signing in, out, and asking who is signed in: open to everyone, or nobody could sign in
OPEN_PATHS = ("/api/auth/login", "/api/auth/logout", "/api/auth/me")
# Paths an admin alone may change: the accounts, the projects and groups, the settings, the workers' instructions and
# the setup wizard's writes
_ADMIN_PREFIXES = ("/api/accounts", "/api/settings", "/api/instructions", "/api/setup", "/api/projects", "/api/groups")
_READ_METHODS = ("GET", "HEAD", "OPTIONS")


def find_needed_role(method: str,
                     path: str) -> str | None:
    """The role a request needs, or None when it needs none: the page itself and signing in are open, the accounts
    are an admin's even to read, a read elsewhere a viewer's, a change of the settings or the setup an admin's, and
    every other change an operator's."""

    if not path.startswith("/api/") or path in OPEN_PATHS:
        return None
    if path.startswith("/api/accounts"):
        return account_role_vocabulary.ADMIN
    if method.upper() in _READ_METHODS:
        return account_role_vocabulary.VIEWER
    if path.startswith(_ADMIN_PREFIXES):
        return account_role_vocabulary.ADMIN

    return account_role_vocabulary.OPERATOR


def is_role_allowed(role: str,
                    needed: str) -> bool:
    """Whether an account of `role` may do what `needed` may; an unknown role may do nothing."""

    roles = account_role_vocabulary.ROLES

    return role in roles and roles.index(role) >= roles.index(needed)


# A route about one run folder: `/api/runs/<root>/<name>/…`; `display-names` is a list route, of no one run
_RUN_PATH = re.compile(r"^/api/runs/[^/]+/([^/]+)")
_LIST_ROUTES = ("/api/runs/display-names",)


def find_request_run(path: str) -> str | None:
    """The run folder's name a request is about, from its path; None for a route of no one run."""

    if path in _LIST_ROUTES:
        return None
    match = _RUN_PATH.match(path)

    return match.group(1) if match else None


def is_admin_path(path: str) -> bool:
    """Whether the path is one only an admin account reaches: the accounts, the projects' management, the settings and
    the setup, whatever role a project gives."""

    return path.startswith(_ADMIN_PREFIXES)
