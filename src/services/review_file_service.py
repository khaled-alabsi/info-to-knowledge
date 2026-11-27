"""Phase 5's files, REQUIREMENTS.md R16:

    05_review/
      before.md                 the published document as the review found it, front matter and all
      document.md               before.md with every accepted edit made, in order: what the worker reads and edits
      todos.json                the store: the hash of before.md, the critique's outcome, and each todo with its
                                outcome, its edit, its sources, its searches and its question
      notes.json                the worker's notes
      answers/<slug>.json       each answer as submitted, judged from disk
      logs/conversation.jsonl   every call of the conversation (written by the conversation's loop)
      review.md                 the report, written last: the phase's file
      earlier/<time>/           every earlier review of this run, whole: a review that starts over (phase 5 published
                                another document) moves the one before it here, never deletes it, so every critique
                                stays on disk to improve the flow's instructions from

The store is the one record of what was decided. `document.md` is derived from it and `before.md`, so a kill between
two writes never leaves an edit made twice or lost: the next life derives the document again.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_client import path_sandbox
from models import output_contract
from models.run_paths import RunPaths
from schemas import review_actions
from services import conversation_log_service
from services import phase_file_service
from utils import document_edit_utils
from utils import json_store_utils

__all__ = ["is_critique_accepted", "build_todo_id", "build_review_dir", "build_earlier_dir", "build_before_path", "build_working_document_path",
           "build_store_path",
           "build_answer_path", "compute_text_hash", "read_store", "write_store", "start_review", "write_answer",
           "read_answer", "write_notes", "read_notes", "write_report", "read_entry_statuses", "list_follow_up_reads", "build_notes_path"]

_BEFORE_FILE = "before.md"
_WORKING_DOCUMENT_FILE = "document.md"
_STORE_FILE = "todos.json"
_NOTES_FILE = "notes.json"
_ANSWERS_DIR_NAME = "answers"
_EARLIER_DIR_NAME = "earlier"
_LOGS_DIR_NAME = "logs"
# What one review consists of, moved together when a new review starts. Its conversation log goes too: a read of the
# next review's todo of the same id would otherwise be counted from this one's.
_REVIEW_PARTS = (_BEFORE_FILE, _WORKING_DOCUMENT_FILE, _STORE_FILE, _NOTES_FILE, _ANSWERS_DIR_NAME, _LOGS_DIR_NAME,
                 output_contract.REVIEW_REPORT_FILE)


def build_todo_id(number: int) -> str:
    """The id a todo is known by in the store, the messages, the checks and the report: its place in the critique."""

    return f"TODO-{number:02d}"


def build_review_dir(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.REVIEW_DIR_NAME


def build_earlier_dir(paths: RunPaths) -> Path:
    return build_review_dir(paths) / _EARLIER_DIR_NAME


def build_before_path(paths: RunPaths) -> Path:
    return build_review_dir(paths) / _BEFORE_FILE


def build_working_document_path(paths: RunPaths) -> Path:
    return build_review_dir(paths) / _WORKING_DOCUMENT_FILE


def build_store_path(paths: RunPaths) -> Path:
    return build_review_dir(paths) / _STORE_FILE


def build_notes_path(paths: RunPaths) -> Path:
    return build_review_dir(paths) / _NOTES_FILE


def build_answer_path(paths: RunPaths,
                      slug: str) -> Path:
    """Resolved through the jail: a slug is part of a file name."""

    return path_sandbox.resolve_within(paths.output_root,
                                       f"{output_contract.REVIEW_DIR_NAME}/{_ANSWERS_DIR_NAME}/{slug}.json")


def compute_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_store(paths: RunPaths) -> dict[str, Any]:
    """The store a previous life of the phase left; an empty dict when there is none."""

    path = build_store_path(paths)

    return dict(json_store_utils.read_json(path)) if path.is_file() else {}


def is_critique_accepted(store: dict[str, Any]) -> bool:
    """Has the review's critique an accepted answer? Its todos are recorded only then, an empty list included."""

    return store.get("todos") is not None


