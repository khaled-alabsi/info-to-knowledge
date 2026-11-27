"""Many run folders' results in one zip, for the page's Manager tab: each run's finished document with its resources
folder, as phase 4 published it (`document_folder_service.build_document_dir`). The parts a person can ask for are
named in `DOWNLOAD_PARTS`; only the document is offered for now."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from models.run_paths import RunPaths
from services import document_folder_service
from services import profile_version_service
from services import run_artifact_service

__all__ = ["DOWNLOAD_PARTS", "DOCUMENT_PART", "VERSION_LATEST", "VERSION_ORIGINAL", "VERSION_ALL", "DOCUMENT_VERSIONS",
           "build_documents_zip", "list_versions"]

DOCUMENT_PART = "document"
# Which version of each run's document a download holds, when a run has several (the owner: asked which version, or
# all): the latest, as it is now; the original, before any change; or every version
VERSION_LATEST = "latest"
VERSION_ORIGINAL = "original"
VERSION_ALL = "all"
DOCUMENT_VERSIONS = (VERSION_LATEST, VERSION_ORIGINAL, VERSION_ALL)
# Every part a download may hold, with the words the page shows for it
DOWNLOAD_PARTS = {DOCUMENT_PART: "Final document with its resources folder"}
_RESOURCES_DIR = "resources"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _list_document_files(folder: Path) -> tuple[Path | None, list[Path]]:
    """The published folder and its document with every file under `resources/`; no folder and no file when the run has
    no published document."""

    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    if not paths.summary.is_file():
        return None, []
    document = document_folder_service.build_document_path(paths)
    document_dir = document.parent
    if not document.is_file():
        return None, []
    resources = sorted(path for path in (document_dir / _RESOURCES_DIR).rglob("*") if path.is_file())

    return document_dir, [document, *resources]


def list_versions(folder: Path) -> list[dict]:
    """The run's document versions, oldest first, each {"number", "label", "path"}, as the Open document menu lists
    them; empty when the run has no published document."""

    document_dir, files = _list_document_files(folder)
    if document_dir is None:
        return []

    return profile_version_service.list_document_versions(folder, files[0])


def _list_version_files(folder: Path,
                        document_dir: Path,
                        version: str) -> list[tuple[Path, str]]:
    """Each document file the zip holds for one run under `version`, with its name in the run's folder of the zip: the
    latest as the published document's own name; the original, or every version, by number and label
    (`guide-1-original.md`), as the run page's download names them. A run with one version gives it under its name."""

    document = document_dir / f"{document_dir.name}.md"
    if version == VERSION_LATEST:
        return [(document, document.name)]
    versions = profile_version_service.list_document_versions(folder, document)
    if len(versions) <= 1:
        return [(document, document.name)]
    chosen = versions[:1] if version == VERSION_ORIGINAL else versions

    return [(folder / row["path"], run_artifact_service.name_document_version(document=document, number=row["number"],
                                                                                label=row["label"])) for row in chosen]


def build_documents_zip(folders: list[Path],
                        version: str = "") -> tuple[bytes, list[str]]:
    """A zip holding, for each run folder, its document as `version` asks (the latest, as it is now, by default; the
    original, before any change; or every version) in a folder named after the document, with its `resources/` once,
    and the names of the run folders left out because they have no published document. Two runs whose documents share a
    name are told apart by their run folder's name."""

    buffer = io.BytesIO()
    skipped: list[str] = []
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w") as archive:
        for folder in folders:
            document_dir, files = _list_document_files(folder)
            if document_dir is None:
                skipped.append(folder.name)
                continue
            name = document_dir.name
            top = name if name not in used else f"{name} ({folder.name})"
            used.add(top)
            for path, arc_name in _list_version_files(folder, document_dir, version or VERSION_LATEST):
                archive.write(path, f"{top}/{arc_name}", compress_type=zipfile.ZIP_DEFLATED)
            for path in files[1:]:
                compression = zipfile.ZIP_STORED if path.suffix.lower() in _IMAGE_SUFFIXES else zipfile.ZIP_DEFLATED
                archive.write(path, f"{top}/{path.relative_to(document_dir).as_posix()}", compress_type=compression)

    return buffer.getvalue(), skipped
