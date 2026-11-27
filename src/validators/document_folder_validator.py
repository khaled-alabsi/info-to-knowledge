"""Validate the folder phase 4 publishes, and phase 5 edits (R16). A pure function over bytes on
disk. REQUIREMENTS.md R10.

The program checks the result and never makes the choice: every image the document links is in the folder and
every frame in the folder is linked, no file name names the recording, the text holds no clock time, no banned phrase
and no sentence said twice (a caption may restate the claim of the text beside it), and every caption cites entries
that exist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils import document_heading_utils
from models import output_contract
from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from models import pipeline_errors
from services import document_folder_service
from services import integration_service
from utils import document_edit_utils
from utils import document_text_utils
from utils import mermaid_text_utils

__all__ = ["validate_document_folder", "validate_document_file", "find_repeated_document_sentences", "build_repeat_text"]

_LINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LINK_TARGET = re.compile(r"\]\([^)]+\)")


def _remove_figure_lines(text: str) -> str:
    """`text` without its figures: every caption line and every image line."""

    return "\n".join(line for line in text.splitlines()
                     if not (line.startswith(document_text_utils.FIGURE_CAPTION_START) or _LINK.search(line)))


def _strip_links(text: str) -> str:
    """`text` with each image link's target dropped: a file name is checked on its own, the alt text with the prose."""

    return _LINK_TARGET.sub("]", text)


def list_note_times(paths: RunPaths) -> frozenset[str]:
    """The clock times the run's notes hold in their own text: a process table's CPU time, a schedule's hours. The writer
    is never shown a position in the recording, so a time a note holds is that note's content (DEFECTS 379: "0:09.96"
    copied from a process table was refused as a recording position, and the writer replaced the column with "--")."""

    try:
        entries = integration_service.build_integration(paths).get("entries", [])
    except (OSError, ValueError, KeyError, pipeline_errors.MissingPhaseInput):
        return frozenset()

    return frozenset(time for entry in entries for time in document_text_utils.find_timestamps(str(entry.get("text") or "")))


_NOTE_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")


def list_note_words(paths: RunPaths) -> frozenset[str]:
    """The identifier-like words (six characters or more) the run's notes spell, for the check that a code block holds
    none the notes do not (release test 2's chapter 5 printed "pgench_accounts_3" for the screen's "pgbench_accounts_3",
    DEFECTS 440)."""

    try:
        entries = integration_service.build_integration(paths).get("entries", [])
    except (OSError, ValueError, KeyError, pipeline_errors.MissingPhaseInput):
        return frozenset()

    return frozenset(word for entry in entries for word in _NOTE_WORD.findall(str(entry.get("text") or "")))


