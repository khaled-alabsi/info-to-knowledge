"""Phase 4's files under `04_writing/`: the agenda, the draft headings, each chapter's submission and requests, the
sections, the executive summary draft and the final summary, the follow-up records, the agent's notes and the frame
store; their paths, their reads and their writes. The document is assembled from them by `document_assembly_service`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_client import path_sandbox
from models import output_contract
from models.run_paths import RunPaths
from services import phase_file_service
from utils import json_store_utils
from utils import section_markdown_utils

__all__ = [
    # paths and slugs
    "build_outline_slug", "build_outline_path", "build_contents_path", "build_parts_path", "build_summary_path", "build_section_path",
    "build_sections_dir", "build_executive_summary_draft_path", "build_image_store_path", "build_chapter_submission_path",
    "build_follow_up_request_path", "is_agenda_known", "build_draft_headings_path", "build_working_draft_path", "build_follow_up_records_path",
    "build_draft_headings_slug", "build_chapter_text_slug", "build_chapter_text_part_slug",
    "read_chapter_text_part_number", "build_section_slug",
    # writes
    "write_outline", "write_contents", "write_section", "write_parts", "write_summary", "write_image_store",
    "write_chapter_submission", "write_follow_up_request", "write_draft_headings", "write_executive_summary_draft",
    "write_agent_notes", "write_meeting_notes", "write_follow_up_record", "remove_follow_up_records", "clear_document_parts",
    # reads
    "read_outline", "read_contents", "read_section", "read_parts", "read_summary", "read_image_store", "list_cuts_in_use",
    "read_chapter_submission", "read_draft_headings", "read_executive_summary_draft", "read_agent_notes", "read_follow_up_records",
    "list_follow_up_steps", "find_quote_sources",
    # names
    "CHAPTER_NAME_PATTERN", "DRAFT_HEADINGS_DIR_NAME", "CHAPTERS_DIR_NAME", "REQUESTS_DIR_NAME",
    "SECTIONS_DIR_NAME", "OUTLINES_DIR_NAME", "SECTION_SLUG_PATTERN", "EXECUTIVE_SUMMARY_DRAFT_SLUG", "PARTS_SLUG",
    "CONTENTS_SLUG", "SUMMARY_SLUG", "TABLE_OF_CONTENTS_SLUG", "FRAME_DECISION_KEYS"]

_CONTENTS_FILE = "contents.json"
_PARTS_FILE = "parts.json"
_SUMMARY_FILE = "summary.json"
# REQUIREMENTS.md R12: the conversation's first submission, context for the agent and never published.
_EXECUTIVE_SUMMARY_DRAFT_FILE = "executive-summary-draft.json"
# REQUIREMENTS.md R12: the decisions the conversation's agent recorded for later steps, kept across compactions and resumes.
_AGENT_NOTES_FILE = "notes.json"
# REQUIREMENTS.md R12: the document as the conversation writes it, pending markers and all; `04_document.md` is
# written only at the end.
_WORKING_DRAFT_FILE = "draft.md"
# R12: each follow-up's last outcome (status, attempts, calls, reason), so a resume re-asks only what can change.
_FOLLOW_UPS_FILE = "follow-ups.json"
# How many parts each chapter asked in parts is asked in, written before its first part, so the steps list shows every
# part from the start: `{"<chapter>": <parts>}`
_CHAPTER_PARTS_FILE = "chapter-parts.json"
# A chapter's own follow-ups and files: its draft headings (`chapter-11`), its text (`chapter-11-text`) or a part of it
# (`chapter-11-text-2`)
CHAPTER_NAME_PATTERN = re.compile(r"^chapter-(\d+)(-text(?:-\d+)?)?(?:\.|$)")
# The folders of `04_writing/` a chapter's work is filed in, and a section's slug, `heading-007-2`
DRAFT_HEADINGS_DIR_NAME = "draft-headings"
CHAPTERS_DIR_NAME = "chapters"
REQUESTS_DIR_NAME = "requests"
SECTIONS_DIR_NAME = "sections"
OUTLINES_DIR_NAME = "outlines"
# The looks and cuts phase 4's chapters made
_IMAGE_STORE_FILE = "images.json"
SECTION_SLUG_PATTERN = re.compile(r"^heading-(\d+)-\d+$")
# The follow-ups of phase 4 before the chapters (the executive summary draft, the agenda), and the one after them
EXECUTIVE_SUMMARY_DRAFT_SLUG = "executive-summary-draft"
PARTS_SLUG = "parts"
CONTENTS_SLUG = "contents"
# The outline that holds the table of contents the program builds from the draft headings
TABLE_OF_CONTENTS_SLUG = "table-of-contents"
SUMMARY_SLUG = "summary"
# The image store's lists of decisions about a needed frame, each record naming the follow-up that made it
FRAME_DECISION_KEYS = ("leftOut", "diagramReplacements")


def _build_writing_path(paths: RunPaths,
                        name: str) -> Path:
    """`04_writing/<name>` in the run folder."""

    return paths.output_root / output_contract.WRITING_DIR_NAME / name


def _resolve_slug_path(paths: RunPaths,
                       folder: str,
                       slug: str,
                       suffix: str) -> Path:
    """`04_writing/<folder>/<slug><suffix>`, resolved inside the run folder: a slug is part of a file name, and one
    that climbs out of the folder is refused (`path_sandbox`)."""

    return path_sandbox.resolve_within(paths.output_root, f"{output_contract.WRITING_DIR_NAME}/{folder}/{slug}{suffix}")


def build_outline_slug(number: int) -> str:
    """The slug of the outline of the chunk of the document numbered `number`."""

    return f"outline-{number:02d}"


def build_outline_path(paths: RunPaths,
                       slug: str) -> Path:
    return _resolve_slug_path(paths, OUTLINES_DIR_NAME, slug, ".json")


def build_contents_path(paths: RunPaths) -> Path:
    return _build_writing_path(paths, _CONTENTS_FILE)


def build_parts_path(paths: RunPaths) -> Path:
    return _build_writing_path(paths, _PARTS_FILE)


def build_summary_path(paths: RunPaths) -> Path:
    return _build_writing_path(paths, _SUMMARY_FILE)


def build_executive_summary_draft_path(paths: RunPaths) -> Path:
    return _build_writing_path(paths, _EXECUTIVE_SUMMARY_DRAFT_FILE)


def build_image_store_path(paths: RunPaths) -> Path:
    """The looks and cuts phase 4's chapters made."""

    return _build_writing_path(paths, _IMAGE_STORE_FILE)


