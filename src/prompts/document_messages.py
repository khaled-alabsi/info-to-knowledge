"""Phase 4: the messages of the calls path (the agenda, one chunk's outline, the title, one section part, the
summary) and the human text of the conversation's follow-ups (the draft headings of a chapter and a chapter's text).

The calls path's messages are text only and sent everything they need: a call has no tools, so an entry the
message does not hold is an entry the model cannot use; the conversation's follow-ups read with their tools. The entries are handed over as the merge
computed them, each with its id, and the model is told to use only those ids.

The SYSTEM text is data, in `agent-context/instructions/parts.md`, `outline.md`, `contents.md`, `section.md` and
`summary.md`.
Only the human text below is a template.
"""

from __future__ import annotations

import math
import re

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate

from models import output_contract
from models.attempt_request import AttemptRequest
from prompts import system_prompt_builder
from utils import document_plan_utils, document_text_utils, mermaid_text_utils, section_markdown_utils

__all__ = ["build_chapter_text", "build_draft_headings_text", "build_parts_messages", "build_outline_messages",
           "build_contents_messages", "build_section_messages", "build_summary_messages",
           "build_table_of_contents_text", "render_batches_for_agenda", "render_entries", "render_moments",
           "render_batches", "PARTS_PHASE", "OUTLINE_PHASE", "CONTENTS_PHASE", "SECTION_PHASE", "SUMMARY_PHASE"]

PARTS_PHASE = "parts"
OUTLINE_PHASE = "outline"
CONTENTS_PHASE = "contents"
SECTION_PHASE = "section"
SUMMARY_PHASE = "summary"
# An outline only needs to know what a diagram is about, not every line of it.
_OUTLINE_DIAGRAM_CHARS = 300

PARTS_HUMAN_MESSAGE = PromptTemplate.from_template("""\
These are the {moment_count} moments of the meeting, in the order they happened. Each line is a moment's number,
its time in the recording, what the screen showed (its kind and its title), the first thing it established, and what
was said during it, shortened.

{moments}{batches}{document_headings}

The recording's file name, which may hold the meeting's name, a person's name and the date: {recording_name}
The speaker names the meeting tool's own transcript carries: {transcript_speakers}
Text on screen in the first frames: {opening_text}
Text on at least half of the frames, such as a slide footer: {recurring_text}

{previous_findings}
Divide the meeting into its agenda points, the first one starting at moment 1, and name its event and its speakers. Answer
through the function you were given, once.""")

OUTLINE_HUMAN_MESSAGE = PromptTemplate.from_template("""\
{part_line}These are the {entry_count} entries one chunk of the video established, in the order they occurred.
Each line is an id, its kind, and its text. `facts` is a fact a frame established from its frame
and the speech while it was shown. `diagram` is Mermaid source for a flow or an architecture, shortened
here; several can be versions of one diagram built up over frames. `open_question` is something a frame
raised and did not settle. `said` is a point the speech made that no frame noted. `url` is a web address and
what the page is. `action_item` is who does what, by
when. `question` is a question someone asked and the answer given. `speech` is something said that no frame
covered.

{entries}

{earlier_topics}{contradictions}
{previous_findings}
Name where each topic starts, in the order of the list: a topic holds every entry from its start up to the next
topic's start. Answer through the function you were given, once.""")

# R12: the whole table of contents, one follow-up of the conversation: chapters and headings for every entry at once.
TABLE_OF_CONTENTS_HUMAN_MESSAGE = PromptTemplate.from_template("""\
These are all {entry_count} entries of the document, in the order they occurred. Each line is an id, its kind, and
its text: `facts` a fact a frame established, `diagram` Mermaid source (shortened here), `open_question` raised and
not settled, `url` a web address, `action_item` who does what by when, `question` a question and the answer given,
`speech` what was said.

{entries}

The agenda you accepted, one chapter per point, each with the entry its time begins at:
{points}

Your draft headings of each chapter:
{draft_headings}

{contradictions}{previous_findings}
The meeting runs {minutes} minutes, so about {low} to {high} headings in all: one for every three to five minutes of
talk.

Write the whole table of contents: one chapter per agenda point, {point_count} in all, each with its title, where it
starts and its headings, each heading with where it starts. The program puts chapters and headings in the order their
starts come and fills in every entry. Answer through the function you were given, once.""")

# The draft headings of one chapter, before the table of contents.
DRAFT_HEADINGS_HUMAN_MESSAGE = PromptTemplate.from_template("""\
Find the draft headings of chapter {chapter} of {chapter_count}, "{title}". Read it in the notes file: `read_notes`
with chapter {chapter}: the whole chapter comes back in one read. Then say what the presenter set out to do in it and
which subjects it dealt with, each with the id where it starts, copied as the notes print it: a frame's note as
`F<frame>.<note>`, or a line of speech as its `Said:` line prints it, `S<line>`; a line of speech heard over a frame keeps
its `S` id and is never a note of that frame. {start_ids}{document_headings}{previous_findings}Answer through the function you
were given, once.""")

EARLIER_TOPICS = PromptTemplate.from_template("""\
The topics earlier chunks of the video already made. When an entry continues one of them, put it in a
topic with exactly that title; make a new topic only for a new subject:
{titles}

""")

CONTENTS_HUMAN_MESSAGE = PromptTemplate.from_template("""\
{parts}These are the {topic_count} topics of the document, in the order they came up in the video. Each line
is the topic's number, its title, how many entries it holds, and one of them.

{topics}

{previous_findings}
Write the document's title. Answer through the function you were given, once.""")

SECTION_HUMAN_MESSAGE = PromptTemplate.from_template("""\
The document's title: {document_title}
This section's chapter: {chapter_title}
This section's topic: {topic_title}
The chapter's other topics, which you do not write about: {other_topics}

{earlier_parts}The {entry_count} entries of this topic. Each line is an id, its kind, and its text.

{entries}

{previous_findings}
Use every one of these {entry_count} ids in the brackets of at least one block of your section, and no other id.
Answer through the function you were given, once.""")

