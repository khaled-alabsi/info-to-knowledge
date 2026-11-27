"""The projects of `i2k serve`: which project each run folder is in, and the roles each project gives to accounts and
to groups.

Kept in `ui/projects.json` beside the accounts. A run folder the file does not list is in the default project, so
every run made before projects existed stays where it was. An identity's role in a project is the highest the project
gives it, by its name or by any of its groups (the single sign-on pattern: the groups come from `group_service` now,
from a token's group claim later); in the default project it is at least its account's own role. Another project is
open only to the identities it gives a role. An admin account may see and do everything in every project.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from models import account_role_vocabulary
from models.ui_errors import AccountError
from services import group_service
from services import job_queue_service
from utils import json_store_utils

__all__ = ["PROJECTS_FILE", "DEFAULT_PROJECT", "list_projects", "add_project", "rename_project", "remove_project",
           "set_member", "set_group_role", "move_run", "find_run_project", "find_role", "list_visible_projects"]

PROJECTS_FILE = "projects.json"
# Where every run is that no project lists
DEFAULT_PROJECT = "default"
_DEFAULT_TITLE = "Default"
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_lock = threading.Lock()


def _build_path() -> Path:
    return job_queue_service.QUEUE_DIR / PROJECTS_FILE


def _load() -> dict[str, Any]:
    """The file, with the default project always in it."""

    path = _build_path()
    record = json_store_utils.read_json(path) if path.is_file() else {}
    record = record if isinstance(record, dict) else {}
    projects = record.get("projects") if isinstance(record.get("projects"), dict) else {}
    projects.setdefault(DEFAULT_PROJECT, {"title": _DEFAULT_TITLE, "members": {}, "groups": {}})
    runs = record.get("runs") if isinstance(record.get("runs"), dict) else {}

    return {"projects": projects, "runs": runs}


def _save(record: dict[str, Any]) -> None:
    path = _build_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store_utils.write_json(path, record)


def _find_project(record: dict[str, Any],
                  slug: str) -> dict[str, Any]:
    if slug not in record["projects"]:
        raise AccountError(f"no project {slug!r}")

    return record["projects"][slug]


def list_projects() -> list[dict[str, Any]]:
    """Every project as `{"slug", "title", "members", "groups", "runs"}`, the default first, then by title: `members`
    and `groups` the roles it gives by name; `runs` counts the run folders the file puts in it (the default's are every
    other run, so it counts none)."""

    record = _load()
    counts: dict[str, int] = {}
    for slug in record["runs"].values():
        counts[slug] = counts.get(slug, 0) + 1
    projects = [{"slug": slug, "title": project.get("title", slug), "members": dict(project.get("members") or {}),
                 "groups": dict(project.get("groups") or {}), "runs": counts.get(slug, 0)}
                for slug, project in record["projects"].items()]

    return sorted(projects, key=lambda project: (project["slug"] != DEFAULT_PROJECT, project["title"].casefold()))


def add_project(title: str) -> str:
    """A new project of no member and no run; its slug, made from its title. Refused by name when the title is empty
    or a project has that slug."""

    cleaned = title.strip()
    slug = _NOT_SLUG.sub("-", cleaned.casefold()).strip("-")
    if not slug:
        raise AccountError("a project needs a title with a letter or a digit")
    with _lock:
        record = _load()
        if slug in record["projects"]:
            raise AccountError(f"a project {slug!r} exists")
        record["projects"][slug] = {"title": cleaned, "members": {}, "groups": {}}
        _save(record)

    return slug


def rename_project(slug: str,
                   title: str) -> None:
    """The project's title changed; its slug, and so its runs and members, stay."""

    if not title.strip():
        raise AccountError("a project needs a title")
    with _lock:
        record = _load()
        _find_project(record, slug)["title"] = title.strip()
        _save(record)


def remove_project(slug: str) -> None:
    """The project gone, its runs back in the default project; the default project is never removed."""

    if slug == DEFAULT_PROJECT:
        raise AccountError("the default project holds every run no other project holds, and stays")
    with _lock:
        record = _load()
        _find_project(record, slug)
        del record["projects"][slug]
        record["runs"] = {run: project for run, project in record["runs"].items() if project != slug}
        _save(record)


def _set_grant(slug: str,
               kind: str,
               name: str,
               role: str | None) -> None:
    """The project's grant of `kind` ("members" or "groups") to `name` set to `role`, or taken away with no role."""

    if role is not None and role not in account_role_vocabulary.ROLES:
        raise AccountError(f"no role {role!r}: the roles are " + ", ".join(account_role_vocabulary.ROLES))
    with _lock:
        record = _load()
        grants = _find_project(record, slug).setdefault(kind, {})
        if role is None:
            grants.pop(name, None)
        else:
            grants[name] = role
        _save(record)


def set_member(slug: str,
               account: str,
               role: str | None) -> None:
    """`account` given `role` in the project, or, with no role, no longer given one by name (in the default project,
    back to its account's own role)."""

    _set_grant(slug, "members", account, role)


def set_group_role(slug: str,
                   group: str,
                   role: str | None) -> None:
    """Every account of `group` given `role` in the project, or, with no role, the group's grant taken away."""

    _set_grant(slug, "groups", group, role)


def move_run(run: str,
             slug: str) -> None:
    """The run folder named `run` put in the project `slug`."""

    with _lock:
        record = _load()
        _find_project(record, slug)
        if slug == DEFAULT_PROJECT:
            record["runs"].pop(run, None)
        else:
            record["runs"][run] = slug
        _save(record)


def find_run_project(run: str) -> str:
    """The project the run folder named `run` is in."""

    record = _load()
    slug = record["runs"].get(run, DEFAULT_PROJECT)

    return slug if slug in record["projects"] else DEFAULT_PROJECT


def find_role(account: dict[str, Any],
              slug: str) -> str | None:
    """The role an identity (`{"name", "role"}`, and `"groups"` when its sign-in names them, as a single sign-on token
    does; else its groups in `group_service`) has in the project `slug`: admin for an admin account, else the highest
    the project gives it by name or by any of its groups, a grant by name standing over its account's own role in the
    default project; None where the project gives it nothing."""

    if account.get("role") == account_role_vocabulary.ADMIN:
        return account_role_vocabulary.ADMIN
    project = _load()["projects"].get(slug)
    if project is None:
        return None
    name = str(account.get("name") or "")
    groups = account["groups"] if "groups" in account else group_service.list_account_groups(name)
    group_roles = [(project.get("groups") or {}).get(group) for group in groups]
    by_name = (project.get("members") or {}).get(name)
    own = by_name if by_name is not None else (account.get("role") if slug == DEFAULT_PROJECT else None)
    given = [role for role in (own, *group_roles) if role in account_role_vocabulary.ROLES]

    return max(given, key=account_role_vocabulary.ROLES.index) if given else None


def list_visible_projects(account: dict[str, str]) -> list[str]:
    """The slugs of the projects `account` may see."""

    return [project["slug"] for project in list_projects() if find_role(account, project["slug"]) is not None]
