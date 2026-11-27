"""A finished audio run's document written again in another ingesting profile, inside the same run:
the document it has is set aside whole as a version, and phase 4 runs again from the
same notes.

Only a profile that needs the same phases 1 to 3 qualifies (`profile_convert_service.select_first_phase` is 4): the
two profiles prepare the notes in the same steps. `takeaway` never does, either way: it has no phase 2 or 3 of its own.
A version is moved, never deleted, into `04_versions/<n>/`: phase 4's files and call logs, the published folder,
phase 7's folder and request, and the profile it was written in. Another profile, a run in another folder, is
`profile_convert_service`'s.
"""

from __future__ import annotations

import json
from pathlib import Path

from models import ingesting_profile_vocabulary
from models import output_contract
from models.run_paths import RunPaths
from services import document_pass_service
from services import ingesting_profile_service
from services import phase_file_service
from services import profile_convert_service
from services import recording_medium_service
from services import run_reset_service

__all__ = ["VERSIONS_DIR", "list_appendable_profiles", "set_document_aside", "list_set_aside_versions",
           "list_document_versions"]

# The documents a run wrote before, each in a folder of its number
VERSIONS_DIR = "04_versions"
# What a set-aside version says of itself
_VERSION_FILE = "version.json"
_FINALIZE = output_contract.PHASE_NUMBERS[output_contract.FINALIZE_PHASE]


def _build_paths(folder: Path) -> RunPaths:
    return RunPaths(repository=folder, module=folder.name, output_root_override=folder)


def _is_audio_document_run(folder: Path) -> bool:
    paths = _build_paths(folder)
    frames_number = output_contract.PHASE_NUMBERS[output_contract.FRAMES_PREPARING_PHASE]

    return (paths.summary.is_file() and phase_file_service.has_phase_file(paths, frames_number)
            and phase_file_service.read_phase_file(paths, frames_number).get("medium") == output_contract.AUDIO_MEDIUM)


def list_appendable_profiles(folder: Path) -> list[str]:
    """The profiles a finished audio run can write its document in again inside its own folder: every profile but its
    own and a one-call one, whose notes phase 3 prepares as for its own; none for a run that is no finished audio run
    or was written in a one-call profile."""

    current = ingesting_profile_service.resolve_profile(folder)
    if not _is_audio_document_run(folder) or current in ingesting_profile_vocabulary.ONE_CALL_PROFILES:
        return []

    return [name for name in ingesting_profile_vocabulary.MODEL_PICKABLE_PROFILES
            if name != current and profile_convert_service.select_first_phase(folder, name) == _FINALIZE]


def _list_version_dirs(folder: Path) -> list[Path]:
    root = folder / VERSIONS_DIR

    return sorted((path for path in root.glob("*") if path.is_dir() and path.name.isdigit()),
                  key=lambda path: int(path.name)) if root.is_dir() else []


def _list_moved(folder: Path) -> list[Path]:
    """What phase 4 and later wrote that a version keeps: phase 4's files, folders and call logs, the published
    folder, phase 7's folder and request, and the profile."""

    phase_4 = run_reset_service.PHASE_OUTPUTS[_FINALIZE]
    phase_7 = run_reset_service.PHASE_OUTPUTS[output_contract.PHASE_NUMBERS[output_contract.DOCUMENT_RESHAPING_PHASE]]
    names = [*phase_4.files, *phase_4.dirs, *phase_7.dirs, output_contract.RUN_DOCUMENT_PASS_FILE,
             output_contract.INGESTING_PROFILE_FILE]
    moved = [folder / name for name in names if (folder / name).exists()]
    moved += run_reset_service.list_published_folders(folder)
    moved += run_reset_service.list_finalize_calls(folder)

    return moved