# The marks on a speech entry's line that tell the writer it may be cited and need not be
_HEARD_BY_FRAME_MARK = "(heard by a frame)"
_BRIEF_REPLY_MARK = "(a brief reply)"

# One chapter, every heading of it, in one follow-up, or in parts of whole headings past `document.chapterEntries` notes.
# Its closing repeats the narration rule and the banned words, the two most frequent chapter rejections over six runs though write.md states both, and the rule on
# persons, which no check holds: a chapter was accepted naming a stakeholder slide's people in full.
# A PDF has no meeting and nobody spoke: told the meeting's naming rule beside its own section's "a person it names is
# written as it stands", a writer weighed the two over an author the report thanks (DEFECTS 493)
MEETING_NAMING_RULE = ("Name a person by their role (\"the commissioning manager\", \"a developer of the delivery "
                       "team\"), never by their full name, even when a slide lists it; a first name only for someone who "
                       "speaks in the meeting.")
PDF_NAMING_RULE = "Write a person the document names as it names them: it has no meeting, and nobody in it spoke."
PDF_SPEECH_KIND = "page text"
CHAPTER_HUMAN_MESSAGE = PromptTemplate.from_template("""\
The document's title: {document_title}
This chapter: {chapter_title} (chapter {chapter} of {chapter_count}){part_line}

Write every heading listed below, in this order, one section each. Under each heading are its entries: each line is
an id, its kind, and its text.

{headings}

{previous_findings}
Return `markdown`: {return_scope} as one text. For each of the {section_count} headings above, in this order, write the
heading's own line exactly as listed (`### Title (T<chapter>.<heading>)`) and under it that heading's text, with no other heading of level 3
or above.
Use every id listed under a heading in the brackets of at least one block of that heading's section, and no id of
another heading; {speech_lines} marked {heard_by_frame_mark} or {brief_reply_mark} may be cited and need not be. Where a frame carries what the
text cannot, `look` at the frame, `cut` the part that matters, `mask` anything secret, and write it into the section
where the text needs it, `![what it shows](cut-NN "what the reader takes from it")`.
Every sentence states a fact of the subject. No sentence has the presenter, the speaker or the meeting doing something
("the presenter explains …", "the speaker opens …"): write what they said as the fact itself, and put a moment that
teaches nothing on the `> **Set aside:**` line. Never use these words: {banned_phrases}. {naming_rule} A `> **Set aside:**` line reads `<why> [ids]`: the reason
first, the ids in brackets at its end. Every paragraph ends with its own ids in brackets, also right before a diagram
or a figure: ids written once after several blocks belong to the last of them only.{needed_frames}
Answer through the function you were given, once.""")

# The summary is asked with no number: told its total (50 or 67 words) the writer counted every draft word by word
# (DEFECTS.md 64), and told "at most 41 sentences of at most 12 words" it counted each sentence's words instead
# (the Wikipedia runs, 22 minutes); the program counts, and the check reports a summary over its limit with the count
SUMMARY_HUMAN_MESSAGE = PromptTemplate.from_template("""\
The document's title: {title}

{parts}What each section of the document says first. Each line is its chapter, its heading, its first paragraph,
and the ids it rests on; under it stand the notes those ids name. The paragraph is a rewording: state each fact as
its notes state it, and add no reason, result or degree ("successfully", "entirely") the notes do not give.

{sections}

The decisions the sections state, with the ids they rest on:
{decisions}

The action items the sections state, with the ids they rest on:
{action_items}

What was left open:
{open_points}

{previous_findings}
Write the executive summary, citing only ids named above, and {agenda_rule}: its conclusion first, then each main point
the sections hold, one short sentence each, and nothing more. The program counts its words, never their ids, and a
summary over its limit comes back with the count, so never count words or sentences in your thinking. Every
count, date and order of steps in it is the one a section above states, never one you count or order yourself: a
summary that compressed the meeting said "three services" where the slide split the feature into two. Answer
through the function you were given, once.""")

EARLIER_PARTS = PromptTemplate.from_template("""\
What the document already says about this topic, from earlier in the video. Continue it: never say
again what it says, and when your entries extend one of its diagrams, return that diagram extended,
every line of it kept:

{text}

""")

RETRY_NOTE = PromptTemplate.from_template("""\
YOUR PREVIOUS ANSWER WAS REJECTED. It was discarded, so answer in full again. What was wrong with it:
{findings}
""")


def _render_previous_findings(request: AttemptRequest) -> str:
    if not request.findings:
        return ""
    findings = "\n".join(f"  - {finding.get('code')}: {finding.get('detail', '')} {finding.get('fix', '')}".rstrip()
                         for finding in request.findings)

    return RETRY_NOTE.format(findings=findings)


def _render_contradictions(contradictions: list[dict]) -> str:
    if not contradictions:
        return ""
    lines = "\n".join(f"  - {contradiction['text']}" for contradiction in contradictions)

    return f"The frames disagreed with each other on these points. Treat each as not established:\n{lines}\n"


def _write_inner_fences_as_tildes(text: str) -> str:
    """`text` with each fence line inside its outer ```markdown fence written with tildes: a markdown file shown on screen
    held a code block, its own ``` closed the outer fence early, and the writer could not write the note as a block
    (DEFECTS 598). A ``` block is never closed by ~~~, so the file stays one block. Any other text is returned as is."""

    lines = text.strip().split("\n")
    if len(lines) < 3 or lines[0].strip() not in ("```markdown", "```md") or lines[-1].strip() != "```":
        return text

    return "\n".join([lines[0], *(("~~~" + line.strip()[3:]) if line.strip().startswith("```") else line
                                  for line in lines[1:-1]), lines[-1]])


