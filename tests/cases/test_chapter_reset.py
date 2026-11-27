"""`i2k reset-chapter N`: phase 4 set back to just before chapter N, so a chapter whose notes changed (phase 3 finished
frames phase 4 had written without) is asked again from its draft headings, and chapters 1 to N-1 stay accepted.
(2026-09-30: phase 3 left 61 frames of the last chapter without notes, and only `reset-to 3` could redo that chapter,
removing every accepted chapter with it.)
"""

import json

from support import fixtures as fx
from support import harness

from models import output_contract
from services import chapter_reset_service
from services import document_file_service


def _build_run():
    paths = fx.make_paths()
    writing = paths.output_root / output_contract.WRITING_DIR_NAME
    files = {
        "draft-headings/chapter-01.json": {"headings": [{"title": "A"}, {"title": "B"}]},
        "draft-headings/chapter-02.json": {"headings": [{"title": "C"}]},
        "chapters/chapter-01-text.json": {"sections": 2},
        "chapters/chapter-02-text-1.json": {"sections": 1},
        "requests/chapter-01-text.md": "request",
        "requests/chapter-02.md": "request",
        "requests/chapter-02-text-1.md": "request",
        "sections/heading-001-1.md": "A. [F1.1]\n",
        "sections/heading-002-1.md": "B. [F2.1]\n",
        "sections/heading-003-1.md": "C. [F3.1]\n",
    }
    for name, content in files.items():
        path = writing / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    calls = paths.output_root / "calls"
    calls.mkdir(parents=True, exist_ok=True)
    for name in ("chapter-01.json", "chapter-01-text.json", "chapter-02.json", "chapter-02-text-1.json", "summary.json",
                 "frames-000-004.json"):
        (calls / name).write_text("{}", encoding="utf-8")
    for slug in ("parts", "chapter-01", "chapter-01-text", "chapter-02", "chapter-02-text-1", "contents", "summary"):
        document_file_service.write_follow_up_record(paths, slug, {"status": "accepted"})

    return paths, writing, calls


def test_a_reset_from_a_chapter_removes_it_and_what_follows_and_keeps_the_chapters_before():
    paths, writing, calls = _build_run()
    listed = chapter_reset_service.list_chapter_reset_targets(paths, 2)
    harness.check("the listing names chapter 2's files, its section and the final parts, and neither section of chapter 1",
                  "04_writing/draft-headings/chapter-02.json" in listed and "04_writing/sections/heading-003-1.md" in listed
                  and "calls/summary.json" in listed and not any("chapter-01" in line or "heading-001-1" in line or "heading-002-1" in line
                                                                 for line in listed), str(listed))
    harness.check("listing touches nothing", (writing / "draft-headings" / "chapter-02.json").is_file())
    chapter_reset_service.reset_from_chapter(paths, 2)
    records = document_file_service.read_follow_up_records(paths)
    harness.check("chapter 2 and the final parts are forgotten, chapter 1 and the agenda kept",
                  sorted(records) == ["chapter-01", "chapter-01-text", "parts"], str(sorted(records)))
    harness.check("chapter 2's files are gone", not (writing / "draft-headings" / "chapter-02.json").exists()
                  and not (writing / "chapters" / "chapter-02-text-1.json").exists()
                  and not (writing / "sections" / "heading-003-1.md").exists()
                  and not (calls / "chapter-02-text-1.json").exists())
    harness.check("chapter 1's files and phase 3's calls stay", (writing / "sections" / "heading-002-1.md").is_file()
                  and (writing / "chapters" / "chapter-01-text.json").is_file() and (calls / "frames-000-004.json").is_file())


def test_a_reset_from_a_chapter_with_no_draft_headings_before_it_is_refused():
    paths, writing, _ = _build_run()
    (writing / "draft-headings" / "chapter-01.json").unlink()
    try:
        chapter_reset_service.list_chapter_reset_targets(paths, 2)
        refused = ""
    except ValueError as error:
        refused = str(error)
    harness.check("refused by name: the sections of the earlier chapters cannot be counted", "chapter-01" in refused, refused)


