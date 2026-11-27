"""The tools of phase 5's conversation: seven to read the sources and the document, a note to keep the
conversation small, four of phase 4's frame tools to recut a figure (`REVIEW_FRAME_TOOL_NAMES`), and one submit tool for
each of the two jobs. REQUIREMENTS.md R16.

Each class's docstring is the description the model reads. Only the submit tool the current follow-up expects is
offered; the reading tools are offered throughout.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from models.submit_tool_spec import SubmitToolSpec
from schemas import write_actions

__all__ = ["NOTES_READS", "FRAME_READS", "READ_MOMENTS_TOOL", "READ_TRANSCRIPT_TOOL", "FIND_TOOL", "REVIEW_FRAME_TOOL_NAMES", "READ_TOOLS", "SUBMIT_TOOLS", "FIGURE_ACTIONS",
           "FIGURE_KEEP", "FIGURE_REMOVE", "FIGURE_REPLACE", "FIGURE_RECUT", "ReadDocument", "ReadTranscript", "Find",
           "ReadMoments", "ReadFrameRecord", "LookAtFrame", "ReadEntries", "Note", "ReviewTodo", "ReviewTodos",
           "NewSentence", "Resolution"]

# The names of the reads that open the frames' notes, each the key of its class in `READ_TOOLS`
READ_MOMENTS_TOOL = "read_moments"
# The read of the transcript alone, which opens no frame's notes
READ_TRANSCRIPT_TOOL = "read_transcript"
READ_FRAME_RECORD_TOOL = "read_frame_record"
LOOK_AT_FRAME_TOOL = "look_at_frame"
FIND_TOOL = "find"
# The reads that open a frame: a search naming a frame says which read it made
FRAME_READS = (READ_FRAME_RECORD_TOOL, LOOK_AT_FRAME_TOOL)
# The reads that open the frames' notes, `find` included since it searches them too. "Not in the recording" rests
# on both of its channels, since the transcript alone misses what the notes hold.
NOTES_READS = (READ_MOMENTS_TOOL, *FRAME_READS, FIND_TOOL)
# What a resolution does with a figure line (`Resolution`'s `figure_action` holds one of these, or nothing)
FIGURE_KEEP = "keep"
FIGURE_REMOVE = "remove"
FIGURE_REPLACE = "replace"
FIGURE_RECUT = "recut"
FIGURE_ACTIONS = (FIGURE_KEEP, FIGURE_REMOVE, FIGURE_REPLACE, FIGURE_RECUT)


class ReadDocument(BaseModel):
    """Read the document as the review has edited it so far: one section by its heading, or all of it a chunk at a
    time."""

    heading: str = Field(default="", description="a section's heading as the document writes it, without the #s; the "
                                                 "whole document when empty")
    chunk: int = Field(default=1, description="which chunk of the whole document, from 1")


class ReadTranscript(BaseModel):
    """Read the meeting's transcript: every line spoken, with its id and time, in the order it was said. Give a time
    range, words to look for, or both; the lines come back a chunk at a time."""

    start: str = Field(default="", description="from this time (mm:ss or h:mm:ss); the recording's start when empty")
    end: str = Field(default="", description="up to this time; the recording's end when empty")
    words: str = Field(default="", description="only lines holding every one of these words, ignoring case")
    chunk: int = Field(default=1, description="which chunk of the lines, from 1")


class Find(BaseModel):
    """Find where words occur, ignoring case: the transcript's lines, the notes taken on each frame of the meeting,
    the entries the document was written from, and the document's sections. Each place holds every one of the
    words."""

    words: str = Field(description="the words to look for, separated by spaces")
    chunk: int = Field(default=1, description="which chunk of the places, from 1")


class ReadMoments(BaseModel):
    """Read the moments of a time range: each frame taken of the meeting then, with what happened on it, the notes
    taken on it, and the lines spoken while it was shown."""

    start: str = Field(default="", description="from this time (mm:ss or h:mm:ss); the recording's start when empty")
    end: str = Field(default="", description="up to this time; the recording's end when empty")
    chunk: int = Field(default=1, description="which chunk of the moments, from 1")


class ReadFrameRecord(BaseModel):
    """Read every note taken on one frame of the meeting: what it establishes, the text it shows, its diagram, what
    happened on it and what it left open."""

    frame: int = Field(description="the frame's number, as a moment or `find` shows it")


class LookAtFrame(BaseModel):
    """Look at one frame of the meeting yourself. Costly; use it when the notes cannot settle what the screen
    showed."""

    frame: int = Field(description="the frame's number, as a moment or `find` shows it")


class ReadEntries(BaseModel):
    """Read entries the document was written from, by their ids: each with its kind, its time, its frames and its
    text."""

    ids: list[str] = Field(description="entry ids, such as F<frame>.<note> or S<line>")


class Note(BaseModel):
    """Record what you searched and what is left, so it stays when the conversation is compacted."""

    text: str = Field(description="the note, one or two sentences")
    replaces: int | None = Field(default=None, description="leave it empty to add a note, the usual case; a number "
                                                           "overwrites that earlier note and its text is lost: give one "
                                                           "only when a reply says the note does not fit")


class ReviewTodo(BaseModel):
    kind: Literal["gap", "open_question", "contradiction", "unsupported", "figure"] = Field(
        description="gap, open_question, contradiction, unsupported or figure")
    section: str = Field(description="the heading of the section the sentence is in, as the document writes it, "
                                     "without the #s")
    block: int = Field(default=0, description="the number of the block the sentence stands in, as the `{block N}` "
                                              "line before it names it; the program then knows the section and the "
                                              "block's text")
    quote: str = Field(default="", description="the sentence the todo is about, copied from that block; when it is not "
                                               "found there word for word, the todo is about the whole block")
    missing: str = Field(description="what is missing, in one plain sentence")
    why_it_matters: str = Field(description="what a reader cannot do or decide without it")
    where: str = Field(description="where the answer may be (a time in the transcript, a frame, a moment), or "
                                   "\"not in the sources\"")


class ReviewTodos(BaseModel):
    """The todo list of the critique: what a reader of this document still cannot know, most important first. An
    empty list is the answer for a document without gaps."""

    todos: list[ReviewTodo] = Field(description="the todos, the one a reader loses most without first")


# A transcript line's id written with fewer digits than its four ("S081" for S0081): a reviewer read S0081 from the tool
# and cited it shortened, and its resolution was refused as citing a line the meeting does not have
_SHORT_LINE_ID = re.compile(r"^S(\d{1,3})$")


class NewSentence(BaseModel):
    text: str = Field(description="one sentence of the edit, as the document will print it")
    transcript_ids: list[str] = Field(default_factory=list, description="the ids of the transcript lines that say it")
    frames: list[int] = Field(default_factory=list, description="the numbers of the frames that show it")
    entry_ids: list[str] = Field(default_factory=list, description="the ids of the entries that hold it")

    @field_validator("transcript_ids", "entry_ids")
    @classmethod
    def pad_line_ids(cls,
                     ids: list[str]) -> list[str]:
        """Each id as the transcript writes it, `S` and four digits: the number a shortened id names is the same line.
        The entries of unread speech are named by those ids, so `entry_ids` is repaired too; a note's id is left alone."""

        stripped = [line_id.strip() for line_id in ids]

        return [f"S{int(match.group(1)):04d}" if (match := _SHORT_LINE_ID.match(line_id)) else line_id
                for line_id in stripped]