def _render_entry_line(entry: dict,
                       text: str,
                       marks: str = "",
                       is_pdf: bool = False) -> str:
    """One entry as every prompt lists it: its id, its kind in brackets, `marks` (each with a space before it) and its
    text. A PDF's speech entry is its pages' own text, so its kind reads `page text`: listed as `[speech]`, a writer
    called a table's lead-in "a speech line" (DEFECTS 493)."""

    kind = PDF_SPEECH_KIND if is_pdf and entry["kind"] == "speech" else entry["kind"]
    # A markdown note of several paragraphs is shown in a fence, the shape it is written in: shown bare, a terminal's
    # summary was copied as loose paragraphs, each refused as naming no id, eight checks running (DEFECTS 596). A fenced
    # block's ids after its fence cover every paragraph in it. A note of a list and then a code block counts too, its
    # code fence written with tildes inside: shown bare, a writer fenced it as markdown with the ``` code fence inside,
    # which closed the outer fence early, and the slide's code was lost in the edit that followed (DEFECTS 650)
    if entry["kind"] == "markdown" and "\n\n" in text.strip() and not text.strip().startswith("```"):
        text = "\n" + _write_inner_fences_as_tildes(f"```markdown\n{text.strip()}\n```")
    elif entry["kind"] == "markdown":
        text = _write_inner_fences_as_tildes(text)

    return f"{entry['id']} [{kind}]{marks} {text}"


def _render_speech_marks(entry: dict) -> str:
    """The marks a speech entry carries in a chapter request, each with a space before it; none for any other kind."""

    if entry["kind"] != "speech":
        return ""

    return ((f" {_HEARD_BY_FRAME_MARK}" if entry.get("heardByFrame") else "")
            + (f" {_BRIEF_REPLY_MARK}" if entry.get("isBriefReply") else ""))


def _render_table_rows_mark(entry: dict) -> str:
    """" (a table of N rows)" for an entry holding markdown tables, N their rows below the headers; "" for any other: a
    writer counted a table of 80 incidents as "Seventy-two incidents" (pdf-02, markdown mode, DEFECTS 139)."""

    rows = section_markdown_utils.count_table_rows(str(entry.get("text") or ""))

    return f" (a table of {rows} rows: a count of them is {rows}, never one you count)" if rows else ""


def _render_outline_entries(entries: list[dict]) -> str:
    """One line per entry; a diagram's source on one line, cut short."""

    lines = []
    for entry in entries:
        text = entry["text"]
        if entry["kind"] == "diagram":
            flat = " ".join(text.split())
            text = flat if len(flat) <= _OUTLINE_DIAGRAM_CHARS else flat[:_OUTLINE_DIAGRAM_CHARS] + " ..."
        lines.append(_render_entry_line(entry, text))

    return "\n".join(lines)


def _render_earlier_topics(titles: list[str]) -> str:
    if not titles:
        return ""

    return EARLIER_TOPICS.format(titles="\n".join(f"  - {title}" for title in titles))


def _render_earlier_parts(text: str) -> str:
    if not text.strip():
        return ""

    return EARLIER_PARTS.format(text=text.strip())


def _render_clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)

    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def render_moments(moments: list[dict]) -> str:
    """One line per moment: `M3 [0:20] slide "Tokens" | happens: ... | established: ... | said: ...`."""

    lines = []
    for moment in moments:
        screen = f"{moment.get('screenKind') or 'screen'} \"{moment.get('title', '')}\"" if moment.get("title") else (
            moment.get("screenKind") or "no frame")
        happens = f" | happens: {moment['happens']}" if moment.get("happens") else ""
        lines.append(f"M{moment['number']} [{_render_clock(float(moment['start']))}] {screen}{happens}"
                     f" | established: {moment.get('facts') or '-'} | said: {moment.get('speech') or '-'}")

    return "\n".join(lines)


def _sort_batches(batches: list[dict]) -> list[dict]:
    return sorted(batches, key=lambda batch: float(batch["start"]))


def render_batches_for_agenda(batches: list[dict],
                              is_pdf: bool) -> str:
    """R15: phase 3's account of each batch, in time order, with no time or frame range: the agenda is cut where the
    presenter turns, and a batch's range is only where phase 3 happened to batch its frames (a run once cut
    at every one). A PDF's batches are its figures read together. Empty when there is none."""

    if not batches:
        return ""
    accounts = "\n".join(f"- {batch.get('summary', '')}" for batch in _sort_batches(batches))
    if is_pdf:
        return ("\n\nWhat the document's figures show, in the document's order, one account per batch of figures "
                "phase 3 read together. A batch is never an agenda point: cut points where the document turns to a "
                "new subject, read from the moments:\n" + accounts)

    return ("\n\nHow the meeting went, in the order it happened, one account per batch of frames phase 3 read "
            "together. A batch is never an agenda point: cut points where the presenter turns to a new purpose, "
            "read from the moments:\n" + accounts)


def render_batches(batches: list[dict],
                   is_pdf: bool) -> str:
    """R15: phase 3's account of each batch, in time order with its time and frame range, as a block after the
    moments; a PDF's batches are its figures read together. Empty when there is none."""

    if not batches:
        return ""
    lines = []
    for batch in _sort_batches(batches):
        frames = batch.get("frames") or []
        covered = (f", frame {frames[0]}" if len(frames) == 1 else f", frames {frames[0]} to {frames[-1]}") if frames else ""
        lines.append(f"- {_render_clock(float(batch['start']))} to {_render_clock(float(batch['end']))}{covered}: "
                     f"{batch.get('summary', '')}")

    if is_pdf:
        return ("\n\nWhat the document's figures show, batch by batch, in the document's order. Each account tells "
                "what its figures show; it is a map beside the moments, not a source to cite:\n" + "\n".join(lines))

    return ("\n\nHow the meeting went, batch by batch, in the order it happened. Each account tells what the "
            "presenter set out to show and where it ended; it is a map beside the moments, not a source to cite:\n"
            + "\n".join(lines))


def _render_part_list(parts: list[dict]) -> str:
    """The parts, numbered, each with the announced items it covered; empty when the parts are not known."""

    if not parts:
        return ""
    lines = "\n".join(f"{part['number']}. {part['title']}"
                      + (f" (covers the announced: {'; '.join(part['coversAnnounced'])})" if part.get("coversAnnounced") else "")
                      for part in parts)

    return f"The points of the meeting's agenda, in order:\n{lines}\n\n"