def test_a_text_only_reset_keeps_the_draft_headings_and_removes_the_texts_from_the_chapter_on():
    paths, writing, calls = _build_run()
    chapter_reset_service.reset_from_chapter(paths, 2, is_text_only=True)
    records = document_file_service.read_follow_up_records(paths)
    harness.check("chapter 2's text and the final parts are forgotten, its draft headings kept",
                  sorted(records) == ["chapter-01", "chapter-01-text", "chapter-02", "parts"], str(sorted(records)))
    harness.check("chapter 2's draft headings, their request and their call log stay",
                  (writing / "draft-headings" / "chapter-02.json").is_file() and (writing / "requests" / "chapter-02.md").is_file()
                  and (calls / "chapter-02.json").is_file())
    harness.check("chapter 2's text, its request, its call log and its section are gone",
                  not (writing / "chapters" / "chapter-02-text-1.json").exists()
                  and not (writing / "requests" / "chapter-02-text-1.md").exists()
                  and not (calls / "chapter-02-text-1.json").exists() and not (writing / "sections" / "heading-003-1.md").exists())


def test_a_reset_sets_aside_the_cuts_no_kept_section_places():
    """A wrong cut of a chapter's earlier attempt was offered to the chapter written again, and placed again
    (2026-10-04)."""

    paths, writing, _ = _build_run()
    (writing / "sections" / "heading-002-1.md").write_text("B. [F2.1]\n\n![a chart](cut-001 \"why\") [F2.1]\n", encoding="utf-8")
    document_file_service.write_image_store(paths, {"cuts": {"cut-001": {"frame": "frame-002", "file": "a.png"},
                                                             "cut-002": {"frame": "frame-003", "file": "b.png"},
                                                             "cut-003": {"frame": "frame-003", "file": "c.png",
                                                                         "discarded": "undone"}}})
    listed = chapter_reset_service.list_chapter_reset_targets(paths, 2, is_text_only=True)
    harness.check("the listing names the unplaced cut", any("cut-002 set aside" in line for line in listed), str(listed))
    chapter_reset_service.reset_from_chapter(paths, 2, is_text_only=True)
    cuts = document_file_service.read_image_store(paths)["cuts"]
    harness.check("the cut a kept section places stays in use, the unplaced one is set aside, an undone one stays undone",
                  "discarded" not in cuts["cut-001"] and cuts["cut-002"]["discarded"] == "reset"
                  and cuts["cut-003"]["discarded"] == "undone", str(cuts))


def test_a_reset_marks_each_follow_up_it_takes_back_in_the_conversation_log():
    paths, _, _ = _build_run()
    log = paths.output_root / output_contract.WRITE_CONVERSATION_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"followUp": "chapter-02-text-1", "blocked": "1 answers rejected"}) + "\n", encoding="utf-8")
    chapter_reset_service.reset_from_chapter(paths, 2, is_text_only=True)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    harness.check("the history stays and a reset row follows for the text taken back",
                  rows[0].get("blocked") and {"followUp": "chapter-02-text-1", "reset": True} in rows[1:], str(rows))


def test_a_reset_removes_the_frame_decisions_of_the_follow_ups_it_takes_back():
    """Six leave_out records of a chapter's text stayed after its reset, and would have counted its needed frames as
    accounted for in the text written again (2026-10-04)."""

    paths, _, _ = _build_run()
    document_file_service.write_image_store(paths, {"cuts": {}, "leftOut": [
        {"frame": "frame-001", "reason": "r", "chapter": "chapter-01-text"},
        {"frame": "frame-003", "reason": "r", "chapter": "chapter-02-text-1"}],
        "diagramReplacements": [{"frame": "frame-003", "reason": "r", "chapter": "chapter-02-text-1"}]})
    chapter_reset_service.reset_from_chapter(paths, 2, is_text_only=True)
    store = document_file_service.read_image_store(paths)
    harness.check("chapter 2's decisions are gone, chapter 1's stay",
                  [record["frame"] for record in store["leftOut"]] == ["frame-001"] and store["diagramReplacements"] == [],
                  str(store))
