"""`i2k reset-chapter N`: phase 4 set back to just before chapter N, as if chapter N and every later one had never been
written. Chapters 1 to N-1 stay accepted, and the agenda and the executive summary draft with them.

Removed: each later chapter's draft headings, its text answers, its requests and its call logs; the sections of their
headings; the final parts (the title and the summary), which rest on every chapter; and their records in
`follow-ups.json`, so a resume asks them again. A chapter whose notes changed after it was written (phase 3 finished
frames phase 4 had gone without) is then asked from its draft headings, with the notes as they are now.

Sections are numbered in heading order over the whole document, so the sections of chapter N and after are the ones
numbered past the headings chapters 1 to N-1 hold. The logs stay: they are the history of what ran, never the state a
resume reads.

With `is_text_only`, the draft headings of chapter N and after stay accepted (their files, requests, call logs and
records), and only their texts, their sections and the final parts go: for a text written wrong from headings that
are right, so the headings' long whole-chapter reads are not asked again.

Either way, every cut no kept section places is set aside (`discarded: "reset"`, `output_contract.RESET_DISCARD_REASON`),
as `undo_cut` sets one aside: a chapter written again is offered the cuts already made from its frames, and a wrong cut
left from its earlier attempt was offered and placed again.
"""

from __future__ import annotations

from pathlib import Path

from models import output_contract
from models.chapter_reset_spec import ChapterResetSpec
from models.run_paths import RunPaths
from services import document_file_service
from services import figure_match_service
from utils import json_lines_utils
from utils import json_store_utils

__all__ = ["list_chapter_reset_targets", "reset_from_chapter"]

# The follow-ups that rest on every chapter
_FINAL_PARTS = ((document_file_service.CONTENTS_SLUG, document_file_service.build_contents_path),
                (document_file_service.SUMMARY_SLUG, document_file_service.build_summary_path))
_FINAL_SLUGS = tuple(slug for slug, _ in _FINAL_PARTS)
_CHAPTER_FOLDERS = (document_file_service.DRAFT_HEADINGS_DIR_NAME, document_file_service.CHAPTERS_DIR_NAME,
                    document_file_service.REQUESTS_DIR_NAME)


def _is_reset_target(name: str,
                     chapter: int,
                     is_text_only: bool) -> bool:
    """Whether a reset from `chapter` takes this name back. A name that is no chapter's never is, and with `is_text_only`
    only a chapter's text is."""

    match = document_file_service.CHAPTER_NAME_PATTERN.match(name)
    if match is None:
        return False
    is_text = match.group(2) is not None

    return int(match.group(1)) >= chapter and (is_text or not is_text_only)


def _count_earlier_headings(paths: RunPaths,
                            chapter: int) -> int:
    """The headings chapters 1 to `chapter - 1` hold, from their draft headings. A chapter with none on disk is refused
    by name: its sections could not be told from the later ones."""

    count = 0
    for number in range(1, chapter):
        slug = document_file_service.build_draft_headings_slug(number)
        path = document_file_service.build_draft_headings_path(paths, slug)
        if not path.is_file():
            raise ValueError(f"{slug} has no draft headings in {path.parent}: the sections of the chapters "
                             f"before {chapter} cannot be counted")
        count += len(json_store_utils.read_json(path).get("headings") or [])

    return count


def _is_later_section(path: Path,
                      earlier: int) -> bool:
    """Is this the markdown of a section past the `earlier` headings chapters 1 to N-1 hold?"""

    match = document_file_service.SECTION_SLUG_PATTERN.match(path.stem)

    return path.suffix == ".md" and match is not None and int(match.group(1)) > earlier


def _list_reset_files(paths: RunPaths,
                      chapter: int,
                      is_text_only: bool) -> list[Path]:
    """The files a reset from `chapter` removes."""

    root = paths.output_root
    writing = root / output_contract.WRITING_DIR_NAME
    earlier = _count_earlier_headings(paths, chapter)

    # STEP 1: each later chapter's draft headings, text answers and requests
    files = [path for folder in _CHAPTER_FOLDERS if (writing / folder).is_dir()
             for path in sorted((writing / folder).iterdir()) if _is_reset_target(path.name, chapter, is_text_only)]

    # STEP 2: the sections of their headings
    sections = document_file_service.build_sections_dir(paths)
    if sections.is_dir():
        files += [path for path in sorted(sections.iterdir()) if _is_later_section(path, earlier)]

    # STEP 3: the final parts, which rest on every chapter, and the call logs of all of them
    files += [path for _, build_path in _FINAL_PARTS if (path := build_path(paths)).is_file()]
    calls = root / output_contract.CALLS_DIR_NAME
    if calls.is_dir():
        files += [path for path in sorted(calls.glob("*.json"))
                  if _is_reset_target(path.name, chapter, is_text_only) or path.stem in _FINAL_SLUGS]

    return files