def _render_claims(claims: list[dict]) -> str:
    return "\n".join(f"  - {claim.get('text', '')} ({', '.join(claim.get('entry_ids', []))})" for claim in claims) or "  none"


def render_entries(entries: list[dict]) -> str:
    """One line per entry: its id, its kind, its text."""

    return "\n".join(_render_entry_line(entry, entry["text"]) for entry in entries)


def _build_message_pair(phase: str,
                        human_text: str) -> list[BaseMessage]:
    """The system message the phase's instructions make, and the human message of `human_text`."""

    return [SystemMessage(content=system_prompt_builder.build_system_prompt(phase)),
            HumanMessage(content=[{"type": "text", "text": human_text}])]


def _render_screen_text(inputs: dict,
                        which: str) -> str:
    """The frames' `opening` or `recurring` texts, each quoted, or "none"."""

    texts = (inputs.get("screenText") or {}).get(which) or []

    return "; ".join(f'"{text}"' for text in texts) or "none"


def _render_document_headings(headings: list[dict]) -> str:
    """A PDF's own headings for the agenda call, each with the moment (its page) it stands on; empty for a recording.
    Handed only the pages' moments, the worker made one agenda point of a whole guide with seven headed sections
    (pdf-01); told to make each heading a point, it gave three headings of one page three points, which the check
    refuses, since each point starts at a later moment (pdf-02)."""

    if not headings:
        return ""
    top = min(int(heading["level"]) for heading in headings)
    # A document whose every heading has one level (a deck, a PDF whose reader wrote one level) opens with its title: told each heading
    # is a point, a worker made the report's title its first agenda point (pdf-02, markdown mode)
    is_one_level = len(headings) > 1 and all(int(heading["level"]) == top for heading in headings)
    lines = "".join(f"\n{'  ' * (int(heading['level']) - top)}- moment {heading['moment']}: {heading['title']}"
                    f"{' (the document title, no point)' if is_one_level and index == 0 else ''}"
                    for index, heading in enumerate(headings))

    return ("\n\nThis recording is a PDF document, and these are its own headings, in order, each with the moment it "
            "stands on:" + lines + "\nMake each heading of the second level from the top an agenda point "
            "starting at its moment, titled as the document titles it; the top heading is the document's title, and what "
            "stands under it before the first such heading (the document's lead) opens the first point, never a point "
            "of its own. A document with one level of heading takes each heading as a point. A point starts at a "
            "moment of its own: headings of that level standing on the same moment are one point, titled with "
            "what they share or with their titles joined (\"Week 1 to Week 3\"), and its chapter keeps them as its "
            "headings.")


def build_parts_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message and the human message of the parts call."""

    moments = request.inputs.get("moments") or []
    human_text = PARTS_HUMAN_MESSAGE.format(moment_count=len(moments), moments=render_moments(moments),
                                            batches=render_batches_for_agenda(request.inputs.get("batches") or [],
                                                                              bool(request.inputs.get("isPdf"))),
                                            document_headings=_render_document_headings(
                                                request.inputs.get("documentHeadings") or []),
                                            recording_name=request.inputs.get("recordingName") or "not known",
                                            transcript_speakers=", ".join(request.inputs.get("transcriptSpeakers") or [])
                                            or "none",
                                            opening_text=_render_screen_text(request.inputs, "opening"),
                                            recurring_text=_render_screen_text(request.inputs, "recurring"),
                                            previous_findings=_render_previous_findings(request))

    return _build_message_pair(PARTS_PHASE, human_text)


def build_outline_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message and the human message of the outline call."""

    entries = request.inputs.get("entries") or []
    part_title = str(request.inputs.get("partTitle") or "").strip()
    part_line = f"This chunk belongs to the agenda point titled: {part_title}\n\n" if part_title else ""
    human_text = OUTLINE_HUMAN_MESSAGE.format(part_line=part_line, entry_count=len(entries),
                                              entries=_render_outline_entries(entries),
                                              earlier_topics=_render_earlier_topics(
                                                  request.inputs.get("earlierTopics") or []),
                                              contradictions=_render_contradictions(
                                                  request.inputs.get("contradictions") or []),
                                              previous_findings=_render_previous_findings(request))

    return _build_message_pair(OUTLINE_PHASE, human_text)


def _render_points(points: list[dict]) -> str:
    return "\n".join(f"{point['number']}. {point['title']} (from {point.get('firstEntryId') or 'the start'})"
                     for point in points) or "(no agenda)"


def _render_draft_headings(drafts: list[dict]) -> str:
    """Each chapter's draft headings: its purpose, then each heading with where it starts and what it establishes."""

    blocks = []
    for draft in drafts:
        headings = "\n".join(f"  - {heading.get('title', '')} (from {heading.get('first_entry_id', '')}): "
                             f"{heading.get('what', '')}" for heading in draft.get("headings") or [])
        blocks.append(f"Chapter {draft['chapter']}: {draft.get('purpose', '')}\n{headings}")

    return "\n\n".join(blocks) or "(no chapter was read)"


def build_draft_headings_text(request: AttemptRequest) -> str:
    """The follow-up that reads one chapter in the notes file, whole, and drafts its headings."""

    return DRAFT_HEADINGS_HUMAN_MESSAGE.format(chapter=request.inputs["chapter"], chapter_count=request.inputs["chapterCount"],
                                              title=request.inputs["title"],
                                              start_ids=_render_start_ids(request.inputs.get("startIds") or []),
                                              document_headings=_render_chapter_headings(request.inputs),
                                              previous_findings=_render_previous_findings(request))


def _render_start_ids(ids: list[str]) -> str:
    """The ids a chapter's headings may start at, as runs: each frame's notes as one run, the speech lines as runs of
    consecutive lines. Told only the ids' forms, a writer joined a frame's number to the numbers of lines said over it
    ("F17.91") and, on another run, wrote a line without its zeros ("S999"); both blocked the run."""

    if not ids:
        return ""
    runs: list[list[str]] = []
    for entry_id in ids:
        previous = runs[-1][-1] if runs else ""
        if previous and _is_next_id(previous, entry_id):
            runs[-1].append(entry_id)
        else:
            runs.append([entry_id])
    listed = ", ".join(run[0] if len(run) == 1 else f"{run[0]} to {run[-1]}" for run in runs)

    return f"The ids this chapter's notes print, the only ones a heading may start at: {listed}. "


