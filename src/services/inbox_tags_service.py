"""The tags given to an inbox video before its first run, kept in `ui/inbox-tags.json` by the video's path, so the new
video's page shows them again after a refresh. Pure file work. When the run starts, the page sends them on the run line
(`--tag`), which writes them into the run folder's own `run-tags.json`."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import config
from models.ui_errors import UnreadableTagsError
from services import run_tags_service
from utils import json_store_utils

__all__ = ["STORE_PATH", "set_store_path", "read_video_tags", "write_video_tags", "move_video_tags"]

STORE_PATH = config.UI_DIR / "inbox-tags.json"

_LOG = logging.getLogger(__name__)


def set_store_path(path: Path) -> None:
    """Where the tags are kept; a test points it at its own folder."""

    global STORE_PATH
    STORE_PATH = path


def _read_store() -> dict:
    """Every video's tags; none when no store is kept yet. A store that is there and does not read raises
    `UnreadableTagsError`: a write would otherwise save it with one video's tags, over every other video's."""

    try:
        store = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise UnreadableTagsError(f"{STORE_PATH} does not read ({exc}); repair or remove it") from exc
    if not isinstance(store, dict):
        raise UnreadableTagsError(f"{STORE_PATH} holds no table of tags; repair or remove it")

    return store


def read_video_tags(video: Path) -> list[str]:
    """The tags given to the inbox video; empty when it has none."""

    try:
        tags = _read_store().get(str(video))
    except UnreadableTagsError as exc:
        # The page shows no tags; the store stays as it is until a write is asked for
        _LOG.warning("%s: no tags are shown for %s (%s)", STORE_PATH, video, exc)

        return []

    return [str(tag) for tag in tags] if isinstance(tags, list) else []


def write_video_tags(video: Path,
                     tags: list[str]) -> list[str]:
    """Every tag the inbox video is to have, cleaned as a run's tags are; an empty list takes its entry out. Returns
    the tags kept."""

    store = _read_store()
    cleaned = run_tags_service.clean_tags(tags)
    if cleaned:
        store[str(video)] = cleaned
    else:
        store.pop(str(video), None)
    json_store_utils.write_json(STORE_PATH, store)

    return cleaned


def move_video_tags(video: Path,
                    target: Path) -> None:
    """The tags of an inbox video renamed to `target` kept under its new path, in one write. A store that does not read
    raises `UnreadableTagsError`, so the tags are not dropped with the old path."""

    store = _read_store()
    tags = store.pop(str(video), None)
    if tags and target != video:
        store[str(target)] = tags
        json_store_utils.write_json(STORE_PATH, store)
