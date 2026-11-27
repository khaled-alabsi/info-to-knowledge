"""The folder a reader opens: the document, its frames and its provenance, moved as one piece. REQUIREMENTS.md R10.

    <event>-<speakers>-<date>/                 named after the meeting (R13)
      <event>-<speakers>-<date>.md          the document, opened by its front matter, each image linked relatively,
                                            as resources/figure-01.png
      resources/            one frame per figure, numbered in document order
      provenance.json       what every paragraph, caption and image rests on

Written by the program at the end of phase 4, from the frame store, straight into the named folder: there is no
unnamed copy. A figure's file is named by its number, never by its frame, because the document's banned-phrase check
reads the links too. The provenance copies phase 4's, with every web address's
secrets hidden as the document hides them, and adds each image: its frame, its time, its box, its caption's entries,
the warnings its cut came with, and what was left out.
"""

from __future__ import annotations

import json
import re

import shutil
from pathlib import Path
from typing import Any

from models import output_contract
from models.front_matter_request import FrontMatterRequest
from models.run_paths import RunPaths
from services import document_file_service
from services import input_range_service
from services import run_tags_service
from utils import document_block_utils
from utils import document_name_utils
from utils import document_text_utils
from utils import json_store_utils

__all__ = ["set_aside_removed_figures", "restore_removed_figures", "build_published_name", "render_published_front_matter",
           "build_document_dir", "build_document_path", "build_figure_file_name", "write_document_folder"]


# Inside the review's removed-figures folder: which files were moved there and which were renamed after them
_RENAMES_FILE = "renames.json"
# A published figure's file is `figure-NN.png`: named by its number, never by its frame
_FIGURE_PREFIX = "figure-"
# What the provenance keeps of how a figure's cut was made
_CUT_PROVENANCE_KEYS = ("lookRect", "recordingRect", "warnings", "marks", "masks", "isFromRecording")
_FIGURE_GLOB = f"{_FIGURE_PREFIX}*.png"


def _build_removed_figures_dir(output_root: Path) -> Path:
    return output_root / output_contract.REVIEW_DIR_NAME / output_contract.REVIEW_REMOVED_FIGURES_DIR


def _read_figure_number(image: Path) -> int:
    """The number in a figure's file name: `figure-100.png` is 100, which a sort by name puts before 11."""

    return int(image.stem.removeprefix(_FIGURE_PREFIX))


def _hide_text_secrets(provenance_part: Any) -> Any:
    """`provenance_part` with every `text` field in it, at any depth, passed through the document's hiding of
    web-address secrets."""

    if isinstance(provenance_part, list):
        return [_hide_text_secrets(item) for item in provenance_part]
    if isinstance(provenance_part, dict):
        return {key: (document_text_utils.hide_secret_query_values(item) if key == "text" and isinstance(item, str)
                      else _hide_text_secrets(item)) for key, item in provenance_part.items()}

    return provenance_part


def _copy_figures(output_root: Path,
                  resources: Path,
                  images: list[dict[str, Any]],
                  numbers: dict[str, int]) -> dict[str, str]:
    """Each placed image's cut copied to `resources/figure-NN.png`, NN its figure number (`numbers`, image id -> number),
    and every other figure file a previous write left there removed, file by file. Image id -> its relative link."""

    resources.mkdir(parents=True, exist_ok=True)
    links = {}
    for image in images:
        name = build_figure_file_name(numbers[image["id"]])
        shutil.copyfile(output_root / image["cutFile"], resources / name)
        links[image["id"]] = f"{output_contract.RESOURCES_DIR_NAME}/{name}"
    kept = {Path(link).name for link in links.values()}
    for stale in resources.glob(_FIGURE_GLOB):
        if stale.name not in kept:
            stale.unlink()
    if not links and not any(resources.iterdir()):
        resources.rmdir()

    return links


def _read_video_name(paths: RunPaths) -> str:
    """The recording's file name, which holds the meeting's date."""

    return Path(paths.module).name


def _list_speakers(parts: dict[str, Any]) -> list[str]:
    return [str(speaker) for speaker in parts.get("speakers") or []]