def _is_next_id(previous: str,
                entry_id: str) -> bool:
    """Whether `entry_id` follows `previous` in a run: the next note of the same frame, or the next line of speech."""

    if previous.startswith("S") and entry_id.startswith("S"):
        return int(entry_id[1:]) == int(previous[1:]) + 1
    if previous.startswith("F") and entry_id.startswith("F"):
        frame, note = previous[1:].split(".")
        next_frame, next_note = entry_id[1:].split(".")
        return frame == next_frame and int(next_note) == int(note) + 1

    return False


def _render_heading_count(minutes: float | None) -> str:
    """How many headings a recording's chapter of `minutes` calls for, one for every three to five minutes of talk; empty
    when its length is not known. Said only in the table of contents' request, which the program no longer sends, it
    reached no writer, and a chapter of a minute and a half got three headings."""

    if minutes is None:
        return ""
    if minutes < 3:
        return (f"This chapter runs {minutes:.1f} minutes of talk: one heading, unless it plainly holds two subjects a "
                "reader would look up apart. ")
    low = max(1, round(minutes / 5))
    high = max(low + 1, round(minutes / 3))

    return (f"This chapter runs {round(minutes)} minutes of talk, so about {low} to {high} headings: one for every three "
            "to five minutes, never one per frame or per fact. ")


def _render_chapter_headings(inputs: dict) -> str:
    """A PDF chapter's own headings for its draft headings follow-up; for a recording, how many headings its minutes
    call for; for a PDF chapter holding none, one heading under its own title."""

    if inputs.get("documentHeadings") is None:
        return _render_heading_count(inputs.get("chapterMinutes"))
    titles = inputs["documentHeadings"]
    if not titles:
        # A one-word chapter title against the instructions' "two to eight words" cost a writer 6,866 characters of
        # thinking on which rule wins (DEFECTS 510): the document's own title wins. The first note is named by its id:
        # told "its first note" of a chapter opening on the document's lead, a writer thought 44,527 characters over
        # whether the lead or the chapter's own heading was meant (DEFECTS 520)
        first = next(iter(inputs.get("startIds") or []), "")
        start = f"its first note, {first}" if first else "its first note"
        return ("The document has no heading inside this chapter: answer one heading, titled as the chapter is, whatever "
                f"its length, starting at {start}. ")
    listed = "; ".join(f'"{title}"' for title in titles)

    return (f"The document's own headings inside this chapter, in order: {listed}. Make each of them one draft heading, "
            "titled as the document titles it whatever its length and starting at the first note of its section, and "
            "add none of your own, "
            "none titled as the chapter either: the program moves the first of them back to the chapter's first note, so "
            "the chapter's text before its first heading is already in it. ")


