"""The messages of phase 5's conversation, the review. REQUIREMENTS.md R16.

The system message is the review's instruction, `review.md`, sent once for the whole session. The first follow-up
asks for the critique and carries the finished document whole; each later one asks for one todo, naming its sentence,
what is missing and where the worker thought the answer may be. After the conversation is compacted, the starting
messages carry the todo list and each todo's outcome so far.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from models import ingesting_profile_vocabulary, output_contract
from prompts import system_prompt_builder
from schemas import review_actions
from utils import document_edit_utils

__all__ = ["build_review_system_text", "build_critique_text", "build_resolve_text", "render_todo_plan",
           "NO_PLAN_YET"]

# The starting messages' todo part before the critique is accepted.
NO_PLAN_YET = "The todo list is not written yet: the critique writes it."


def build_review_system_text(ingesting_profile: str,
                             tags: list[str],
                             has_frames: bool,
                             is_pdf: bool = False) -> str:
    """`review.md` with the run's ingesting profile's file, then what the recording itself is."""

    workflow = "pdf" if is_pdf else "video" if has_frames else "audio"
    text = system_prompt_builder.build_system_prompt(output_contract.REVIEW_PHASE,
                                                     {ingesting_profile_vocabulary.PROMPT_FIELD: ingesting_profile,
                                                      system_prompt_builder.WORKFLOW_FIELD: workflow})
    recording = system_prompt_builder.render_recording_section(tags, has_frames, is_pdf)

    return f"{text}\n\n{recording}" if recording else text


def build_critique_text(document: str,
                        max_todos: int) -> str:
    """Job 1: the whole document, and what to answer with."""

    return (f"Job 1, the critique. The finished document follows in full, each block of its text numbered by a "
            f"`{{block N}}` line before it. Read all of it, then submit your todo list with `submit_review`: at most "
            f"{max_todos} todos, most important first, each with its block's number, its section, its sentence, what "
            f"is missing, why it matters and where the answer may be. Every figure the document places follows this "
            f"message as an image: look at each one.\n\n"
            f"{document_edit_utils.render_numbered_document(document.strip())}")


# A placed figure's image file, as its figure line names it
_FIGURE_FILE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")


def list_placed_figures(document: str,
                        document_dir: Path) -> list[Path]:
    """The image files the document's figure lines place, in their order, once each and only those on disk: the
    critique looks at what a reader sees, never at cuts made and not placed."""

    names = dict.fromkeys(match.group(1) for match in _FIGURE_FILE.finditer(document))

    return [document_dir / name for name in names if (document_dir / name).is_file()]


# A figure line, `![alt](resources/figure-07.png)`, or its caption, `*Figure 7. ...*`
_FIGURE_QUOTE = re.compile(r"^(?:!\[|\*?Figure\s+\d+\.)")


def _build_figure_edit_text(todo: dict[str, Any],
                            total: int,
                            figure_frame: str = "") -> str:
    """Job 2 for a todo about a figure line or its caption that loses something: the one answer the checks accept. Told
    only in the instructions, a reviewer spent three answers editing around a figure and was refused each time."""

    return (f"Job 2, {todo['id']} of {total}: figure. In \"{todo['section']}\", the figure: \"{todo['quote']}\"\n"
            f"What it loses: {todo.get('missing', '')}\n"
            # The frame by the published provenance: told only the reviewer's own guess, a worker searched two frames
            # for the one its figure came from (DEFECTS 246)
            + (f"`look_at_frame` at {figure_frame}, the frame it was cut from, then answer " if figure_frame else
               f"`look_at_frame` at the frame it was cut from ({todo.get('where', 'named in the figure')}), then answer ")
            + f"`submit_resolution` with status \"resolved\", `replaces` the figure's line copied exactly, and one "
            f"`figure_action`: \"{review_actions.FIGURE_REPLACE}\" when its content is code, a table or a list: give that content read off "
            f"the frame as `markdown_block` (a fenced code block with its language and every line in order, a "
            f"markdown table, or a list) and the frame in `block_frames`; \"{review_actions.FIGURE_RECUT}\" when it shows a picture a "
            f"reader needs and lost part of it: `look` at its frame, `cut` the whole part with a margin, look at the "
            f"cut, and give its id as `cut`; \"{review_actions.FIGURE_REMOVE}\" when the text around it already says what it shows; "
            f"\"{review_actions.FIGURE_KEEP}\" when it shows its part whole after all. Never write a sentence about the figure.")


def _build_figure_close_text(todo: dict[str, Any],
                             total: int) -> str:
    """Job 2 for any other todo whose quote is a figure line: closed at once, since the review leaves figures as they
    are."""

    return (f"Job 2, {todo['id']} of {total}: {todo['kind']}. In \"{todo['section']}\", about the figure: "
            f"\"{todo['quote']}\"\n"
            f"The review leaves figures and their captions as they are, and adds no sentence about them. Close this "
            f"todo now: `submit_resolution` with status \"resolved\", no `replaces`, no sentence, and `searches` "
            f"saying that the review leaves figures as they are. Read nothing first.")


_NOTE_FRAME = re.compile(r"^F(\d+)\.")


def _name_frame(frame: object,
                times: dict) -> str:
    """"frame 116 at 46:26", or "frame 116" when its time is not known: the reads take a time, and a reviewer told the
    number alone looked for frame 116 at 04:30 and 06:00 (DEFECTS 613)."""

    time = times.get(int(frame)) if str(frame).isdigit() else None

    return f"frame {frame} at {time}" if time else f"frame {frame}"


def _render_related_lines(lines: list,
                          times: dict) -> str:
    """The lines of the frames' notes that use a rare word of the todo, each with its frame and its time, or nothing."""

    if not lines:
        return ""

    return ("Notes of these frames use a rare word of it, and the answer may stand there (read them with `read_moments` "
            "before you call it unresolved):\n" + "".join(f"- {_name_frame(frame, times)}: {line}\n" for frame, line in lines))


