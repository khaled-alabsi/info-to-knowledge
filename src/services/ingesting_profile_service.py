"""A run's ingesting profile: picked from its tags, kept in `ingesting-profile.json` in the run folder, and read by
phases 4 and 5. Pure file work; the profiles and their tags are
`models/ingesting_profile_vocabulary.py`."""

from __future__ import annotations

import json
from pathlib import Path

from models import ingesting_profile_vocabulary
from models import output_contract
from services import run_tags_service
from utils import json_store_utils

__all__ = ["find_profile_in_tags", "find_chosen_profile", "is_one_call_profile", "read_profile", "write_profile",
           "resolve_profile", "list_profiles", "list_profile_names", "read_document_language"]


def _build_profile_path(folder: Path) -> Path:
    return folder / output_contract.INGESTING_PROFILE_FILE


def find_profile_in_tags(tags: list[str]) -> str | None:
    """The profile the first tag naming one picks, by its name or an alias, letter case ignored; None when no tag
    names one."""

    names = {name.casefold(): name for name in ingesting_profile_vocabulary.PROFILE_ALIASES}
    names.update({alias.casefold(): name for name, aliases in ingesting_profile_vocabulary.PROFILE_ALIASES.items()
                  for alias in aliases})

    keys = [tag.casefold() for tag in run_tags_service.clean_tags(tags)]

    return next((names[key] for key in keys if key in names), None)


def read_profile(folder: Path) -> dict | None:
    """`{"profile", "chosenBy"}` of the run folder; None when it has no profile file, one that cannot be read, or one
    naming a profile that is no longer listed."""

    try:
        data = json.loads(_build_profile_path(folder).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return data if isinstance(data, dict) and data.get("profile") in ingesting_profile_vocabulary.PROFILES else None


def write_profile(folder: Path,
                  name: str,
                  chosen_by: str) -> None:
    """The run folder's profile set to `name`, chosen by `chosen_by`; a name or a chooser not in the list is refused
    by name."""

    if name not in ingesting_profile_vocabulary.PROFILES:
        raise ValueError(f"no ingesting profile {name!r}: the profiles are "
                         + ", ".join(ingesting_profile_vocabulary.PROFILES))
    if chosen_by not in ingesting_profile_vocabulary.CHOSEN_BY:
        raise ValueError(f"{chosen_by!r} is no chooser of a profile: " + ", ".join(ingesting_profile_vocabulary.CHOSEN_BY))
    json_store_utils.write_json(_build_profile_path(folder), {"profile": name, "chosenBy": chosen_by})


def find_chosen_profile(folder: Path) -> str | None:
    """The profile chosen for the run before any model call: the file's, else the one a tag of the run names; None when
    neither chose one. Phase 1 reads it, before phase 2 writes the tag's pick into the file."""

    recorded = read_profile(folder)
    if recorded:
        return recorded["profile"]

    return find_profile_in_tags(run_tags_service.read_tags(folder))


def is_one_call_profile(folder: Path) -> bool:
    """Whether the run's document is one call over the transcript (`ONE_CALL_PROFILES`), by the profile chosen for it."""

    return find_chosen_profile(folder) in ingesting_profile_vocabulary.ONE_CALL_PROFILES


def resolve_profile(folder: Path) -> str:
    """The profile the run's document is written in: its file's, or the default when it has none."""

    recorded = read_profile(folder)

    return recorded["profile"] if recorded else ingesting_profile_vocabulary.DEFAULT_PROFILE


def read_document_language(folder: Path) -> str:
    """The language the run's document is written in: the one phase 1 heard, for a profile written in the recording's
    own language, else English (`en`)."""

    if resolve_profile(folder) not in ingesting_profile_vocabulary.OWN_LANGUAGE_PROFILES:
        return ingesting_profile_vocabulary.DEFAULT_DOCUMENT_LANGUAGE
    path = folder / output_contract.TRANSCRIPT_FILE
    transcript = json_store_utils.read_json(path) if path.is_file() else {}

    return str(transcript.get("language") or ingesting_profile_vocabulary.DEFAULT_DOCUMENT_LANGUAGE)


def list_profiles() -> list[dict]:
    """Every profile with the tags that pick it and what kind of recording it is for, the default first, for the page
    and the CLI."""

    return [{"name": name, "aliases": list(aliases), "description": ingesting_profile_vocabulary.PROFILE_DESCRIPTIONS[name]}
            for name, aliases in ingesting_profile_vocabulary.PROFILE_ALIASES.items()]


def list_profile_names() -> list[str]:
    """Every profile's name, the default first."""

    return list(ingesting_profile_vocabulary.PROFILES)