def build_table_of_contents_text(request: AttemptRequest) -> str:
    """R12: the whole table of contents' follow-up: every entry in order, the agenda's points and the draft headings of
    each chapter.
    The conversation's system message carries the rules, so this is the data and the ask only."""

    entries = request.inputs.get("entries") or []
    points = request.inputs.get("points") or []
    minutes = max(1, round(max((float(entry["timestamps"][-1]) for entry in entries if entry.get("timestamps")),
                               default=0.0) / 60))

    return TABLE_OF_CONTENTS_HUMAN_MESSAGE.format(entry_count=len(entries), entries=_render_outline_entries(entries),
                                                  points=_render_points(points), point_count=len(points),
                                                  draft_headings=_render_draft_headings(request.inputs.get("draftHeadings") or []),
                                                  minutes=minutes, low=max(1, minutes // 5),
                                                  high=max(2, math.ceil(minutes / 3)),
                                                  contradictions=_render_contradictions(
                                                      request.inputs.get("contradictions") or []),
                                                  previous_findings=_render_previous_findings(request))


def build_contents_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message and the human message of the contents call."""

    topics = request.inputs.get("topics") or []
    lines = "\n".join(f"{topic['number']}. {topic['title']} ({topic['entryCount']} entries), for example: "
                      f"{topic['example']}" for topic in topics)
    human_text = CONTENTS_HUMAN_MESSAGE.format(parts=_render_part_list(request.inputs.get("parts") or []),
                                               topic_count=len(topics), topics=lines,
                                               previous_findings=_render_previous_findings(request))

    return _build_message_pair(CONTENTS_PHASE, human_text)


def build_section_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message and the human message of one section's call."""

    entries = request.inputs.get("entries") or []
    human_text = SECTION_HUMAN_MESSAGE.format(document_title=request.inputs["documentTitle"],
                                              chapter_title=request.inputs.get("chapterTitle", "") or "none",
                                              topic_title=request.inputs["topicTitle"],
                                              other_topics="; ".join(request.inputs.get("otherTopics") or []) or "none",
                                              earlier_parts=_render_earlier_parts(request.inputs.get("earlierParts", "")),
                                              entry_count=len(entries), entries=render_entries(entries),
                                              previous_findings=_render_previous_findings(request))

    return _build_message_pair(SECTION_PHASE, human_text)


def _list_misheard_words(entries: list[dict],
                         spellings: dict[str, str]) -> list[str]:
    """`F15.1 ('Lidl' for Legal)` for each note holding a word the transcript writes for a known term."""

    patterns = {word: re.compile(rf"(?<![\w-]){re.escape(word)}(?![\w-])") for word in spellings}
    found = []
    for entry in entries:
        text = str(entry.get("text") or "")
        words = [word for word, pattern in patterns.items() if pattern.search(text)]
        if words:
            found.append(f"{entry['id']} ({', '.join(f'{word!r} for {spellings[word]}' for word in words)})")

    return found


_CONTINUED_SLUG = re.compile(r"-(\d+)$")


def _render_part_line(inputs: dict) -> str:
    """Which part of the chapter a request is; a heading an earlier request began is said to continue only when one
    does: a piece past 1 whose earlier piece an earlier request wrote (told so in a part that began only new
    headings, a worker stopped twice to find the heading that continued). Both pieces of a short return folded into one
    heading can sit in one request, and that heading begins there."""

    if int(inputs.get("partCount") or 1) <= 1:
        return ""
    written = set(inputs.get("earlierSlugs") or [])
    continues = any(int(match.group(1)) > 1
                    and _CONTINUED_SLUG.sub(f"-{int(match.group(1)) - 1}", str(section["slug"])) in written
                    for section in inputs.get("sections") or []
                    for match in [_CONTINUED_SLUG.search(str(section.get("slug") or ""))] if match)

    # The request's own headings are counted and all asked for: told "part 1 of 3" over three headings, a worker read the
    # part as one heading and wrote two of three
    count = len(inputs.get("sections") or [])
    headings = f"{count} heading" + ("s" if count != 1 else "")
    continuation = (" A heading an earlier part began continues here: write it with only the notes listed under it "
                    "below, carrying on from where that part stopped, and never repeat what it wrote" if continues else "")

    return (f"\nThis chapter is asked in {inputs['partCount']} requests, and this is request {inputs['part']} of "
            f"{inputs['partCount']} for this chapter: it holds {headings}, listed below, and you write all of "
            f"{'them' if count != 1 else 'it'}. The chapter's other headings are asked in the other requests."
            + continuation)




# A line that makes a block more than prose: a list item, a table row or a fence, or what phase 4 reads as code
def build_chapter_text(request: AttemptRequest) -> str:
    """The follow-up that writes one whole chapter: every heading with its entries, and what each heading's earlier
    parts in another chapter already say."""

    blocks = []
    is_pdf = bool(request.inputs.get("isPdf"))
    # A frame suggested under several headings is placed once: named in full under the first, and under each later one
    # as already listed (a worker read three listings of one cost chart as three figures to place)
    listed: set[str] = set()
    spellings = system_prompt_builder.read_transcript_spellings()
    # The code, tables and lists earlier chapters and parts already wrote count as written too: compared only within
    # the request, a slide's code an earlier chapter held was asked for as it stands and refused as said earlier, and
    # a writer went round between the two
    written_blocks = [section_markdown_utils.list_block_lines(str(text)) for text in request.inputs.get("earlierBlocks") or []]
    # A note saying again what an earlier note of the chapter says, named beside it: written from both, one point stood
    # twice in one chapter, under one heading or two (DEFECTS 302)
    restating = document_text_utils.list_restating_entries(
        [entry for section in request.inputs.get("sections") or [] for entry in section.get("entries") or []])
    for section in request.inputs.get("sections") or []:
        section_entries = section.get("entries") or []
        entry_lines = "\n".join(_render_entry_line(entry, entry["text"],
                                                    marks=_render_speech_marks(entry) + _render_table_rows_mark(entry),
                                                    is_pdf=is_pdf)
                                for entry in section_entries)
        shown = [suggestion for suggestion in section.get("suggestions") or [] if suggestion.split(" ", 1)[0] not in listed]
        repeated = sorted({suggestion.split(" ", 1)[0] for suggestion in section.get("suggestions") or []} & listed)
        listed.update(suggestion.split(" ", 1)[0] for suggestion in section.get("suggestions") or [])
        # Each reason is phase 3's judgement of why the frame helps, and may guess: "the values the CLI reads from the
        # environment" was written into a chapter as a fact the page never states (DEFECTS 514)
        suggested = (("\nScreenshots phase 3 suggested for this heading, each with its reason for placing it (a judgement "
                      "of why it helps, never a fact to write): " + "; ".join(shown) if shown else "")
                     + ("\nAlso about this heading, listed under an earlier heading of this chapter: " + ", ".join(repeated)
                        + ". Place each such frame once in the chapter; one placement serves every heading"
                        if repeated else ""))
        cut_lines = ("\nCuts already made from these frames, to place by their id instead of cutting the frame again: "
                + "; ".join(section["existingCuts"]) if section.get("existingCuts") else "")
        cut_lines += ("\nShown already: " + "; ".join(section["placedMatches"])
                 + ". Call `leave_out` for the later frame with the reason that a placed frame shows it, unless it shows "
                 "more" if section.get("placedMatches") else "")
        # The web addresses, which a worker reads as metadata and leaves out, named once more (asked why, a worker said
        # web addresses read as metadata)
        url_ids = [f"{entry['id']} ({entry['kind']})" for entry in section_entries
                if entry["kind"] == "url" and document_plan_utils.is_kept_note(entry)]
        # A web address is written once in the whole document: a chapter's parts wrote the same "Open in browser:"
        # line again and again, refused each time as said earlier
        url_line = ("\nNotes easily missed, each to be cited in the brackets of the block it belongs to: " + ", ".join(url_ids)
                  + ("; a web address is written as a link once in the whole document: when an earlier section or part "
                     "already holds it, cite its note's id in the brackets of the block about that page, never on the "
                     "set-aside line, with no line for the address") if url_ids else "")
        # A note in the recording's own words ("shown on screen") is copied into the text and blocks it; the notes and their words are named here
        banned_wording_notes = [f"{entry['id']} ({', '.join(repr(phrase) for phrase in phrases)})" for entry in section_entries
                  for phrases in [document_text_utils.find_banned_phrases(str(entry.get("text") or ""))] if phrases]
        banned_wording_line = ("\nNotes whose wording your text may not copy: state what they establish without those "
                               "words: " + ", ".join(banned_wording_notes) if banned_wording_notes else "")
        # The questions a heading holds, named again: three chapters running were refused for open-question notes cited
        # nowhere, though the instruction says never to set one aside
        # One line per kind, each with its one action: told "an Open line, or cited with the block that answers it" for
        # both, a worker twice dropped the id of a question asked and answered whose reply another note also states
        # An open note that only says a step is not shown yet is about the recording: listed as an Open line here and
        # refused as narration by the check, a worker reworded one into a claim the section contradicted
        not_yet = [entry["id"] for entry in section_entries if entry["kind"] == "open_question"
                   and document_text_utils.is_not_yet_shown(str(entry.get("text") or ""))]
        still_open = [entry["id"] for entry in section_entries if entry["kind"] == "open_question"
                      and entry["id"] not in not_yet]
        asked = [entry["id"] for entry in section_entries if entry["kind"] == "question"]
        questions = (("\nOpen questions under this heading, each an `> **Open:**` line where it comes up: "
                      + ", ".join(still_open) if still_open else "")
                     + ("\nOpen notes that only say a step is not shown yet, about the recording and not the subject: "
                        "put each on the section's `> **Set aside:**` line, never as an `> **Open:**` line and never "
                        "reworded: " + ", ".join(not_yet) if not_yet else "")
                     + ("\nQuestions asked in the meeting under this heading, each written as the question and the answer "
                        "given (or that none was given), \"Asked whether <question>, the team answered that <answer>\", "
                        "never with the presenter or the speaker as the one acting, and cited in that block's brackets, "
                        "even when another note states the same answer: " + ", ".join(asked) if asked else ""))
        # A note worded as the meeting's own course ("The presentation moves to the second use case") was retold as a
        # sentence and refused as narration, twice for one chapter: the notes worded so are named
        # A question is named once, among the questions: listed also here, a worker was told both to write it as an
        # `> **Open:**` line and to set it aside
        # Nor a kind never set aside: a web address worded as what the slide showed was listed here too, beside the
        # line saying it is never set aside (froscon talk 3143, DEFECTS 265)
        retold = [entry["id"] for entry in section_entries
                  if entry["kind"] != "open_question" and not document_plan_utils.is_kept_note(entry)
                  and document_text_utils.find_narrated_sentences(str(entry.get("text") or ""))]
        # One instruction for these notes, the same as write.md's set-aside rule: told only "cite it in brackets" here
        # and "set it aside" there, a worker spent 42,896 characters choosing between them
        narrating = ("\nNotes worded as what happened in the meeting, never retold as a sentence: cite each in the brackets "
                     "of the block whose fact it supports, or, when it says nothing about the subject (who presents, a "
                     "window switched), put it on the section's `> **Set aside:** <why> [ids]` line: " + ", ".join(retold)
                     if retold else "")
        # A chart a note drew before the check refused one (a gantt of a timeline slide, its days invented) is named,
        # so it is not drawn into the text beside the frame that shows it
        charts = [entry["id"] for entry in section_entries if entry["kind"] == "diagram"
                  and str(entry.get("text") or "").split()[:1] and str(entry["text"]).split()[0]
                  in mermaid_text_utils.CHART_KINDS]
        diagram_and_markdown_lines = ("\nDiagram notes that draw a chart, never drawn in the text: cite each id in the brackets of the block that "
                   "states the same values, and let the frame's cut show the chart: " + ", ".join(charts)
                   if charts else "")
        # A diagram past the trusted arrow count is never drawn, beside its slide either: a 13-arrow one about half wrong
        # was drawn above the slide's cut, since the rule stopped it only as a replacement
        dense = [f"{entry['id']} ({mermaid_text_utils.count_arrows(str(entry.get('text') or ''))} arrows)"
                 for entry in section_entries if entry["kind"] == "diagram"
                 and entry["id"] not in charts
                 and mermaid_text_utils.count_arrows(str(entry.get("text") or "")) > mermaid_text_utils.MOST_TRUSTED_ARROWS]
        diagram_and_markdown_lines += ("\nDiagram notes with more arrows than a slide's lines can be traced for, never drawn in the text: "
                    "state in words the links the notes' facts give, cite each id in the brackets of that block, and let "
                    "the frame's cut show the diagram: " + ", ".join(dense) if dense else "")
        # The screen's code, table or list phase 3 wrote as markdown goes into the text as that block: a document
        # placed frames of code, cut short at their edges
        markdown_ids, twin_ids, prose_ids = section_markdown_utils.split_twin_blocks(section_entries, written_blocks)
        # A markdown note is named only when it holds a slide's sentences, stated in the writer's own words: a screen's
        # code, table or list is written as its block whatever a fact says of it (DEFECTS 637)
        restated = [f"{entry['id']} (as {restating[entry['id']]})" for entry in section_entries if entry["id"] in restating
                    and (entry.get("kind") != "markdown" or entry["id"] in prose_ids)]
        restated_line = ("\nNotes that may say again what an earlier note of this chapter says: " + ", ".join(restated)
                         + "; where it is the same point, write it once and cite both ids in that block's brackets"
                         if restated else "")
        diagram_and_markdown_lines += ("\nNotes holding a screen's code, table or list as markdown: write each block into the text as it "
                    "stands, its ids after it, and never cut its frame for that part: " + ", ".join(markdown_ids)
                    if markdown_ids else "")
        # The same slide shown again holds the same block: written a second time it was refused as said earlier, while
        # this line asked for it, and a writer went round six times between the two
        # A slide's sentences or its title were written as markdown in phase 3 and copied here as a code box of prose
        # (five in one document once); they are stated, never boxed
        # ... and a slide that only names the talk, its speaker or its event teaches nothing to state: asked to state
        # a title slide's notes, a writer narrated the talk three times and was refused for it each time (froscon 3211
        # chapter 1, DEFECTS 291)
        diagram_and_markdown_lines += ("\nNotes holding a slide's sentences or title, no code, table or list: state what they say in "
                    "your sentences and cite their ids, never as a block or a heading; a slide that only names the talk, "
                    "its speaker or its event teaches nothing, and its ids go on the `> **Set aside:**` line: "
                    + ", ".join(prose_ids) if prose_ids else "")
        # A block holding a person's e-mail, asked for as it stands, was refused for the address (DOCUMENT_PERSONAL_IN_TEXT):
        # a talk's closing slide gave the speaker's (38c3 talk 797 chapter 9, DEFECTS 549)
        addressed = [entry["id"] for entry in section_entries if entry["id"] in {*markdown_ids, *prose_ids}
                     and document_text_utils.PERSONAL_EMAIL.search(str(entry.get("text") or ""))]
        diagram_and_markdown_lines += ("\nOf those, blocks holding a person's e-mail address: write them without the "
                                       "address: " + ", ".join(addressed) if addressed else "")
        diagram_and_markdown_lines += ("\nNotes repeating a block an earlier heading already writes: never write it again; its id may go in "
                    "the brackets of the block this heading writes about it, and need not: " + ", ".join(twin_ids) if twin_ids else "")
        misheard = _list_misheard_words(section_entries, spellings)
        terms = ("\nNotes holding a word the transcript writes for a known term: " + "; ".join(misheard)
                 + ". Write the term where the context supports it, never the misheard word" if misheard else "")
        # A name heard once and written as heard, where a screen of the recording spells it (DEFECTS 620, 647)
        screen_misheard = _list_misheard_words(section_entries, request.inputs.get("screenSpellings") or {})
        terms += ("\nNotes holding a word the speech may have misheard for a name a screen of the recording spells: "
                  + "; ".join(screen_misheard) + ". Where the note means that name, write the screen's spelling"
                  if screen_misheard else "")
        # Headings the draft started inside this heading's paragraph, folded into it: each subject keeps its place
        subjects = ("This heading also covers these subjects; write each under a `####` subheading of its own: "
                    + "; ".join(section["subjects"]) + "\n" if section.get("subjects") else "")
        blocks.append(f"### {section['heading']} ({section['topicId']})\n{subjects}{entry_lines}{url_line}{restated_line}{questions}{banned_wording_line}"
                      f"{narrating}{terms}{diagram_and_markdown_lines}{suggested}{cut_lines}")
    speech_lines = f"{PDF_SPEECH_KIND} lines" if is_pdf else "speech lines"
    naming_rule = PDF_NAMING_RULE if is_pdf else MEETING_NAMING_RULE
    # A part is asked for its own headings: told "the whole chapter" beside the part line's "only the notes listed", a
    # writer spent its thinking on which one holds (DEFECTS 495)
    return_scope = "this request's headings" if int(request.inputs.get("partCount") or 1) > 1 else "the whole chapter"

    return CHAPTER_HUMAN_MESSAGE.format(document_title=request.inputs["documentTitle"],
                                        chapter_title=request.inputs["chapterTitle"],
                                        chapter=request.inputs["chapter"], chapter_count=request.inputs["chapterCount"],
                                        part_line=_render_part_line(request.inputs),
                                        headings="\n\n".join(blocks), section_count=len(request.inputs.get("sections") or []),
                                        previous_findings=_render_previous_findings(request),
                                        banned_phrases=output_contract.PROMPT_PLACEHOLDERS["DOCUMENT_BANNED_PHRASES"],
                                        needed_frames=_render_needed_frames(request.inputs.get("sections") or []),
                                        heard_by_frame_mark=_HEARD_BY_FRAME_MARK, brief_reply_mark=_BRIEF_REPLY_MARK,
                                        speech_lines=speech_lines, naming_rule=naming_rule, return_scope=return_scope)


def _render_needed_frames(sections: list[dict]) -> str:
    """The closing's line naming every frame of the chapter marked `needed`, or "" when none is: a needed frame left
    unplaced was the most frequent chapter rejection, 180 findings over six runs, though each heading lists its own."""

    needed = list(dict.fromkeys(frame for section in sections for frame in section.get("neededFrames") or []))
    if not needed:
        return ""

    return (f"\nBefore you answer, account for each frame this chapter marks needed, one by one: {', '.join(needed)}. "
            "Place its cut, call `replace_with_diagram`, or call `leave_out` with an allowed reason; an answer that "
            # A worker that called no tool wrote cut-21 to cut-25 into its text: 48 cut ids never made, over six runs
            "leaves one out is rejected. A cut exists only once you call `cut`: place it by the id that call returns, "
            # A worker wrote "[leave_out]: frame-037 is redundant" into its text instead of calling the tool, the third
            # planned call never made in one chapter run
            "never by an id you choose. `leave_out` and `replace_with_diagram` are tool calls made before you answer; "
            "a line in your answer saying a frame is left out counts for nothing.")


# A note's text cut at this many characters under a summary's section line: a table of 80 rows is one note
_SUMMARY_SOURCE_CHARS = 600


def _render_summary_sources(section: dict) -> str:
    """The notes a section's first paragraph cites, each indented under its line with its id: what the summary states
    is said as they say it, never as the paragraph reworded it (DEFECTS 505)."""

    sources = section.get("sources") or []
    if not sources:
        return ""

    return "".join("\n      " + f"{source['id']}: " + " ".join(source["text"].split())[:_SUMMARY_SOURCE_CHARS]
                   for source in sources)


def build_summary_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message and the human message of the summary call, written last, from the parts and the sections."""

    parts = request.inputs.get("parts") or []
    sections = "\n".join(f"  - [{section.get('chapter') or '-'}] {section.get('heading', '')}: {section.get('text', '')} "
                         f"({', '.join(section.get('entryIds', []))})" + _render_summary_sources(section)
                         for section in request.inputs.get("sections") or [])
    agenda_rule = (f"write one agenda line for each of the {len(parts)} agenda points, in order" if parts
                   else "leave the agenda empty: the meeting's agenda points are not known")
    human_text = SUMMARY_HUMAN_MESSAGE.format(title=request.inputs["title"], parts=_render_part_list(parts),
                                              sections=sections or "  none",
                                              decisions=_render_claims(request.inputs.get("decisions") or []),
                                              action_items=_render_claims(request.inputs.get("actionItems") or []),
                                              open_points="\n".join(f"  - {point}" for point in
                                                                    request.inputs.get("openPoints") or []) or "  none",
                                              previous_findings=_render_previous_findings(request),
                                              agenda_rule=agenda_rule)

    return _build_message_pair(SUMMARY_PHASE, human_text)