def _build_sentence_text(todo: dict[str, Any],
                         total: int,
                         sources: list[str],
                         changed_section: str,
                         other_sources: list[str] | None = None) -> str:
    """Job 2 for a todo about a sentence of the document."""

    # Without the notes a reviewer guessed one frame per todo, one number up each time, and cited it
    written_from = (f"The document wrote this sentence from the notes {', '.join(sources)}: start with them (F<frame>.<note> "
                    f"is a note on that frame, S<line> a transcript line).\n" if sources else "")
    # Named by id alone, the notes went unread: a sentence its note stated word for word was judged against the figure
    # beside it and replaced with that figure's content (DEFECTS 402)
    notes = list(todo.get("sourceNotes") or [])
    times = dict(todo.get("frameTimes") or {})
    written_from += "".join(
        f"- {note_id}"
        + (f" ({_name_frame(match.group(1), times)})"
           if (match := _NOTE_FRAME.match(str(note_id))) and int(match.group(1)) in times else "")
        + f": {text}\n" for note_id, text in notes)
    if todo.get("isStatedByNotes"):
        written_from += ("The sentence says what these notes say. Before you change it, read the frame or transcript "
                         "lines of these notes: when they show what the notes say, the sentence is right, so answer "
                         "`submit_resolution` with status \"resolved\", no edit and no sentence, and say in `searches` "
                         "which source shows it; a figure or another frame beside the sentence is no source of it.\n")
    # An earlier todo's answer had replaced the sentence a later todo was about, and the later one, asked about the old
    # sentence, edited it again and was refused as naming a sentence the section no longer has
    changed = (f"An earlier todo's edit changed this section, and the sentence is no longer in it. It now reads: "
               f"\"{' '.join(changed_section.split())}\"\nWhen it already does what this todo asks, answer "
               f"`submit_resolution` with status \"resolved\", no edit and no sentence, and say in `searches` where it "
               f"does; otherwise edit the section as it reads now.\n" if changed_section else "")

    return (f"Job 2, {todo['id']} of {total}: {todo['kind']}. In \"{todo['section']}\", about the sentence: "
            f"\"{todo['quote']}\"\n"
            f"{changed}"
            f"Missing: {todo['missing']}\n"
            f"{written_from}"
            # The other side of a contradiction, by its notes: a reviewer read four frames to find it (DEFECTS 245)
            + (f"The other side of it stands in a sentence written from the notes {', '.join(other_sources)}: read "
               f"them too.\n" if other_sources else "")
            + _render_related_lines(list(todo.get("relatedLines") or []), times)
            + f"You thought the answer may be: {todo['where']}\n"
            f"Find it in the sources first. Then `submit_resolution`: resolved with the edit and its sources, or "
            f"unresolved with your searches and the question for the reader. An unresolved answer is accepted only "
            # Two todos running were refused for it: each worker counted the frames' notes an earlier todo had read
            f"after `read_moments` or `find` has read the frames' notes of that time in this todo's own turns, even "
            f"when an earlier todo read the same notes; list the searches you made, a frame an earlier todo read "
            f"among them.")


def build_resolve_text(todo: dict[str, Any],
                       total: int,
                       sources: list[str],
                       changed_section: str = "") -> str:
    """Job 2, for one todo: `todo` carries its id, kind, section, quote, what is missing and where to look; `sources`
    are the note ids the sentence was written from, which the published document no longer shows; `changed_section`
    is its section as it reads now when an earlier todo's edit took the quoted sentence out, else empty. The program may
    add `figureFrame` (a figure's frame) and `otherSources` (a contradiction's other side's notes) to the todo."""

    is_figure_line = bool(_FIGURE_QUOTE.match(str(todo.get("quote") or "").strip()))
    if is_figure_line and todo.get("kind") == "figure":
        # The program's hints ride in the todo: the figure's frame by the provenance, a contradiction's other side
        return _build_figure_edit_text(todo, total, str(todo.get("figureFrame") or ""))
    if is_figure_line:
        return _build_figure_close_text(todo, total)

    return _build_sentence_text(todo, total, sources, changed_section, list(todo.get("otherSources") or []))


def render_todo_plan(todos: list[dict[str, Any]]) -> str:
    """The todo list as the starting messages carry it: each todo's id, kind, section, what is missing and its outcome so far."""

    if not todos:
        return "You listed no todo."
    lines = [f"- {todo['id']} ({todo['kind']}) in \"{todo['section']}\": {todo['missing']} [{todo['status']}]"
             for todo in todos]

    return "The todos you listed, most important first:\n" + "\n".join(lines)