def write_store(paths: RunPaths,
                store: dict[str, Any]) -> None:
    json_store_utils.write_json(build_store_path(paths), store)


def _move_earlier_review(paths: RunPaths) -> None:
    """The review on disk, when there is one, moved whole into `earlier/<time>/`. Nothing is deleted."""

    folder = build_review_dir(paths)
    if not (folder / _STORE_FILE).is_file():
        return
    parts = [folder / name for name in _REVIEW_PARTS if (folder / name).exists()]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = build_earlier_dir(paths) / stamp
    number = 1
    while target.exists():
        number += 1
        target = build_earlier_dir(paths) / f"{stamp}-{number}"
    target.mkdir(parents=True)
    for part in parts:
        shutil.move(str(part), str(target / part.name))


def start_review(paths: RunPaths,
                 published_text: str) -> dict[str, Any]:
    """A new review of `published_text`: its copy as `before.md`, and a store with no todo list yet. The review an
    earlier life left is moved whole into `earlier/<time>/`, so its critique stays on disk and no answer it wrote can
    be taken for this review's."""

    _move_earlier_review(paths)
    build_review_dir(paths).mkdir(parents=True, exist_ok=True)
    build_before_path(paths).write_text(published_text, encoding="utf-8")
    store = {"schemaVersion": 1, "sourceHash": compute_text_hash(published_text), "reviewedHash": "",
             "critique": {"status": "pending", "attempts": 0, "reason": ""}, "todos": None}
    write_store(paths, store)

    return store


# The critique's answer, whose todos each name the section their quote stands in
CRITIQUE_SLUG = "review-todos"


# A search naming a frame it read: "read_frame_record 4", "read_frame_record frame-004"
NAMED_FRAMES = re.compile(rf"(?:{'|'.join(map(re.escape, review_actions.FRAME_READS))})\D{{0,12}}?(\d+)")


def write_answer(paths: RunPaths,
                 slug: str,
                 answer: dict[str, Any]) -> Path:
    path = build_answer_path(paths, slug)
    answer = _fill_from_blocks(paths, answer)
    written = (_move_quotes_under_their_headings(paths, answer) if slug == CRITIQUE_SLUG
               else _drop_unmade_searches(paths, slug, answer))
    json_store_utils.write_json(path, written)

    return path


def list_read_frames(paths: RunPaths,
                     slug: str) -> set[int]:
    """The frames the review conversation read with a frame read, up to and through follow-up `slug`."""

    frames = set()
    for read in list_reads_up_to(paths, slug):
        name, _, args = read.partition(" ")
        frame = (json.loads(args or "{}") or {}).get("frame") if name in review_actions.FRAME_READS else None
        if frame is not None and str(frame).isdigit():
            frames.add(int(frame))

    return frames


def _drop_unmade_searches(paths: RunPaths,
                          slug: str,
                          answer: dict[str, Any]) -> dict[str, Any]:
    """The answer without the searches that name a frame the review never read, when a search it made remains: the
    program knows what was read, and a reviewer that listed "read_frame_record 4" for a frame it had only found words
    on was refused and blocked the review (pdf-02). With no search made left, the answer stays as written
    and is refused: a resolution naming reads it never made has no search behind it."""

    searches = [str(search) for search in answer.get("searches") or []]
    read = list_read_frames(paths, slug)
    made = [search for search in searches if all(int(number) in read for number in NAMED_FRAMES.findall(search))]
    if not made or len(made) == len(searches):
        return answer

    return {**answer, "searches": made}


def _is_in_block(heading: str,
                 text: str,
                 quote: str) -> bool:
    return bool(quote.strip()) and document_edit_utils.find_quote(f"### {heading}\n\n{text}", heading, quote) is not None