def build_figure_file_name(number: int) -> str:
    """A published figure's file name in `resources/`: `figure-01.png` for the first."""

    return f"{_FIGURE_PREFIX}{number:02d}.png"


def build_published_name(paths: RunPaths,
                         markdown: str) -> str:
    """R13: the name the finished folder is published under, `[event]-[speakers]-[date]`: the agenda names the
    event and the speakers, the recording's file name holds the date, and the title of `markdown` (phase 4's document)
    stands in for an event nobody named. Phase 4 publishes under it; phase 5 (R16) finds the document by it."""

    video_name = _read_video_name(paths)
    parts = document_file_service.read_parts(paths)
    title = next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), "") or video_name
    speakers = _list_speakers(parts)
    meeting_date = document_name_utils.find_date_in_name(video_name)

    return document_name_utils.build_document_name(str(parts.get("event_name") or ""), speakers, meeting_date,
                                                   fallback=title)


def render_published_front_matter(paths: RunPaths) -> str:
    """R13: the metadata block the published document opens with: topic, event, speakers, date and the run's tags, and
    the part of the recording processed when the run processed a part."""

    parts = document_file_service.read_parts(paths)
    speakers = _list_speakers(parts)
    input_range = input_range_service.read_range(folder=paths.output_root)
    part = input_range_service.describe_range(input_range=input_range) if input_range is not None else ""

    request = FrontMatterRequest(topic=str(parts.get("topic") or ""),
                                 event=str(parts.get("event_name") or ""),
                                 speakers=speakers,
                                 meeting_date=document_name_utils.find_date_in_name(_read_video_name(paths)),
                                 tags=run_tags_service.read_tags(paths.output_root),
                                 part=part)

    return document_name_utils.render_front_matter(request=request)


def build_document_dir(paths: RunPaths) -> Path:
    """R13: the published folder, `<name>/` in the run folder, named from phase 4's document (`paths.summary`) and the
    recording's file name by `build_published_name`. Phase 4 publishes into it and phase 5 edits it."""

    name = build_published_name(paths, paths.summary.read_text(encoding="utf-8"))

    return paths.output_root / name


def build_document_path(paths: RunPaths) -> Path:
    folder = build_document_dir(paths)

    return folder / f"{folder.name}.md"


def _describe_image_provenance(image: dict[str, Any],
                               cut: dict[str, Any],
                               figure: int,
                               file: str,
                               entries_by_id: dict[str, dict[str, Any]],
                               frame_times: dict[str, float]) -> dict[str, Any]:
    """One placed figure in the provenance: its number, file, frame and time, the entries it rests on, and how its cut
    was made."""

    return {"figure": figure, "file": file, "frame": image["frame"], "time": frame_times.get(image["frame"]),
            "caption": image["caption"], "alt": image["alt"],
            "entries": [entries_by_id.get(entry_id, {"id": entry_id}) for entry_id in image["entryIds"]],
            **{key: cut[key] for key in _CUT_PROVENANCE_KEYS},
            # A cut no mask was drawn on has no rectangles: phase 5's audit reads them to tell a masked figure from a
            # dark one (DEFECTS 400)
            "maskedAreas": cut.get("maskedAreas") or []}


def write_document_folder(paths: RunPaths,
                          store: dict[str, Any],
                          document_provenance: dict[str, Any],
                          entries_by_id: dict[str, dict[str, Any]],
                          frame_times: dict[str, float]) -> Path:
    """Write the folder from the frame store, straight under the meeting's name (R13). Returns the document's path."""

    # STEP 1: the figures, in document order, each from the cut it was placed with
    document = build_document_path(paths)
    folder = document.parent
    folder.mkdir(parents=True, exist_ok=True)
    images = [{**block, "cutFile": store["cuts"][block["cut"]]["file"]} for block in store["blocks"] if block["kind"] == "image"]
    numbers = document_block_utils.map_figure_numbers(store["blocks"])
    links = _copy_figures(paths.output_root, folder / output_contract.RESOURCES_DIR_NAME, images, numbers)

    # STEP 2: the document, opened by its front matter, every figure linked and numbered
    document.write_text(render_published_front_matter(paths)
                        + document_block_utils.render_blocks(store["blocks"], links), encoding="utf-8")

    # STEP 3: the provenance, secrets hidden
    provenance = {
        "schemaVersion": 1, "document": document.name,
        "images": [_describe_image_provenance(image, store["cuts"][image["cut"]], numbers[image["id"]], links[image["id"]],
                                              entries_by_id, frame_times) for image in images],
        "leftOut": store["leftOut"],
        "removedUnreferenced": store["removedUnreferenced"],
        "facts": document_provenance}
    json_store_utils.write_json(folder / output_contract.DOCUMENT_PROVENANCE_FILE, _hide_text_secrets(provenance))

    return document