def _list_reset_records(paths: RunPaths,
                        chapter: int,
                        is_text_only: bool) -> list[str]:
    """The follow-ups whose records a reset from `chapter` removes, so a resume asks them again."""

    return [slug for slug in document_file_service.read_follow_up_records(paths)
            if _is_reset_target(slug, chapter, is_text_only) or slug in _FINAL_SLUGS]


def _list_unplaced_cuts(paths: RunPaths,
                        files_to_remove: list[Path]) -> list[str]:
    """The cuts in use that no section left after the reset places."""

    sections = document_file_service.build_sections_dir(paths)
    to_remove = set(files_to_remove)
    kept_section_slugs = ([path.stem for path in sorted(sections.glob("*.md")) if path not in to_remove]
                          if sections.is_dir() else [])
    placed = figure_match_service.list_placed_cut_ids(paths, kept_section_slugs)

    in_use = document_file_service.list_cuts_in_use(document_file_service.read_image_store(paths))

    return [cut_id for cut_id in sorted(in_use) if cut_id not in placed]


def _list_frame_decisions(paths: RunPaths,
                          chapter: int,
                          is_text_only: bool) -> list[tuple[str, dict]]:
    """The `leave_out` and `replace_with_diagram` records the follow-ups taken back made, with the list each is in: left
    behind, they would count a needed frame as accounted for in a chapter that never decided it."""

    store = document_file_service.read_image_store(paths)

    return [(key, record) for key in document_file_service.FRAME_DECISION_KEYS for record in store.get(key) or []
            if _is_reset_target(str(record.get("chapter") or ""), chapter, is_text_only)]


def _build_reset_spec(paths: RunPaths,
                      chapter: int,
                      is_text_only: bool) -> ChapterResetSpec:
    """What a reset from `chapter` removes, worked out once for both the listing and the reset."""

    files = _list_reset_files(paths, chapter, is_text_only)

    return ChapterResetSpec(files=files, follow_up_slugs=_list_reset_records(paths, chapter, is_text_only),
                            unplaced_cut_ids=_list_unplaced_cuts(paths, files),
                            frame_decisions=_list_frame_decisions(paths, chapter, is_text_only))


def _render_targets(paths: RunPaths,
                    spec: ChapterResetSpec) -> list[str]:
    records = document_file_service.build_follow_up_records_path(paths).relative_to(paths.output_root)
    store = document_file_service.build_image_store_path(paths).relative_to(paths.output_root)

    return ([str(path.relative_to(paths.output_root)) for path in spec.files]
            + [f"{records}: the record of {slug}" for slug in spec.follow_up_slugs]
            + [f"{store}: {cut_id} set aside, placed in no kept section" for cut_id in spec.unplaced_cut_ids]
            + [f"{store}: the {key} record of {record.get('frame')} by {record.get('chapter')}"
               for key, record in spec.frame_decisions])


def list_chapter_reset_targets(paths: RunPaths,
                               chapter: int,
                               is_text_only: bool = False) -> list[str]:
    """What `reset_from_chapter` would remove, one line each, relative to the run folder. Nothing is touched."""

    return _render_targets(paths, _build_reset_spec(paths, chapter, is_text_only))


def reset_from_chapter(paths: RunPaths,
                       chapter: int,
                       is_text_only: bool = False) -> list[str]:
    """Remove chapter `chapter` and every later one from phase 4, with the final parts. Returns what was listed."""

    spec = _build_reset_spec(paths, chapter, is_text_only)

    # STEP 1: the files, and the follow-up records a resume would otherwise read as accepted
    for path in spec.files:
        path.unlink(missing_ok=True)
    document_file_service.remove_follow_up_records(paths, spec.follow_up_slugs)

    # STEP 2: the log stays, as history; a row per follow-up taken back tells its reader that what came before is not
    # this life's, so a chapter written again does not open with the rejection of the text the reset removed
    log = paths.output_root / output_contract.WRITE_CONVERSATION_LOG
    if spec.follow_up_slugs and log.is_file():
        for slug in spec.follow_up_slugs:
            json_lines_utils.append_json_line(log, {"followUp": slug, "reset": True})

    # STEP 3: the cuts no kept section places set aside, and the frame decisions taken back
    if spec.unplaced_cut_ids or spec.frame_decisions:
        store = document_file_service.read_image_store(paths)
        for cut_id in spec.unplaced_cut_ids:
            store["cuts"][cut_id]["discarded"] = output_contract.RESET_DISCARD_REASON
        for key in document_file_service.FRAME_DECISION_KEYS:
            store[key] = [record for record in store.get(key) or [] if (key, record) not in spec.frame_decisions]
        document_file_service.write_image_store(paths, store)

    return _render_targets(paths, spec)