def validate_document_file(folder: Path,
                           document_name: str,
                           known_entry_ids: list[str],
                           note_times: frozenset[str] = frozenset()) -> ValidationReport:
    """The document `document_name` in `folder`, with its frames and its provenance beside it. A front matter the
    published document opens with (R13) is metadata, and is not read as its text."""

    report = ValidationReport(subject=folder.name)
    document = folder / document_name

    # --- existence: always first, and always an early return ---------------------------------
    report.mark_ran("DOCUMENT_MISSING")
    if not document.is_file() or not document.stat().st_size:
        report.add("DOCUMENT_MISSING", f"{folder.name}/{document_name} was not written",
                   fix="this is a playbook bug: the program assembles this file")

        return report
    _front_matter, text = document_edit_utils.split_front_matter(document.read_text(encoding="utf-8"))
    links = _LINK.findall(text)
    resources = folder / output_contract.RESOURCES_DIR_NAME

    report.mark_ran("DOCUMENT_IMAGE_MISSING")
    missing = [link for link in links if not (folder / link).is_file()]
    if missing:
        report.add("DOCUMENT_IMAGE_MISSING", f"linked and not in the folder: {', '.join(missing)}",
                   fix="this is a playbook bug: every placed image is copied into resources/")

    report.mark_ran("DOCUMENT_IMAGE_UNLINKED")
    files = sorted(path.name for path in resources.iterdir() if not path.name.startswith(".")) if resources.is_dir() else []
    unlinked = [name for name in files if f"{output_contract.RESOURCES_DIR_NAME}/{name}" not in links]
    if unlinked:
        report.add("DOCUMENT_IMAGE_UNLINKED", f"in resources/ and linked nowhere: {', '.join(unlinked)}",
                   fix="this is a playbook bug: a removed image's file is removed with it")

    report.mark_ran("DOCUMENT_IMAGE_NAME")
    named = [name for name in files if document_text_utils.find_banned_phrases(name.replace("-", " "))]
    if named:
        report.add("DOCUMENT_IMAGE_NAME", f"file name(s) that name the recording: {', '.join(named)}",
                   fix="this is a playbook bug: a figure's file is named by its number")

    prose = mermaid_text_utils.remove_blocks(text)
    timestamps = [time for time in document_text_utils.find_timestamps(prose) if time not in note_times]
    for code, found in (("DOCUMENT_TIMESTAMP", timestamps),
                        ("DOCUMENT_BANNED_PHRASE", document_text_utils.find_banned_phrases(_strip_links(prose))),
                        ("DOCUMENT_ENTRY_ID_IN_TEXT", document_text_utils.find_entry_ids(prose))):
        report.mark_ran(code)
        if found:
            report.add(code, f"the document holds {', '.join(map(repr, found))}",
                       fix="a caption, an alt text or a pointer sentence broke what the prose may not hold")
    # A note, never a refusal: the chapter checks refuse a repeat while an answer can still fix it, and here no model
    # call is left, so a refusal failed the whole recording on its last step. A chapter repeated a screen's list as its
    # own and the run failed after every chapter had passed (DOCUMENT_REPEATED_SENTENCE, a hard fix)
    report.mark_ran("DOCUMENT_REPEATED_SENTENCE")
    repeated = find_repeated_document_sentences(text)
    if repeated:
        report.note("DOCUMENT_REPEATED_SENTENCE", f"{len(repeated)} sentence(s) said more than once: "
                    + "; ".join(repr(sentence) for sentence in repeated), fix="say each thing once")

    # --- every caption rests on entries that exist ------------------------------------------------
    report.mark_ran("DOCUMENT_CAPTION_UNSOURCED")
    report.mark_ran("DOCUMENT_PROVENANCE_MISSING")
    provenance_file = folder / output_contract.DOCUMENT_PROVENANCE_FILE
    images = json.loads(provenance_file.read_text(encoding="utf-8")).get("images", []) if provenance_file.is_file() else None
    if images is None:
        report.add("DOCUMENT_PROVENANCE_MISSING", f"{output_contract.DOCUMENT_PROVENANCE_FILE} was not written",
                   fix="this is a playbook bug: the program writes it with the document")
    else:
        known = set(known_entry_ids)
        # A figure whose caption names no entry rests on the frame it was cut from: a run's last chapter blocked at
        # publishing on one such figure, its frame known (DEFECTS 700). An entry cited that no note holds is refused
        unsourced = [image.get("figure") for image in images
                     if (not image.get("entries") and not image.get("frame"))
                     or any(entry.get("id") not in known for entry in image.get("entries") or [])]
        if unsourced:
            report.add("DOCUMENT_CAPTION_UNSOURCED", f"figure(s) whose caption cites no known entry: {unsourced}",
                       fix="this is a playbook bug: a caption's entries are checked when the image is placed")

    return report


def validate_document_folder(paths: RunPaths,
                             known_entry_ids: list[str]) -> ValidationReport:
    """The folder phase 4 publishes, named after the meeting (R13)."""

    document = document_folder_service.build_document_path(paths)

    return validate_document_file(document.parent, document.name, known_entry_ids, list_note_times(paths))


def find_repeated_document_sentences(markdown: str) -> list[str]:
    """Every sentence the document says more than once, normalised, outside what may say a claim again: the summary,
    the agenda, the closing, the captions and figure descriptions, and the diagrams."""

    # Each list and table one sentence, as the section checks read a screen's: two lists of one web page share its side
    # panel's lines (lecture-9 chapter 9, 2026-10-21); the writer's own lists are compared line by line there
    return document_text_utils.find_repeated_sentences(build_repeat_text(markdown), is_list_one_sentence=True)


def build_repeat_text(markdown: str) -> str:
    """The document's text a sentence said twice is looked for in: no diagram, no section a claim may be said again in,
    no figure description and no caption. A figure's description quotes its screen exactly (write.md) (2026-10-08)."""

    prose = mermaid_text_utils.remove_blocks(markdown)

    return _remove_figure_lines(document_text_utils.remove_figure_descriptions(
        document_text_utils.remove_sections(prose, document_heading_utils.list_repeat_exempt_headings())))
