"""The person's own lists in the page's run list: beside New, Running, Not finished and Done, lists
the person makes, names, renames and fills by dragging rows into them. Kept in `ui/lists.json` as
`[{"id", "name", "members": [key, ...]}]`, a member being a row's key as the page names it (an inbox video's path or a
run folder's key); a row stands in one list at most, and a row in a list leaves its status section.
"""

from __future__ import annotations

import config
from utils import json_store_utils

__all__ = ["LISTS_PATH", "set_lists_path", "read_lists", "save_lists"]

LISTS_PATH = config.UI_DIR / "lists.json"
# The longest name a list takes
_NAME_MOST_CHARS = 60


def set_lists_path(path) -> None:
    """Where the lists are kept; a test points it at its own folder."""

    global LISTS_PATH
    LISTS_PATH = path


def read_lists() -> list[dict]:
    """The lists as saved, in their order; none when nothing was saved or the file cannot be read."""

    try:
        lists = json_store_utils.read_json(LISTS_PATH) if LISTS_PATH.is_file() else []
    except (OSError, ValueError):
        return []

    return lists if isinstance(lists, list) else []


def save_lists(lists: list[dict]) -> list[dict]:
    """The lists saved as given, each name trimmed; refused by name when a list has no name, a name is too long, two
    lists share an id, or a row stands in two lists."""

    cleaned, seen_ids, seen_members = [], set(), set()
    for entry in lists:
        name = str(entry.get("name") or "").strip()
        list_id = str(entry.get("id") or "").strip()
        if not name or not list_id:
            raise ValueError("every list needs an id and a name")
        if len(name) > _NAME_MOST_CHARS:
            raise ValueError(f"the list name {name[:20]!r}… is longer than {_NAME_MOST_CHARS} characters")
        if list_id in seen_ids:
            raise ValueError(f"two lists share the id {list_id!r}")
        members = [str(member) for member in entry.get("members") or []]
        twice = seen_members.intersection(members)
        if twice:
            raise ValueError(f"{sorted(twice)[0]} stands in two lists; a row stands in one")
        seen_ids.add(list_id)
        seen_members.update(members)
        cleaned.append({"id": list_id, "name": name, "members": list(dict.fromkeys(members))})
    json_store_utils.write_json(LISTS_PATH, cleaned)

    return cleaned