def list_cuts_in_use(store: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The store's cuts still in use: a cut undone, or set aside by a chapter reset, cannot be placed."""

    return {cut_id: cut for cut_id, cut in (store.get("cuts") or {}).items() if not cut.get("discarded")}


def write_image_store(paths: RunPaths,
                      store: dict[str, Any]) -> Path:
    path = build_image_store_path(paths)
    json_store_utils.write_json(path, store)

    return path


def read_image_store(paths: RunPaths) -> dict[str, Any]:
    """The frame store phase 4 left; empty when no chapter made a frame."""

    path = build_image_store_path(paths)

    return json_store_utils.read_json(path) if path.is_file() else {}


def build_draft_headings_slug(point_number: int) -> str:
    """The slug of a chapter's draft headings, `chapter-03`: its file, its request and its record are named by it."""

    return f"chapter-{point_number:02d}"


def build_chapter_text_slug(point_number: int) -> str:
    """The slug of a chapter's text, `chapter-03-text`; a chapter asked in parts adds `-1`, `-2`."""

    return f"{build_draft_headings_slug(point_number)}-text"


def build_chapter_text_part_slug(point_number: int,
                                 part_number: int) -> str:
    """The slug of one part of a chapter asked in parts, `chapter-03-text-2`."""

    return f"{build_chapter_text_slug(point_number)}-{part_number}"


def read_chapter_text_part_number(slug: str) -> int:
    """The part a chapter text slug names: 2 for `chapter-03-text-2`, 0 for the whole chapter's `chapter-03-text`."""

    match = CHAPTER_NAME_PATTERN.match(slug)
    suffix = match.group(2) if match else None

    return int(suffix.rsplit("-", 1)[1]) if suffix and suffix != "-text" else 0


def build_section_slug(heading_number: int,
                       part_number: int) -> str:
    """The slug of one section part, `heading-007-2`: the heading's number over the whole document, then its part."""

    return f"heading-{heading_number:03d}-{part_number}"


def build_chapter_submission_path(paths: RunPaths,
                                  slug: str) -> Path:
    return _resolve_slug_path(paths, CHAPTERS_DIR_NAME, slug, ".json")


def write_chapter_submission(paths: RunPaths,
                             slug: str,
                             submission: dict[str, Any]) -> Path:
    path = build_chapter_submission_path(paths, slug)
    json_store_utils.write_json(path, submission)

    return path


def read_chapter_submission(paths: RunPaths,
                            slug: str) -> dict[str, Any]:
    return json_store_utils.read_json(build_chapter_submission_path(paths, slug))


def build_follow_up_request_path(paths: RunPaths,
                                 slug: str) -> Path:
    return _resolve_slug_path(paths, REQUESTS_DIR_NAME, slug, ".md")


def write_follow_up_request(paths: RunPaths,
                            slug: str,
                            text: str) -> Path:
    """The message a follow-up of phase 4's conversation was sent, as the agent read it (what
    the agent was given must be readable afterwards). Rewritten each time the follow-up is asked again."""

    path = build_follow_up_request_path(paths, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    return path


def build_draft_headings_path(paths: RunPaths,
                              slug: str) -> Path:
    return _resolve_slug_path(paths, DRAFT_HEADINGS_DIR_NAME, slug, ".json")


def write_draft_headings(paths: RunPaths,
                         slug: str,
                         draft_headings: dict[str, Any]) -> Path:
    path = build_draft_headings_path(paths, slug)
    json_store_utils.write_json(path, draft_headings)

    return path


def read_draft_headings(paths: RunPaths,
                        slug: str) -> dict[str, Any]:
    return json_store_utils.read_json(build_draft_headings_path(paths, slug))


def build_sections_dir(paths: RunPaths) -> Path:
    """`04_writing/sections/`, where every section's markdown is the only copy."""

    return _build_writing_path(paths, SECTIONS_DIR_NAME)


def build_section_path(paths: RunPaths,
                       slug: str) -> Path:
    return _resolve_slug_path(paths, SECTIONS_DIR_NAME, slug, ".md")


def write_meeting_notes(paths: RunPaths,
                        text: str) -> Path:
    """Layer 2 as the agents read it, `03_notes.md`, beside the numbered files."""

    path = paths.output_root / output_contract.NOTES_FILE
    path.write_text(text, encoding="utf-8")

    return path


def write_outline(paths: RunPaths,
                  slug: str,
                  outline: dict[str, Any]) -> Path:
    path = build_outline_path(paths, slug)
    json_store_utils.write_json(path, outline)

    return path


def write_contents(paths: RunPaths,
                   contents: dict[str, Any]) -> Path:
    path = build_contents_path(paths)
    json_store_utils.write_json(path, contents)

    return path


def write_section(paths: RunPaths,
                  slug: str,
                  markdown: str) -> Path:
    """The section as its writer laid it out, note ids included."""

    path = build_section_path(paths, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")

    return path


def write_parts(paths: RunPaths,
                parts: dict[str, Any]) -> Path:
    """The agenda, a PDF's with no event: a document is named by its title, and NIST's agenda naming "NIST CSF 2.0
    Presentation" called the published document a presentation, in its front matter and its folder's name."""

    if parts.get("event_name") and phase_file_service.is_pdf_run(paths):
        parts = {**parts, "event_name": ""}
    path = build_parts_path(paths)
    json_store_utils.write_json(path, parts)

    return path


def write_summary(paths: RunPaths,
                  summary: dict[str, Any]) -> Path:
    path = build_summary_path(paths)
    json_store_utils.write_json(path, summary)

    return path


def write_executive_summary_draft(paths: RunPaths,
                                  draft: dict[str, Any]) -> Path:
    path = build_executive_summary_draft_path(paths)
    json_store_utils.write_json(path, draft)

    return path


def read_executive_summary_draft(paths: RunPaths) -> dict[str, Any]:
    return json_store_utils.read_json(build_executive_summary_draft_path(paths))


def build_working_draft_path(paths: RunPaths) -> Path:
    return _build_writing_path(paths, _WORKING_DRAFT_FILE)


def build_follow_up_records_path(paths: RunPaths) -> Path:
    """`04_writing/follow-ups.json`: each follow-up's status, attempts and calls, read by the resume and the page."""

    return _build_writing_path(paths, _FOLLOW_UPS_FILE)


def write_follow_up_record(paths: RunPaths,
                           slug: str,
                           record: dict[str, Any]) -> None:
    path = build_follow_up_records_path(paths)
    json_store_utils.write_json(path, {**read_follow_up_records(paths), slug: record})


def remove_follow_up_records(paths: RunPaths,
                             slugs: list[str]) -> None:
    """Forget these follow-ups, so a resume asks them again (`chapter_reset_service`)."""

    path = build_follow_up_records_path(paths)
    if path.is_file():
        json_store_utils.write_json(path, {slug: record for slug, record in read_follow_up_records(paths).items()
                                           if slug not in slugs})


def read_follow_up_records(paths: RunPaths) -> dict[str, dict[str, Any]]:
    path = build_follow_up_records_path(paths)

    return dict(json_store_utils.read_json(path)) if path.is_file() else {}


def is_agenda_known(paths: RunPaths) -> bool:
    """Whether the agenda is accepted: until it is, the number of chapters is not known."""

    return build_parts_path(paths).is_file()


def write_chapter_part_count(paths: RunPaths,
                             chapter_number: int,
                             part_count: int) -> Path:
    path = _build_writing_path(paths, _CHAPTER_PARTS_FILE)
    counts = json_store_utils.read_json(path) if path.is_file() else {}
    json_store_utils.write_json(path, {**counts, str(chapter_number): part_count})

    return path


def read_chapter_part_counts(paths: RunPaths) -> dict[int, int]:
    path = _build_writing_path(paths, _CHAPTER_PARTS_FILE)

    return {int(chapter): int(count) for chapter, count in json_store_utils.read_json(path).items()} if path.is_file() else {}


def list_follow_up_steps(paths: RunPaths) -> list[tuple[str, str]]:
    """Phase 4's follow-ups in the order they are asked, each with its status in `follow-ups.json` (`accepted`,
    `blocked`) or `waiting` when not asked yet: the executive summary draft, the agenda, each chapter's headings, each
    chapter's text (one step per part once the chapter is asked in parts), the final summary. The chapters are the
    agenda's points, so before the agenda is accepted the count is not known (`is_agenda_known`)."""

    records = read_follow_up_records(paths)
    chapter_count = len(read_parts(paths).get("points") or [])
    recorded_chapters = [int(match.group(1)) for slug in records if (match := CHAPTER_NAME_PATTERN.match(slug))]
    chapter_count = max([chapter_count, *recorded_chapters])
    # A part being asked has its request and no record yet, and the parts after it neither: the parts are also read
    # from the requests and from the chapter's part count, or the list showed the next chapter as the one worked on
    requests_dir = _build_writing_path(paths, REQUESTS_DIR_NAME)
    requested = [path.stem for path in requests_dir.glob("chapter-*-text*.md")] if requests_dir.is_dir() else []
    part_counts = read_chapter_part_counts(paths)
    texts = []
    for number in range(1, chapter_count + 1):
        base = build_chapter_text_slug(number)
        planned = ([build_chapter_text_part_slug(number, part) for part in range(1, part_counts[number] + 1)]
                   if part_counts.get(number, 1) > 1 else [])
        known = {slug for slug in [*records, *requested] if slug == base or slug.startswith(f"{base}-")}
        # a chapter asked in parts no longer holds its whole-chapter step
        known |= set(planned)
        if any(read_chapter_text_part_number(slug) for slug in known):
            known.discard(base)
        # In part order: by name, `-10` would come before `-2`
        texts += sorted(known, key=read_chapter_text_part_number) or [base]
    slugs = [EXECUTIVE_SUMMARY_DRAFT_SLUG, PARTS_SLUG, *[build_draft_headings_slug(number) for number in range(1, chapter_count + 1)],
             *texts, SUMMARY_SLUG]

    return [(slug, records.get(slug, {}).get("status", output_contract.FOLLOW_UP_WAITING)) for slug in slugs]


def _build_agent_notes_path(paths: RunPaths) -> Path:
    return _build_writing_path(paths, _AGENT_NOTES_FILE)


def write_agent_notes(paths: RunPaths,
                      notes: list[str]) -> Path:
    path = _build_agent_notes_path(paths)
    json_store_utils.write_json(path, {"notes": list(notes)})

    return path


def read_agent_notes(paths: RunPaths) -> list[str]:
    path = _build_agent_notes_path(paths)

    return list(json_store_utils.read_json(path).get("notes", [])) if path.is_file() else []


def read_parts(paths: RunPaths) -> dict[str, Any]:
    """The agenda as submitted; empty when it was not written (the agenda blocked, or not asked yet)."""

    path = build_parts_path(paths)

    return json_store_utils.read_json(path) if path.is_file() else {}


def read_summary(paths: RunPaths) -> dict[str, Any]:
    return json_store_utils.read_json(build_summary_path(paths))


def read_outline(paths: RunPaths,
                 slug: str) -> dict[str, Any]:
    return json_store_utils.read_json(build_outline_path(paths, slug))


def read_contents(paths: RunPaths) -> dict[str, Any]:
    return json_store_utils.read_json(build_contents_path(paths))


def find_quote_sources(paths: RunPaths,
                       quote: str) -> list[str]:
    """The note ids that close the block of a written section holding `quote`, or none when no block holds it. The
    published document drops the ids, so a reviewer reading it guessed a frame per todo, one number up each time, and
    recorded those frames as its sources."""

    # Its first 80 characters: a reviewer quotes a sentence's start, and a long one is cut short in the todo
    wanted = " ".join(quote.split()).lower()[:80]
    if not wanted:
        return []
    for path in sorted(build_sections_dir(paths).glob("*.md")):
        # The blocks the program reads: a line of ids alone belongs to the block above it
        for block in section_markdown_utils.build_section_record(path.read_text(encoding="utf-8"))["blocks"]:
            if wanted in " ".join(str(block["text"]).split()).lower():
                return list(dict.fromkeys(block["entry_ids"]))

    return []


def find_other_side_sources(paths: RunPaths,
                            quote: str,
                            missing: str) -> list[str]:
    """For a contradiction, the note ids closing the written block that holds a term of `missing` the quoted sentence
    lacks (a model number, a value): the request named only the quoted sentence's notes, and a reviewer read four
    frames before it found the other side's (DEFECTS 245)."""

    said = " ".join(quote.split()).lower()
    terms = [term for term in re.findall(r"[\w./+-]*\d[\w./+-]*|[A-Z][\w./+-]{3,}", missing) if term.lower() not in said]
    if not terms:
        return []
    for path in sorted(build_sections_dir(paths).glob("*.md")):
        for block in section_markdown_utils.build_section_record(path.read_text(encoding="utf-8"))["blocks"]:
            text = " ".join(str(block["text"]).split()).lower()
            if said[:80] not in text and any(term.lower() in text for term in terms):
                return list(dict.fromkeys(block["entry_ids"]))

    return []


def _attach_cut(image: dict[str, Any],
                cuts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The image with the file and the frame of the cut it names, from the frame store's cuts."""

    cut = cuts.get(image["cut"], {})

    return {**image, "cutFile": str(cut.get("file") or ""), "frame": cut.get("frame", "")}


def read_section(paths: RunPaths,
                 slug: str) -> dict[str, Any]:
    """The section's record (`section_markdown_utils.build_section_record`), each frame with the file and the frame
    of the cut it names, from the frame store."""

    record = section_markdown_utils.build_section_record(build_section_path(paths, slug).read_text(encoding="utf-8"))
    cuts = read_image_store(paths).get("cuts") or {}

    return {**record, "images": [_attach_cut(image, cuts) for image in record["images"]]}


def clear_document_parts(paths: RunPaths) -> None:
    """Remove the parts, the outlines, the title, the sections and the summary a previous run left, file by file,
    before a new run writes its own. Nothing is removed recursively, and nothing outside the known names."""

    for part_file in (build_contents_path(paths), build_parts_path(paths), build_summary_path(paths)):
        part_file.unlink(missing_ok=True)
    for folder, pattern in ((OUTLINES_DIR_NAME, "*.json"), (SECTIONS_DIR_NAME, "*.md")):
        parts_dir = _build_writing_path(paths, folder)
        if parts_dir.is_dir():
            for part_file in parts_dir.glob(pattern):
                part_file.unlink()
