"""The outline of one chunk of the meeting: the topics its entries fall into, each named by the entry it starts
at, and the entries set aside by name. REQUIREMENTS.md R3. The document's title is the contents call's.

The entries are handed over in the meeting's order, so a topic is every entry from its start up to the next topic's
start: the model names the starts, and the program fills in the ids (`utils/document_plan_utils.py`). A model asked
to copy hundreds of ids miscounts; one asked where each subject begins does not have to count. A subject that comes
back later is a later start with the same title, and the program joins the two.

The outline is DATA, so a program can check the thing summarising hides: that nothing was dropped.
An entry the model judges worthless is not deleted, it is listed under `set_aside_entry_ids`, and
that list is kept in the provenance file.

`DocumentTableOfContents` below is the reply of the retired table-of-contents model step, kept until
`submit_table_of_contents` is removed (`todo.md`): the outline's `set_aside_entry_ids` and `not_established`, with
`chapters` of topics in place of `topics`, judged by the outline's checks.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["DocumentOutline", "DocumentTableOfContents", "DocumentTopic", "TableOfContentsChapter"]

# What may be set aside and what is left open mean the same in an outline and in the table of contents.
_SET_ASIDE_DESCRIPTION = ("ids of entries that carry no facts about the subject: greetings, branding, a name caption, "
                          "filler")
_NOT_ESTABLISHED_DESCRIPTION = "what the video raises and does not settle, one short sentence each"


class DocumentTopic(BaseModel):
    """One topic of the document, named by where it starts: it holds every entry from there to the next topic."""

    title: str = Field(description="what the topic is about, in a few words")
    first_entry_id: str = Field(description="the id of the entry this topic starts at, such as F<frame>.<note>; the topic holds "
                                            "every entry from there up to the next topic's start")


class DocumentOutline(BaseModel):
    """How everything one chunk of the video established is sorted into topics, with nothing dropped."""

    topics: list[DocumentTopic] = Field(description="the topics, in the order their starts come in the list")
    set_aside_entry_ids: list[str] = Field(description=_SET_ASIDE_DESCRIPTION)
    not_established: list[str] = Field(description=_NOT_ESTABLISHED_DESCRIPTION)


class TableOfContentsChapter(BaseModel):
    """One chapter: one point of the agenda, with the headings under it."""

    title: str = Field(description="the agenda point's title, or a clearer one for a reader, in two to eight words")
    first_entry_id: str = Field(description="the id of the entry this chapter starts at; the first chapter starts at the "
                                            "first entry")
    topics: list[DocumentTopic] = Field(description="the headings of this chapter, in the order they came; a subject "
                                                     "the talk comes back to later keeps its title there")


class DocumentTableOfContents(BaseModel):
    """The document's whole table of contents: one chapter per agenda point and the headings under each, with every
    entry in one heading or set aside (REQUIREMENTS.md R12): the whole table in one reply."""

    chapters: list[TableOfContentsChapter] = Field(description="one chapter per agenda point, in the agenda's order")
    set_aside_entry_ids: list[str] = Field(description=_SET_ASIDE_DESCRIPTION)
    not_established: list[str] = Field(description=_NOT_ESTABLISHED_DESCRIPTION)