class Resolution(BaseModel):
    """The outcome of one todo: resolved, with the edit to the document and the sources of every new sentence; or
    unresolved, with the searches you made and the question for the reader."""

    status: Literal["resolved", "unresolved"] = Field(description="resolved when the sources answer the todo, "
                                                                  "unresolved when they do not")
    section: str = Field(default="", description="resolved: the heading of the section the edit is in, without the #s")
    block: int = Field(default=0, description="resolved: the number of the block the edit is in, as the `{block N}` "
                                              "line names it; with neither `replaces` nor `after`, the new sentences "
                                              "replace the whole block, so write all of it; an `after` not found in "
                                              "the block adds after the whole block")
    replaces: str = Field(default="", description="resolved: the sentence or sentences the edit replaces, copied "
                                                  "exactly; empty when the edit only adds")
    after: str = Field(default="", description="resolved, when the edit only adds: the sentence the new ones go "
                                               "right after, copied exactly; never together with `replaces`: one "
                                               "edit either replaces or adds")
    sentences: list[NewSentence] = Field(default_factory=list, description="resolved: the new sentences, in order, "
                                                                           "each with its sources")
    searches: list[str] = Field(default_factory=list, description="unresolved: each search you made, what you looked "
                                                                  "for and where")
    question: str = Field(default="", description="unresolved: the question worded for a reader of the document; "
                                                  "resolved, when the sources answer only part of the todo: the part "
                                                  "they do not answer, worded for a reader; empty otherwise")
    figure_action: Literal["", "keep", "remove", "replace", "recut"] = Field(
        default="", description="a figure todo only: keep (the figure shows its part whole), remove (it adds nothing the "
                                "text does not say), replace (its content is code, a table or a list: give it as "
                                "`markdown_block`), or recut (give the cut you made of the whole part as `cut`); "
                                "`replaces` then holds the figure's line, copied exactly")
    cut: str = Field(default="", description="figure recut only: the id of the cut you made, `cut-<n>`")
    markdown_block: str = Field(default="", description="figure replace only: the figure's content as one markdown "
                                                        "block read off its frame: a fenced code block, a table or a list")
    block_frames: list[int] = Field(default_factory=list, description="figure replace only: the frames the block is "
                                                                      "read from")
    lost_at: Literal["", "frame_notes", "outline", "section"] = Field(
        default="", description="resolved, only when the sources you cite cannot tell it: where the flow lost this "
                                "point: frame_notes (no note on a frame held it), outline (an entry held it and was set aside), section (the section left it "
                                "out); empty otherwise")


# A figure todo recuts its figure with phase 4's frame tools: it looks, cuts, undoes a cut and masks a secret. It never
# places an illustration, so `mark`, `leave_out` and `replace_with_diagram` are not offered.
REVIEW_FRAME_TOOL_NAMES = ("look", "cut", "undo_cut", "mask")
READ_TOOLS: dict[str, type[BaseModel]] = {"read_document": ReadDocument, READ_TRANSCRIPT_TOOL: ReadTranscript,
                                          FIND_TOOL: Find, READ_MOMENTS_TOOL: ReadMoments,
                                          READ_FRAME_RECORD_TOOL: ReadFrameRecord, LOOK_AT_FRAME_TOOL: LookAtFrame,
                                          "read_entries": ReadEntries, write_actions.NOTE_TOOL: Note,
                                          **{name: write_actions.FRAME_TOOLS[name] for name in REVIEW_FRAME_TOOL_NAMES}}
SUBMIT_TOOLS: dict[str, SubmitToolSpec] = {
    "submit_review": SubmitToolSpec(schema=ReviewTodos, job_kind="review_todos"),
    "submit_resolution": SubmitToolSpec(schema=Resolution, job_kind="review_resolution")}