def _fill_from_block(body: str,
                     item: dict[str, Any],
                     text_key: str) -> dict[str, Any]:
    """`item` (a critique's todo, or a resolution) with the section and the text of the block it names: its quote or
    `after` kept when the block holds it, else the block's whole text; a resolution naming a block and no sentence
    replaces the whole block. Every `{block N}` line a copy carried along is taken out (DEFECTS 610)."""

    item = {**item, **{key: document_edit_utils.remove_block_marks(str(item[key])) for key in ("quote", "replaces", "after")
                       if isinstance(item.get(key), str)}}
    found = document_edit_utils.read_block(body, item.get("block"))
    if found is None:
        return item
    named = str(item.get(text_key) or "")
    if not _is_in_block(*found, named):
        found = _find_block_holding(body, named) or found
    heading, text = found
    item = {**item, "section": heading}
    # A figure's action names the figure by its line: a resolution naming the figure's caption or its description block
    # was refused as giving no figure line, though the program knows the figure the block stands under (DEFECTS 713)
    is_figure_changed = item.get("figure_action") in (review_actions.FIGURE_REMOVE, review_actions.FIGURE_REPLACE,
                                                      review_actions.FIGURE_RECUT)
    if text_key == "replaces" and is_figure_changed and not str(item.get("replaces") or "").strip().startswith("!["):
        figure_line = _find_figure_line_above(body, item.get("block"), heading)
        if figure_line:
            return {**item, "replaces": figure_line}
    if text_key == "quote":
        return {**item, "quote": item["quote"] if _is_in_block(heading, text, str(item.get("quote") or "")) else text}
    replaces, after = str(item.get("replaces") or ""), str(item.get("after") or "")
    if after.strip():
        return {**item, "after": after if _is_in_block(heading, text, after) else text}

    return item if replaces.strip() else {**item, "replaces": text}


def _drop_sentences_said_elsewhere(body: str,
                                   answer: dict[str, Any]) -> dict[str, Any]:
    """`answer` without the new sentences the document already holds word for word outside the text they replace, so
    the edit says each once; with none left it removes what it replaces, or, adding after a sentence, is the answer
    with no edit that says where the document already does it. A resolution replaced a sentence by one its block, or
    another section, already held, and was refused for saying it twice (DEFECTS 715)."""

    sentences = answer.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        return answer
    folded_body = body.lower()
    replaces = str(answer.get("replaces") or "")
    texts = [str(sentence.get("text") or "") if isinstance(sentence, dict) else "" for sentence in sentences]
    kept = [sentence for sentence, text in zip(sentences, texts)
            if not _is_said_outside(folded_body, replaces.lower(), text)]
    if len(kept) == len(sentences):
        return answer
    if kept or replaces.strip():
        return {**answer, "sentences": kept}
    said = next(text for text in texts if _is_said_outside(folded_body, "", text))

    return {**answer, "after": "", "sentences": [],
            "searches": [*(answer.get("searches") or []), f'the document already says "{said.strip()}"']}


def _is_said_outside(folded_body: str,
                     folded_replaced: str,
                     sentence: str) -> bool:
    copy = sentence.strip().rstrip(".").lower()

    return len(copy) >= 20 and folded_body.count(copy) > folded_replaced.count(copy)


def _find_block_holding(body: str,
                        quote: str) -> tuple[str, str] | None:
    """The one block, (its heading, its text), that holds `quote` word for word; None when no block or more than one
    does. A copy found once is surer than a typed number: a resolution copied block 22's sentence and named block 82,
    under another heading, and was refused as replacing a sentence the section lacks (DEFECTS 714)."""

    if len(quote.strip()) < 20:
        return None
    holding = [block for block in document_edit_utils.list_document_blocks(body) if quote.strip() in block[1]]

    return holding[0] if len(holding) == 1 else None


