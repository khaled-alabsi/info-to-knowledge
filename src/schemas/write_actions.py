"""The tools of phase 4's conversation: the reading tools, a note to keep the conversation small, the frame tools a
chapter places its frames with, and one submit tool per kind of follow-up. REQUIREMENTS.md R12.

Each class's docstring is the description the model reads. Only the submit tool the current follow-up expects is
offered; the reading tools are offered throughout. Each submit tool's reply is judged by the validator of its
`job_kind`; the agenda shares its schema and validator with the calls path's parts call.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from models.submit_tool_spec import SubmitToolSpec
from schemas import illustration_actions
from schemas.document_chapter import DocumentChapter
from schemas.document_final import DocumentFinal
from schemas.document_outline import DocumentTableOfContents
from schemas.document_parts import DocumentParts
from schemas.draft_headings import DraftHeadings
from schemas.executive_summary_draft import ExecutiveSummaryDraft

__all__ = ["NOTE_TOOL", "READ_TOOLS", "FRAME_TOOLS", "SUBMIT_TOOLS", "ListTags", "ListBatches", "ReadNotes",
           "ReadMoments", "Find", "ReadFrame", "ReadDocument", "ReadTranscript", "Note"]


class ListTags(BaseModel):
    """List every tag the meeting's moments carry, sorted alphabetically so near duplicates sit together, with how
    many moments carry it in each chapter: the subjects (systems, tools, terms) and the kinds of facts (concept,
    procedure, reference, problem, risk, decision, status)."""

    chapter: int | None = Field(default=None, description="only this chapter; all chapters when empty")


class ListBatches(BaseModel):
    """List the meeting as a short sequence of batches, in time order: each with its time span, its frames, and an
    account in the third person of what the presenter set out to show, what they showed, what was said and where it
    ended. A map of the whole meeting; read the moments for its facts."""


class ReadNotes(BaseModel):
    """Read the meeting notes file, the whole meeting in the order it happened: one section per frame (what happened
    on screen, what was said while it was shown, and the numbered notes it gave) and one per span of speech no
    frame read. Give a chapter to read that chapter whole, in one read; with no chapter the sections come back a chunk at a time."""

    chapter: int | None = Field(default=None, description="only this chapter; the whole meeting when empty")
    chunk: int = Field(default=1, description="which chunk, from 1; ignored when a chapter is given")


class ReadMoments(BaseModel):
    """Read moments of the meeting: each with its time, frame, tags and entries (id, kind, text). Give a tag, a chapter
    or entry ids; the matching moments come back a chunk at a time."""

    tag: str = Field(default="", description="one tag as `list_tags` shows it, ignoring case")
    chapter: int | None = Field(default=None, description="a chapter")
    ids: list[str] = Field(default_factory=list, description="entry ids; the moments that hold them")
    chunk: int = Field(default=1, description="which chunk of the matches, from 1")


class Find(BaseModel):
    """Find the entries whose text holds every one of the words, ignoring case, each with its moment."""

    words: str = Field(description="the words to look for, separated by spaces")
    chunk: int = Field(default=1, description="which chunk of the matches, from 1")


class ReadFrame(BaseModel):
    """Look at one frame of the recording again: its image and its full record. Costly; use it only when an entry
    cannot be understood without the screen."""

    frame: int = Field(description="the frame number, as a moment shows it")


class ReadDocument(BaseModel):
    """Read the document as it stands: every accepted section, and pending markers for the rest."""

    chapter: int | None = Field(default=None, description="only this chapter; the whole document when empty")


class ReadTranscript(BaseModel):
    """Read the meeting's transcript: every line spoken, with its id and time, in the order it was said. Give a time
    range, words to look for, or both; the lines come back a chunk at a time."""

    start: str = Field(default="", description="from this time, as a moment shows it (mm:ss or h:mm:ss); the "
                                               "recording's start when empty")
    end: str = Field(default="", description="up to this time; the recording's end when empty")
    words: str = Field(default="", description="only lines holding every one of these words, ignoring case")
    chunk: int = Field(default=1, description="which chunk of the lines, from 1")


class Note(BaseModel):
    """Record a decision later requests must keep, such as where a subject is explained and that later sections link
    to it. Notes stay when the conversation is compacted."""

    text: str = Field(description="the decision, one or two sentences")
    replaces: int | None = Field(default=None, description="leave it empty to add a note, the usual case; a number "
                                                           "overwrites that earlier note and its text is lost: give one "
                                                           "only when a reply says the note does not fit")


# A chapter places its images itself: the frame tools of schemas/illustration_actions.py, which
# `write_reading_service` hands to `illustration_action_service`
FRAME_TOOLS: dict[str, type[BaseModel]] = dict(illustration_actions.ACTIONS)
# The one reading tool that reads nothing: it spends no read
NOTE_TOOL = "note"
READ_TOOLS: dict[str, type[BaseModel]] = {"list_tags": ListTags,
                                          "list_batches": ListBatches, "read_notes": ReadNotes, "read_moments": ReadMoments,
                                          "find": Find, "read_frame": ReadFrame, "read_document": ReadDocument,
                                          "read_transcript": ReadTranscript, NOTE_TOOL: Note, **FRAME_TOOLS}
SUBMIT_TOOLS: dict[str, SubmitToolSpec] = {
    "submit_executive_summary_draft": SubmitToolSpec(schema=ExecutiveSummaryDraft, job_kind="executive_summary_draft"),
    "submit_agenda": SubmitToolSpec(schema=DocumentParts, job_kind="parts"),
    "submit_draft_headings": SubmitToolSpec(schema=DraftHeadings, job_kind="draft_headings"),
    "submit_table_of_contents": SubmitToolSpec(schema=DocumentTableOfContents, job_kind="table_of_contents"),
    "submit_chapter": SubmitToolSpec(schema=DocumentChapter, job_kind="chapter"),
    "submit_final": SubmitToolSpec(schema=DocumentFinal, job_kind="final")}