def set_aside_removed_figures(output_root: Path,
                              folder: Path,
                              numbers: set[int]) -> list[str]:
    """The image files of the figures `numbers` names moved out of the published folder's resources into the review's
    folder, and the files after them renamed to the numbers the audit gave their links: left in place, a removed figure
    failed the folder's check as linked nowhere. Moved and renamed, never deleted, each step recorded so a
    reset to phase 4 undoes it; done once, a later publish of the same review finds the record and changes nothing.
    The names moved."""

    resources = folder / output_contract.RESOURCES_DIR_NAME
    aside = _build_removed_figures_dir(output_root)
    record = aside / _RENAMES_FILE
    # STEP 1: done once; a later publish of the same review finds the record and changes nothing
    if record.is_file() or not numbers:
        return []

    # STEP 2: the removed figures' files moved aside
    aside.mkdir(parents=True, exist_ok=True)
    moved = []
    for number in sorted(numbers):
        image = resources / build_figure_file_name(number)
        if image.is_file():
            shutil.move(str(image), str(aside / image.name))
            moved.append(image.name)

    # STEP 3: the files after them renamed to the numbers the audit gave their links
    renames = []
    remaining = sorted(resources.glob(_FIGURE_GLOB), key=_read_figure_number) if resources.is_dir() else []
    for new_number, image in enumerate(remaining, start=1):
        renamed_to = build_figure_file_name(new_number)
        if image.name != renamed_to:
            image.rename(resources / renamed_to)
            # [renamed_to, renamed_from], as `restore_removed_figures` reads it
            renames.append([renamed_to, image.name])

    # STEP 4: each step recorded, so a reset to phase 4 undoes it
    json_store_utils.write_json(record, {"moved": moved, "renames": renames})

    return moved


def restore_removed_figures(output_root: Path,
                            folder: Path) -> list[str]:
    """What `set_aside_removed_figures` did, undone: the files renamed back, last first, then the moved ones put back in
    the published folder's resources. The names put back."""

    aside = _build_removed_figures_dir(output_root)
    resources = folder / output_contract.RESOURCES_DIR_NAME
    record = aside / _RENAMES_FILE
    if not record.is_file():
        return []
    done = json_store_utils.read_json(record)
    for renamed_to, renamed_from in reversed(done.get("renames") or []):
        if (resources / renamed_to).is_file():
            (resources / renamed_to).rename(resources / renamed_from)
    restored = []
    for name in done.get("moved") or []:
        if (aside / name).is_file():
            shutil.move(str(aside / name), str(resources / name))
            restored.append(name)
    record.unlink()

    return restored


def find_figure_frame(paths: RunPaths,
                       quote: str) -> str:
    """The frame a figure the quote places was cut from, by the published document's provenance (`images`, each with
    its file and frame), or "" when the quote places none or the provenance cannot be read (DEFECTS 246)."""

    match = re.search(r"\]\(([^)\s]+\.png)", quote)
    provenance = build_document_dir(paths) / "provenance.json"
    if match is None or not provenance.is_file():
        return ""
    try:
        images = json.loads(provenance.read_text(encoding="utf-8")).get("images") or []
    except (OSError, json.JSONDecodeError):
        return ""

    return next((str(image.get("frame") or "") for image in images if image.get("file") == match.group(1)), "")