def _find_figure_line_above(body: str,
                            number: object,
                            heading: str) -> str:
    """The figure line (`![...](...)`) of block `number` or of the nearest of the two blocks before it under the same
    heading (a figure, its caption, its description); empty when none holds one."""

    if not isinstance(number, int):
        return ""
    for at in range(number, max(number - 3, 0), -1):
        found = document_edit_utils.read_block(body, at)
        if found is None or found[0] != heading:
            return ""
        line = next((line.strip() for line in found[1].split("\n") if line.strip().startswith("![")), "")
        if line:
            return line

    return ""


def _fill_from_blocks(paths: RunPaths,
                      answer: dict[str, Any]) -> dict[str, Any]:
    """The answer with each todo or the resolution placed by the block it names, as `_fill_from_block` places it."""

    document_path = build_working_document_path(paths)
    if not document_path.is_file():
        return answer
    _front_matter, body = document_edit_utils.split_front_matter(document_path.read_text(encoding="utf-8"))
    if "todos" in answer:
        return {**answer, "todos": [_fill_from_block(body, todo, "quote") for todo in answer.get("todos") or []]}

    if answer.get("status") != "resolved":
        return answer

    return _drop_sentences_said_elsewhere(body, _fill_from_block(body, answer, "replaces"))


def _move_quotes_under_their_headings(paths: RunPaths,
                  answer: dict[str, Any]) -> dict[str, Any]:
    """The critique's todos, each quote that stands word for word under another heading than the one named given that
    heading, the one named kept as `namedSection`: the program knows where the sentence stands, and a todo naming a
    chapter's subject over the section holding the sentence was refused and dropped (pdf-02)."""

    document_path = build_working_document_path(paths)
    if not document_path.is_file():
        return answer
    _front_matter, body = document_edit_utils.split_front_matter(document_path.read_text(encoding="utf-8"))
    todos = []
    for todo in answer.get("todos") or []:
        section, quote = str(todo.get("section") or ""), str(todo.get("quote") or "")
        heading = (document_edit_utils.find_quote_heading(body, quote)
                   if quote and document_edit_utils.find_quote(body, section, quote) is None else None)
        todos.append({**todo, "section": heading, "namedSection": section} if heading is not None else todo)

    return {**answer, "todos": todos}


def read_answer(paths: RunPaths,
                slug: str) -> dict[str, Any]:
    return json_store_utils.read_json(build_answer_path(paths, slug))


def write_notes(paths: RunPaths,
                notes: list[str]) -> Path:
    path = build_notes_path(paths)
    json_store_utils.write_json(path, {"notes": list(notes)})

    return path


def read_notes(paths: RunPaths) -> list[str]:
    path = build_notes_path(paths)

    return list(json_store_utils.read_json(path).get("notes", [])) if path.is_file() else []


def write_report(paths: RunPaths,
                 report: str) -> Path:
    """The report, as the phase's own file."""

    path = phase_file_service.build_phase_file_path(paths, output_contract.PHASE_NUMBERS["review"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")

    return path


def read_entry_statuses(paths: RunPaths) -> dict[str, str]:
    """What phase 4 made of every entry, by id, from its coverage table (REQUIREMENTS.md R8); empty for a run whose
    provenance has none."""

    path = paths.output_root / output_contract.DRAFT_PROVENANCE_FILE
    if not path.is_file():
        return {}

    return dict((json_store_utils.read_json(path).get("coverage") or {}).get("entries") or {})


def list_reads_up_to(paths: RunPaths,
                     slug: str) -> list[str]:
    """Every read the review conversation made up to and through follow-up `slug`."""

    return conversation_log_service.list_reads_up_to(paths.output_root / output_contract.REVIEW_CONVERSATION_LOG, slug)


def list_follow_up_reads(paths: RunPaths,
                         slug: str) -> list[str]:
    """Every read one follow-up of the review made, as its name and arguments, in order."""

    return conversation_log_service.list_follow_up_reads(paths.output_root / output_contract.REVIEW_CONVERSATION_LOG, slug)