def set_document_aside(folder: Path,
                       profile: str) -> Path:
    """The run's document and everything phase 4 and later wrote moved into the next `04_versions/<n>/`, phase 4 and
    later forgotten, and `profile` written as the run's, so `run --from 4` writes the document again; refused by name
    when the profile does not qualify (`list_appendable_profiles`) or a run writes into the folder."""

    allowed = list_appendable_profiles(folder)
    if profile not in allowed:
        raise ValueError(f"{folder.name} cannot write its document again as {profile!r} in this run: "
                         + (f"it can as {', '.join(allowed)}" if allowed else "it is no finished audio run whose "
                            "notes another profile reads as they are"))
    if run_reset_service.list_live_runs(folder):
        raise ValueError(f"a run writes into {folder.name} now; wait for it to end")

    # 1. everything the document came from, moved whole under the version's number
    current = ingesting_profile_service.resolve_profile(folder)
    target = folder / VERSIONS_DIR / str(len(_list_version_dirs(folder)) + 1)
    target.mkdir(parents=True)
    for path in _list_moved(folder):
        destination = target / path.relative_to(folder)
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
    published = run_reset_service.list_published_folders(target)
    (target / _VERSION_FILE).write_text(json.dumps({
        "profile": current, "document": f"{published[0].name}/{published[0].name}.md" if published else ""},
        indent=2) + "\n", encoding="utf-8")

    # 2. phase 4 and later forgotten, as a reset to 3 does; the new profile the run's
    run_reset_service.reset_to_phase(_build_paths(folder), _FINALIZE - 1)
    ingesting_profile_service.write_profile(folder, profile, "convert")

    return target


def list_set_aside_versions(folder: Path) -> list[dict]:
    """Each document set aside, oldest first, as {"label", "path"}: its profile, and its published document's path in
    the run folder as the version left it (after any clean up or rewrite)."""

    versions = []
    for version_dir in _list_version_dirs(folder):
        data = json.loads((version_dir / _VERSION_FILE).read_text(encoding="utf-8"))
        document = version_dir / str(data.get("document") or "")
        if data.get("document") and document.is_file():
            versions.append({"label": str(data.get("profile") or "earlier"),
                             "path": document.relative_to(folder).as_posix()})

    return versions


def _describe_original(folder: Path) -> str:
    """The name of the document as phase 4 wrote it, by how it was made (the owner: "give the original document its
    processing name, like markdown conversion (original)"): a PDF's processing, read from the medium its run chose and
    from phase 1's cleanup record, which only a PDF's reading writes; any other run's ingesting profile."""

    medium = recording_medium_service.read_chosen_medium(folder)
    pdf_mode = next((mode for mode, chosen in output_contract.PDF_MODE_MEDIUMS.items() if chosen == medium), "")
    if not pdf_mode and (folder / output_contract.PDF_CLEANUP_FILE).is_file():
        pdf_mode = output_contract.PDF_MODE_PAGES
    processing = pdf_mode.replace("-", " ") if pdf_mode else ingesting_profile_service.resolve_profile(folder)

    return f"{processing} (original)"


def list_document_versions(folder: Path,
                           document: Path | None) -> list[dict]:
    """Every version of the run's document for the Open document menu, oldest first, each {"number", "label",
    "path"}: the documents set aside, each by its profile, then the current one's (as phase 4 wrote it, named by how it
    was made, "markdown conversion (original)", and after each clean up, rewrite or knowledge pass,
    `document_pass_service`), its first labelled by its profile when others stand before it. `document` is the published
    document, None while phase 4 writes it again. A run with one document lists it alone, so the menu shows the same
    rows before and after a pass; none without a document."""

    earlier = list_set_aside_versions(folder)
    is_published = document is not None and document.is_file()
    current = (document_pass_service.list_document_versions(output_root=folder, document=document)
               if is_published else [])
    if not earlier and not current and is_published:
        return [{"number": 1, "label": _describe_original(folder), "path": document.relative_to(folder).as_posix()}]
    if current and not earlier:
        current[0]["label"] = _describe_original(folder)
    if not earlier:
        return current
    profile = ingesting_profile_service.resolve_profile(folder)
    if current:
        current[0]["label"] = profile
    elif is_published:
        current = [{"label": profile, "path": document.relative_to(folder).as_posix()}]

    return [{**version, "number": number} for number, version in enumerate([*earlier, *current], start=1)]
