"""Phase 3: the messages of ONE model call: one frame, a batch of consecutive frames, a batch's account asked alone, the
critique, the repair from it, the open questions and their answers. REQUIREMENTS.md R2.

The human message is R2's prompt; its tokens are real template variables here.

The frame travels as an image part beside the text. Nothing else can be looked up: a chain has no
tools, so everything the frame agent must know is in these two messages.

A GROUP (`framesProcessing.framesPerCall` above 1) sends each frame as its own message, its number, its time, its words and
its frame together, so no answer can drift to the wrong frame: a host that takes a message's frames apart
from its text cannot reorder them across messages.

A REPAIR call is a group of the frames an earlier group call did not finish. Its first message also says what that
call got right (the accepted notes, which the new records may build on) and why each frame failed, in the validator's
words; when the earlier call was cut at the output limit, it says so and asks for brief thinking.

The SYSTEM text is not written here. It is data, in `agent-context/instructions/frames_processing.md`, assembled
by `prompts/system_prompt_builder.py`, and it is a plain string that never passes through a
template. Only the human text below is a template.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate

import config
from models import output_contract
from models.attempt_request import AttemptRequest
from prompts import compaction_prompt_loader
from prompts import system_prompt_builder
from schemas import critique_record
from schemas import frame_record
from services import apple_text_service
from services import batch_summaries_service
from services import editor_pane_service
from services import empty_step_service
from services import outline_reread_service
from services import phase_file_service
from services import record_file_service
from services import struck_word_service
from services import text_detection_service
from utils import board_text_diff_utils, critique_markdown_utils, frame_markdown_utils, frame_message_utils
from utils import announced_words_utils, asked_questions_utils, frame_slug_utils, garbled_name_utils, mermaid_text_utils
from validators import item_validator

__all__ = ["build_frame_messages", "build_frame_batch_messages", "build_batch_summary_messages",
           "build_critique_messages", "build_second_pass_messages", "build_open_questions_messages",
           "build_answer_questions_messages", "render_critique_rules", "render_previous_frames",
           "render_open_questions_before", "render_latest_diagram", "render_previous_findings", "render_repair_note",
           "render_build_note", "render_changed_places_note", "render_garbled_names_note", "render_text_columns_note", "render_earlier_titles", "RETURN_NOTE", "SUMMARY_WRITE", "GROUP_CLOSING_MESSAGE",
           "PHASE", "TEXT_ONLY_FRAME_NOTE", "BOARD_REGION_NOTE", "ANSWER_AFTER_THINKING", "ANSWER_GO_ON", "is_account_asked"]

# The `context_map.json` key this phase's system prompt is assembled from. Not the job kind: the kind selects THIS builder.
PHASE = output_contract.FRAMES_PROCESSING_PHASE
# R19: the critique, and the second look that repairs from it, each with an instruction of its own (the second look's
# says what an addition is, and that it only adds)
CRITIQUE_PHASE = "critique"
SECOND_PASS_PHASE = "second_pass"
OPEN_QUESTIONS_PHASE = "open_questions"
ANSWER_QUESTIONS_PHASE = "answer_questions"

# What both calls say before the earlier frames' notes: a model wrote them and can have misread a frame
EARLIER_NOTES_LEAD = ("The notes written for the most recent earlier frames. A model wrote them and can have misread a "
                      "frame: a name, a text or a value in them goes into this frame's notes only when this frame shows "
                      "it, and a program's name only when this frame shows that name:")

FRAMES_PROCESSING_HUMAN_MESSAGE = PromptTemplate.from_template("""\
## What came before
{EARLIER_NOTES_LEAD}
{PREVIOUS_FRAMES}

What was said before this frame:
{TRANSCRIPT_BEFORE}

## This frame
You are looking at frame {FRAME_NUMBER}, captured at {TIMESTAMP}.

Spoken from this frame until the next one:
{TRANSCRIPT_CURRENT}

{prompt}
{previous_findings}
Answer once, in markdown, as your instructions show: one document headed "# Frame {FRAME_NUMBER} · <screen kind>". \
{SECTIONS}""", partial_variables={"EARLIER_NOTES_LEAD": EARLIER_NOTES_LEAD})

GROUP_CONTEXT_MESSAGE = PromptTemplate.from_template("""\
## What came before
{EARLIER_NOTES_LEAD}
{PREVIOUS_FRAMES}

What was said before the first of these frames:
{TRANSCRIPT_BEFORE}{REPAIR}

## These frames
The next messages are {COUNT} {ORDER}frames, in order: {NUMBERS}. Each gives the frame's number, when it was
captured, the words spoken while it was on screen, and its frame.""", partial_variables={"EARLIER_NOTES_LEAD": EARLIER_NOTES_LEAD})

REPAIR_NOTE = PromptTemplate.from_template("""

## An earlier worker did not finish these frames
An earlier worker was given frames {BATCH_NUMBERS} in one call. {WHY}
{DONE}

It did not finish frames {FAILED_NUMBERS}:
{FAILED_REASONS}

Do frames {FAILED_NUMBERS} only.{SUMMARY}""")

REPAIR_SUMMARY_REJECTED = PromptTemplate.from_template("""

Its "# Batch" account of frames {BATCH_NUMBERS} did not pass the checks either:
{REASON}
Write "# Batch" again, for the whole batch of frames {BATCH_NUMBERS}: from its accepted notes above and your
notes.""")

REPAIR_CUT = ("It spent its whole output budget thinking and was cut off before it wrote its answer, so think "
              "briefly: decide each section once, then write the notes.")
REPAIR_REJECTED = "Some of its notes did not pass the checks."

GROUP_FRAME_MESSAGE = PromptTemplate.from_template("""\
Frame {FRAME_NUMBER}, captured at {TIMESTAMP}.

Spoken from this frame until the next one:
{TRANSCRIPT_CURRENT}""")

# The closing's check of the account, from the validator's own minimum: told five while three passed, a model padded it
_SENTENCES_CHECK = (f'- "## {frame_record.BATCH_SECTIONS["batch_summary"]}" has at least {item_validator.SUMMARY_MIN_SENTENCES} sentences: count '
                    f"them before you answer, and if there are fewer, give the aim, each step, what was said and the\n  "
                    f"outcome a sentence each (fewer than {item_validator.SUMMARY_MIN_SENTENCES} refuse the whole batch);")
GROUP_CLOSING_MESSAGE = PromptTemplate.from_template("""\
{prompt}
{previous_findings}
Answer once, in markdown, as your instructions show: {ANSWER_OPENING}one document headed "# Frame N · <screen kind>" for each of the
frames {NUMBERS}, in this order. {SECTIONS} A frame's notes treat the notes before them in your answer as earlier notes: link to
them under "## Builds on", and extend a diagram one of them drew. Each frame's "## Screen" follows that frame's OWN
message: a frame whose own message says "This screen was shown before, as frame N" may be the one line
"- same as frame N", which the program fills with that frame's outline; a frame whose own message says it is a step
of a slide built up step by step outlines there only what it adds, after the line that message gives, and writes
every other section in full; every other frame outlines its whole screen, part by part: never "same as frame N",
which the program refuses, because a later step shows each frame's notes alone. A note in one frame's message never applies to the frame before or after it.
Before you answer, check every frame for the misses your notes have made before:
- a slide's or a page's title is a part named a title (`- title "<its text>": <where>`), a section slide's too:
  the program finds where each part of the meeting opens by it;
- a frame whose screen holds text and shows it for the first time states under "## Facts" what that screen teaches,
  even when the presenter only passes over it or fails to reach another screen; "{EMPTY_MARK}" is right only for a screen
  shown again with nothing new;
""" + _SENTENCES_CHECK + """
- each frame's "## Screen" is checked against its image from the top edge to the bottom edge, one part at a time:
  every line of text you pass has its quoted line, the last block at the bottom of the screen included;
- its colours are read off ITS frame: a dark slide is dark, whatever the frames before it were;
- a person is "they" or named by role or name, never "he", "she", "him" or "her";
- every statement the screen makes is its own line under "## Facts", never several joined in one;
- "## Diagram" draws only a flow, an architecture or a sequence the screen or the speech shows, and is "{EMPTY_MARK}" for a
  list of names or a picture;
- a diagram on screen is outlined box by box under "## Screen": every box a part with its label quoted, every arrow a
  part under the box it leaves, never "a flowchart showing ..." (the program reads the frame's text and refuses an
  outline that leaves labels out);
- a dense diagram, a table or a form a reader must see gets a screenshot marked "needed" under "## Screenshots";
- a web address is copied only as far as every character is plain to read, then ends with "…": never a guessed
  letter or digit;
- every text on screen is quoted whole, however long, inside double quotes: a heading AND the text under it each in
  its own quotes (`- "Heading": "the description under it"`), and the small grey caption under a button's or an
  item's label too (`- item "<label>": "<caption>"`), never shortened with "..." or "…"; a text written without
  quotes, or cut to its first words, fails the check that reads the frame's text;
- a paragraph on screen is quoted word for word, every paragraph of it, never described ("text explaining that
  ...") and never pointed back to ("similar text to above"), even when two paragraphs read alike;
- "## What happens" is two to five sentences and at most 120 words: one story of the moment, never a list of the
  screen's parts;
- a terminal, a form or any screen of text is outlined line by line: every menu entry, field label with its value and
  message quoted whole; only the rows of a data table and the lines of a log may be given as a count with the first
  and the last quoted, a log's warnings, errors and the lines the talk points at quoted too;
- each frame's notes describe ITS OWN image, never a neighbour's: when frames look alike, name the application and the
  window you see in that image (a chat app is not the web tool beside it), and outline only what that image shows;
- a term is written as the screen spells it: the speech is an automatic transcript that mishears names and acronyms,
  so a word heard but nowhere on screen goes under "## Open questions", as heard, never into a fact;
- "What was said before this frame" is context: its points belong to the earlier frames' notes, so a frame states only
  what its own screen and its own words ("Spoken from this frame until the next one") give.
{SUMMARY_ASK}""", partial_variables={"EMPTY_MARK": frame_record.EMPTY_MARK})
# What the closing asks of the "# Batch" account, stated once: written on a first call and when the earlier account failed,
# left empty on a repair whose earlier account passed.
SUMMARY_WRITE = (f"Write \"# Batch\" FIRST, before the first frame's notes, from what you found reading every frame. Under \"## {frame_record.BATCH_SECTIONS['batch_title']}\": the topic of THESE frames in about 4 to 8 words, as narrow as "
                 "they are: the\nsystem, the step, the rule or the question they show, never the meeting's subject, and "
                 "never an earlier batch's title.\n"
                 f"Under \"## {frame_record.BATCH_SECTIONS['batch_summary']}\": the whole batch these frames and their words "
                 "cover, as one\naccount in the third person, from what the presenter set out to show to where it "
                 f"ended. {item_validator.SUMMARY_MIN_SENTENCES} to {item_validator.SUMMARY_MAX_SENTENCES}\nsentences, at most "
                 f"{item_validator.SUMMARY_MAX_WORDS} words: one sentence for the aim, one for each step in order with what it "
                 "changed, one for what was said, asked\nand answered, and one for the outcome. An account of fewer "
                 f"than {item_validator.SUMMARY_MIN_SENTENCES} sentences is refused, and the whole batch with it.\n"
                 f"Under \"## {frame_record.BATCH_SECTIONS['atmosphere']}\": how this batch FEELS, in two to four sentences. The speaker's tone, how "
                 "the others\ntake part (silent, a question or two, many at once, interrupting, agreeing, pushing "
                 "back), any disagreement\nand whether it is settled, and anything else about the mood. What you "
                 'heard, never what anyone felt.\nWrite "Plain, nobody speaks but the presenter." when the batch '
                 "has nothing in it.")
# The first batch's closing ends as every later one's does, with the line naming the titles. The account comes first in
# the answer: written after the last frame, it was left out on 16 batches of 9 runs, first batches and later ones, the
# worker planning it in its thinking and stopping after the last frame, and a line asking for it there did not stop it
# (DEFECTS 149)
# A talk's first batch is its opening: titled with the talk's own name, it was refused as naming the meeting (DEFECTS 309)
FIRST_BATCH_TITLES = ("\nThe earlier batches' titles: none yet, this is the first batch. Its title names what these frames "
                      "do, never the talk's own title: a talk's opening of title slides is \"Opening: event and speaker\". "
                      "Write its \"# Batch\" before the first frame's notes.")
SUMMARY_KEEP = ("Write no \"# Batch\": the earlier call's account of this batch passed its checks.")
# The answer's shape names the account first: told the frames' documents "in this order" and only at the message's end
# to write "# Batch" first, a worker opened with the frames and left the account out in 3 of 26 batches (froscon talk
# 3147; DEFECTS 149, 320), while every answer that opened with it kept it
ANSWER_OPENING = "first the batch's account, one document headed \"# Batch\" (the last paragraph says what it holds), then "

# Sent after an answer that ended on its own with parts missing, in the same attempt: a batch of two was answered for
# its first frame only, its thinking having planned both (DEFECTS 318), and batches left their account out (320)
# DEFECTS 337: a batch's reply that stopped inside its thinking, before any answer
ANSWER_AFTER_THINKING = ("Your reply above ended inside your thinking, before any answer was written. Write the answer now: "
                         "every frame's document and the batch's account, in the shape your instructions give, from "
                         "what your thinking settled.")
ANSWER_GO_ON = ("Your answer above ended before it was whole: it lacks {MISSING}. Go on with it now: write only {MISSING}, "
                "each in the shape your instructions give, and nothing you already wrote.")
ANSWER_GO_ON_ACCOUNT = "the batch's account, one document headed \"# Batch\""
ANSWER_GO_ON_FRAMES = "the documents of frames {NUMBERS}"
ANSWER_GO_ON_FACTS = ("the documents of frames {NUMBERS} again, each whole, its \"## Facts\" stating what the screen and "
                      "the speech establish: each of these frames shows text no earlier frame showed, and your answer "
                      "gave it no fact")
# A repair answering "Nothing to change" while its critique named mistakes, asked once more in the same attempt: one
# planned in its thinking the edit a held rule asked for and answered "Nothing to change" (Lecture 7, DEFECTS 576)
REPAIR_GO_ON = ("Your answer changed nothing, though your critique named {COUNT}: {NAMES}. Check each against its "
                "frame once more. For each the frame shows, write the edit or the addition that fixes it, in your "
                "answer's usual documents; when the frames show none of them, answer \"# Nothing to change\" again.")


ANSWER_GO_ON_SCREEN = ("the documents of frames {NUMBERS} again, each whole, written from that frame's own image: the "
                       "\"## Screen\" your answer gave each quotes text its frame does not show, another frame's screen")


def is_account_asked(request: AttemptRequest) -> bool:
    """Whether a batch's call asks for its account: every call but a repair whose earlier account passed."""

    repair = request.inputs.get("repair")

    return not repair or _is_account_failed(repair)


# A frame that shows an earlier screen again is read with what that showing left: its
# notes and the words said over it then. The new notes say what this showing adds.
RETURN_NOTE = PromptTemplate.from_template("""
This screen was shown before, as frame {ORIGINAL_NUMBER} at {ORIGINAL_TIMESTAMP}. {ORIGINAL_NOTES}
Spoken while it was shown then:
"{ORIGINAL_SPEECH}"
Write this frame's notes as your instructions say for a screen shown again: its `## Screen` may be the one line
`- same as frame {ORIGINAL_NUMBER}`, which the program fills with that frame's outline, and never points to any other
frame.""")
RETURN_NOTES_KNOWN = "Its notes then:\n{NOTES}"
RETURN_NOTES_IN_BATCH = "Its notes are the ones you write for frame {ORIGINAL_NUMBER} in this answer."

# Frames that follow each other with a small change are one slide built up in steps.
BUILD_START_NOTE = ("\n\nFrames {START} to {END} show one screen built up in {COUNT} steps; this is its first step: outline its "
                    "whole screen, and state under \"## Facts\" what its text, its picture and the words said over it tell; "
                    "never leave its point for a later step to say.")
BUILD_STEP_NOTE = ("\n\nThis is step {NUMBER} of {COUNT} of the screen first shown as frame {START}. Under \"## Screen\" write "
                   "the line \"- step {NUMBER} of the screen of frame {START}: <what this step adds, removes or "
                   "replaces>\", then each part it adds or puts in place of another under it (a picture swapped for "
                   "another is such a part); under \"## Facts\" only what this step and the words over it add.")
BUILD_STEP_ALONE_NOTE = ("\n\nThis is a step of the screen first shown as frame {START}, whose notes are among the earlier "
                         "notes. Under \"## Screen\" write the line \"- a step of the screen of frame {START}: <what this step "
                         "adds, removes or replaces>\", then each part it adds or puts in place of another under it (a "
                         "picture swapped for another is such a part); under \"## Facts\" only what this step adds.")

# REQUIREMENTS.md R15: the account of a batch alone, when every frame of the batch passed and its account did not.
BATCH_SUMMARY_MESSAGE = PromptTemplate.from_template("""\
## One batch of the meeting
An earlier worker noted frames {NUMBERS}, from {START} to {END}. Every frame's notes passed the checks; its account
of the whole batch did not:
{REASON}

The accepted notes of these frames:
{NOTES}

What was said, frame by frame:
{SPEECH}
{previous_findings}
Write one document headed "# Batch". Under "## {TITLE_HEADING}": the topic of these frames in about 4 to 8 words, as narrow as they
are (the system, the step, the rule or the question they show), never the meeting's subject, and never an earlier
batch's title.{TITLES}

Under "## {SUMMARY_HEADING}": the whole batch as one account in the third person,
from what the presenter set out to show, through the steps in their order and what was said or asked around them, to
where it ended. {MIN_SENTENCES} to {MAX_SENTENCES} sentences, at most {MAX_WORDS} words: one sentence for the aim, one for
each step in its order, one for what was said or asked, and one for the outcome. An account of fewer than
{MIN_SENTENCES} sentences is refused, and the whole batch with it.

Under "## {ATMOSPHERE_HEADING}": how the batch FEELS, in two to four sentences. The speaker's tone, how the others take
part (silent, a question or two, many at once, interrupting, agreeing, pushing back), any disagreement and whether it
is settled, and anything else about the mood. Say what you heard, never what anyone is guessed to have felt. Write
"Plain, nobody speaks but the presenter." when the batch has nothing in it.

Answer once, with that one document.""",
    partial_variables={"TITLE_HEADING": frame_record.BATCH_SECTIONS["batch_title"],
                       "SUMMARY_HEADING": frame_record.BATCH_SECTIONS["batch_summary"],
                       "ATMOSPHERE_HEADING": frame_record.BATCH_SECTIONS["atmosphere"]})

# REQUIREMENTS.md R19: the batch's critique, which only reports what went wrong; the second pass below repairs from it.
CRITIQUE_MESSAGE = PromptTemplate.from_template("""\
## Criticise your own notes
These are the notes you wrote for frames {NUMBERS}, from {START} to {END}. Read them back against the frames and the
words and say what went WRONG in them, going through the numbered list of common mistakes your instructions give once.

You are not fixing anything here. A later message asks you for the fix; this one asks only what the mistakes were.

The notes as they stand:
{NOTES}

What was said before these frames, which the notes were written with:
{SPEECH_BEFORE}

What was said, frame by frame:
{SPEECH}

The account of the batch as it stands: {SUMMARY}
How the batch felt, as it stands: {ATMOSPHERE}
{previous_findings}
Open your answer with "## Checked": one line per mistake of that numbered list, its number, its name and the frames
of this batch it happened on, or "none", each written once. Then write each mistake once, as a rule that holds for ANY
meeting, as your instructions show: one "## <name>" section per anti-pattern, with its "What it is", its "How to avoid
it", and a "Seen on" line naming the frames of this batch it happened on. The rule's own lines name no frame and quote no word off these screens: this file is carried into the
instructions every later run is given. Write "# Nothing to criticise" only if these notes genuinely hold none.""")


# REQUIREMENTS.md R19: the second look every batch gets, whether or not anything failed. The tone is the point: a
# model told its notes looked thin finds what it dropped, and one told they were fine adds nothing.
SECOND_PASS_MESSAGE = PromptTemplate.from_template("""\
## Read your own notes again
These are the notes you wrote for frames {NUMBERS}, from {START} to {END}. Reading them back against the frames and
the words, they look thinner than this batch deserves: facts that were said and never written, screens outlined in
two lines where every part was needed, labels nobody quoted.

This is your last look at this batch. Nothing after it reads the frames again.

The notes as they stand:
{NOTES}

What was said before these frames, which the notes were written with:
{SPEECH_BEFORE}

What was said, frame by frame:
{SPEECH}

The account of the batch as it stands: {SUMMARY}
How the batch felt, as it stands: {ATMOSPHERE}
{CRITIQUE}{previous_findings}
Go frame by frame and say what each one is missing, in markdown, once, as your instructions show: one document headed
"# Frame N" per frame you improve, opening with "## What changed". What you write under a list section is ADDED to
the notes, so write only what is new. Leave out a frame you would not change, and write "# Nothing to change" only if
there is genuinely nothing to add anywhere.""")


# The batch's follow-up after the second pass: what a reader who was not there still cannot know.
OPEN_QUESTIONS_MESSAGE = PromptTemplate.from_template("""\
## What is still open
These are the notes you wrote for frames {NUMBERS}, from {START} to {END}, and the account you wrote of the batch.
Read them as someone who did not attend the meeting and cannot see the screen.

The notes as they stand:
{NOTES}

What was said before these frames, which the notes were written with:
{SPEECH_BEFORE}

What was said, frame by frame:
{SPEECH}

What is said right after these frames, the start of the next frame's words:
{SPEECH_AFTER}

The account of the batch: {SUMMARY}
{previous_findings}
Write the questions this batch leaves open, in markdown, once, as your instructions show: one document headed
"# Batch" with one section, "## {OPEN_QUESTIONS_HEADING}". Write "{EMPTY_MARK}" under it when nothing is left open.""",
    partial_variables={"EMPTY_MARK": frame_record.EMPTY_MARK,
                       "OPEN_QUESTIONS_HEADING": frame_record.BATCH_FOLLOW_UP_SECTIONS["open_questions"]})

# The follow-up after the open questions: the same worker looks for the answers in what it was shown.
ANSWER_QUESTIONS_MESSAGE = PromptTemplate.from_template("""\
## Answer what you can
These are the notes you wrote for frames {NUMBERS}, from {START} to {END}, and the questions the batch left open.

The notes as they stand:
{NOTES}

What was said before these frames, which the notes were written with:
{SPEECH_BEFORE}

What was said, frame by frame:
{SPEECH}

What is said right after these frames, the start of the next frame's words:
{SPEECH_AFTER}

The account of the batch: {SUMMARY}

The open questions:
{QUESTIONS}
{EARLIER}{previous_findings}
Look for each answer in the notes, the words and the frames. Write once, in markdown, as your instructions show: one
document headed "# Batch" with three sections, "## Open questions" for the ones you cannot answer, "## Answered
questions" for the ones you can, and "## Not a question" for the ones that ask nothing a reader needs, each with why.
Every question above is in exactly one of them.""")

RETRY_NOTE = PromptTemplate.from_template("""\
YOUR PREVIOUS ANSWER WAS REJECTED. It was discarded, so answer in full again.
What was wrong with it:
{findings}
""")

NO_EARLIER_FRAME = "(no earlier frame: this is the first one)"
NOTHING_SPOKEN = "(nothing is spoken here)"



# An earlier screen is context, not the subject: its outline's top two levels (the screen and its main parts) are
# enough to recognise it, and the rest would crowd out the facts the notes carry.
_EARLIER_SCREEN_DEPTH = 1


def _render_list(values: list) -> str:
    return "; ".join(f'"{value}"' for value in values) if values else "none"


def _render_entries(values: list) -> str:
    return "".join(f"\n    - {value}" for value in values) if values else " none"


def render_latest_diagram(latest: dict | None) -> str:
    """The newest diagram any earlier frame drew, in full, so a diagram built up over several frames is extended
    rather than drawn again. Empty when no earlier frame drew one."""

    if not latest:
        return ""
    text = (f"\n\nThe latest diagram, drawn at frame {latest['index']}. Only when this frame shows more of the SAME "
            f"flow or system, return this diagram extended, its steps and numbers kept. A different flow is never "
            f"added to it: a different flow gets a diagram of its own when the screen or the speech shows one. It was "
            f"read off an earlier frame and can be wrong: find each of its arrows on THIS frame, tail and head, and "
            f"correct the ones this frame draws otherwise or not at all, saying so under `## Contradicts earlier`; "
            f"under `## Builds on`, write how many of its arrows you found as drawn (\"frame {latest['index']}: "
            f"diagram checked, <found> of <all> arrows as drawn\"):\n{mermaid_text_utils.name_bare_nodes(str(latest['diagram']).strip())}")

    return text


def _render_operator_question() -> str:
    question = config.settings.prompt.user_prompt.strip()

    return f"The reader of these notes wants to know: {question}\n" if question else ""


def _encode_frame(request: AttemptRequest,
                  relative_path: str) -> dict:
    """One frame as an image part, enlarged when it is narrower than `framesProcessing.minSentFrameWidth`."""

    frame = request.paths.output_root / relative_path

    return frame_message_utils.encode_frame(frame, config.settings.frames_processing.min_sent_frame_width)


# A board step's changed part is sent alone when it covers at most this share of the frame: on the 5 board recordings
# measured, the box round a step's change covered a median 26% of the screen
BOARD_REGION_SHARE = 0.5
# Said beside a board step sent as its changed part
BOARD_REGION_NOTE = ("\n\nThis frame is a step of a board: only the part that changed since the frame before it is shown, "
                     "cut out of the frame and enlarged. The rest of the board is as the frame before shows it. Write "
                     "what this part shows and what changed in it.")

# Said beside a frame phase 2 marked text-only while `framesProcessing.isTextOnlyFrameSentAsText` is on: it is sent
# with no image, and the lines the program read are all it holds
TEXT_ONLY_FRAME_NOTE = ("\n\nThis frame is not shown as an image: it holds only text, and the lines the program read on it, "
                        "listed above, are all of it. Write its notes from those lines and its words: \"## Screen\" quotes "
                        "each line whole, in the order listed, one part per line; never describe a colour, a position or "
                        "anything else you were not shown.")


def _is_sent_as_text(request: AttemptRequest,
                     item: dict) -> bool:
    """Whether the frame goes as its read text instead of its image: marked text-only,
    sent so while the setting is on, and holding lines the program reads; with none read it goes as its image."""

    # A board under text-only-integration goes as the words phase 2's board reader read on it (`boardWords`)
    is_board_text = (config.settings.frames.board_handler == "text-only-integration" and bool(item.get("isBoardTextOnly")))
    if not is_board_text and not (config.settings.frames_processing.is_text_only_frame_sent_as_text
                                  and item.get("isTextOnly")):
        return False
    if not render_screen_text_note(request, item):
        return False

    # A line a second reader finds and the listed lines lack would be lost with the image (DEFECTS 323: a build's
    # third list item Tesseract missed), so such a frame goes as its image with those lines named
    if is_board_text:
        return True
    # A second reader that runs here and reads nothing checked nothing: the frame goes as its image (DEFECTS 643)
    if apple_text_service.is_available() and not _is_read(str(request.paths.output_root / str(item.get("path") or ""))):
        return False

    return not _list_missed_lines(request, item)


def _select_board_region(request: AttemptRequest,
                         item: dict) -> list[int] | None:
    """The part of a board step phase 2 found changed (`boardChange`), when it covers at most
    BOARD_REGION_SHARE of the frame: sent in place of the whole frame. None for every other frame."""

    box = item.get("boardChange")
    # With text-diff-integration the step goes whole, its changes listed beside it (`render_board_text_diff_note`)
    if not box or config.settings.frames.board_handler == "text-diff-integration":
        return None
    size = frame_message_utils.read_frame_size(request.paths.output_root / item["path"])
    if size is None:
        return None
    x0, y0, x1, y1 = box

    return box if (x1 - x0) * (y1 - y0) <= BOARD_REGION_SHARE * size[0] * size[1] else None


def _build_frame_parts(request: AttemptRequest,
                       item: dict) -> list[dict]:
    """One frame as the call's image part (a board step's changed part alone, cut out and enlarged), or none for a
    frame sent as its read text. No address-bar band travels with it: see REQUIREMENTS.md R8."""

    if _is_sent_as_text(request, item):
        return []
    region = _select_board_region(request, item)
    if region is not None:
        return [frame_message_utils.encode_frame_region(request.paths.output_root / item["path"], region,
                                                        config.settings.frames_processing.min_sent_frame_width)]

    return [_encode_frame(request, item["path"])]


def _list_listed_text_groups(request: AttemptRequest,
                             item: dict) -> list:
    """The groups of text the program reads on a frame that its messages list. A top bar is window chrome: written as
    one line with its items never listed, so it is neither checked nor listed."""

    return [group for group in item_validator.list_screen_text_items(request.paths, item.get("slug", ""))
            if group.kind != item_validator.TOP_BAR_KIND]


def _build_follow_up_frame_parts(request: AttemptRequest,
                                 item: dict) -> list[dict]:
    """A frame for a batch's follow-up: its image, or for a frame sent as its read text, those lines, so a follow-up
    judges the notes against what the frame holds either way."""

    if not _is_sent_as_text(request, item):
        return _build_frame_parts(request, item)
    lines = [line for group in _list_listed_text_groups(request, item) for line in group.lines]

    return [{"type": "text", "text": "Sent as the lines the program read on it, with no image; they are all it holds:\n"
             + "".join(f'- "{line}"\n' for line in lines)}]


def render_text_only_note(request: AttemptRequest,
                          item: dict) -> str:
    return TEXT_ONLY_FRAME_NOTE if _is_sent_as_text(request, item) else ""


def render_board_region_note(request: AttemptRequest,
                             item: dict) -> str:
    return BOARD_REGION_NOTE if not _is_sent_as_text(request, item) and _select_board_region(request, item) else ""


# An editor split one pane above the other: the worker outlined the pane the talk was about and named the other pane's
# file as the open one, or read the two panes as one file (DEFECTS 129)
SPLIT_PANES_NOTE = ("\n\nThe program finds an editor split into {COUNT} panes on this frame, one above the other, each "
                    "ending on a status line of its own that names its file:\n{PANES}Outline each pane as its own part, "
                    "named by the file on its own status line, its code quoted under it; a file named on one pane's "
                    "status line is never the other pane's.")
SPLIT_PANE_LINE = "- the {ORDINAL} pane from the top: {FILE}\n"


def render_split_panes_note(request: AttemptRequest,
                            item: dict) -> str:
    """The panes of an editor split one above the other on the frame, each with the file its own status line names,
    for its message; empty for every other frame."""

    panes = editor_pane_service.list_split_panes(request.paths.output_root / str(item.get("path") or ""))
    if not panes:
        return ""
    ordinals = ("first", "second", "third", "fourth")
    lines = "".join(SPLIT_PANE_LINE.format(
        ORDINAL=ordinals[index] if index < len(ordinals) else f"{index + 1}th",
        FILE=f'"{pane["file"]}"' if pane["file"] else "its file name is not read: take it from the image")
        for index, pane in enumerate(panes))

    return SPLIT_PANES_NOTE.format(COUNT=len(panes), PANES=lines)


# A scroll step told to outline what it adds outlined the old lines and left its new output out (DEFECTS 207)
SCROLLED_IN_NOTE = ("\n\nThis frame is frame {BEFORE} scrolled: these lines are new on it, read by the program (it may "
                    "misread a letter, so quote the image): outline each of them, they are what this step adds:\n{LINES}")


def render_scrolled_in_note(request: AttemptRequest,
                            item: dict) -> str:
    """For a step that is the kept frame before it scrolled, the lines the scroll brings in; empty for any other
    frame."""

    if not item.get("stepOf"):
        return ""
    kept = sorted(item_validator.read_frame_items(request.paths), key=lambda other: int(other.get("index", 0)))
    earlier = [other for other in kept if int(other.get("index", 0)) < int(item.get("index", 0))]
    if not earlier:
        return ""
    lines = empty_step_service.list_scrolled_in_lines(request.paths.output_root / str(earlier[-1].get("path") or ""),
                                                      request.paths.output_root / str(item.get("path") or ""))
    if not lines:
        return ""

    return SCROLLED_IN_NOTE.format(BEFORE=int(earlier[-1]["index"]),
                                   LINES="".join(f'- "{hide_reader_marks(line)}"\n' for line in lines))


# A step's outline held only its step line, the details pane it switched to another item summed up there, and no
# reader reads the pane's small text to check it (DEFECTS 332)
CHANGED_PLACES_NOTE = ("\n\nThis step's pixels changed from frame {BEFORE} in these places (shares of the frame, from its left "
                       "and its top): {PLACES}. Under the step line, outline what each of these places shows now, every "
                       "label and line of text there quoted from this frame, never from frame {BEFORE} or the speech: a "
                       "pane switched to another item is quoted whole, never summed up in the step line.")


def render_changed_places_note(request: AttemptRequest,
                               item: dict) -> str:
    """For a build step, the places where its pixels changed from the kept frame before it; empty for any other frame."""

    if not item.get("stepOf"):
        return ""
    kept = sorted(item_validator.read_frame_items(request.paths), key=lambda other: int(other.get("index", 0)))
    earlier = [other for other in kept if int(other.get("index", 0)) < int(item.get("index", 0))]
    if not earlier:
        return ""
    places = empty_step_service.list_changed_places(request.paths.output_root / str(earlier[-1].get("path") or ""),
                                                    request.paths.output_root / str(item.get("path") or ""))
    if not places:
        return ""

    return CHANGED_PLACES_NOTE.format(BEFORE=int(earlier[-1]["index"]), PLACES="; ".join(
        f"{round(left * 100)}% to {round(right * 100)}% across, {round(top * 100)}% to {round(bottom * 100)}% down"
        for left, top, right, bottom in places))


# DEFECTS 339: the speech model wrote the talk's subject, a library its slides name, as a dish's name 60 times, and the
# notes carried the garbled name; the program respells only a word one or two letters off
GARBLED_NAMES_NOTE = ("\n\nThe speech writes {PAIRS}: a word said often and on no screen, probably the speech model's "
                      "garbling of the name the screen shows. Where it names the same thing, write the screen's "
                      "spelling outside quotes of the speech.")


def render_garbled_names_note(request: AttemptRequest,
                              item: dict) -> str:
    """The words of the frame's speech that may be a name the earlier screens show, garbled; empty when there are none."""

    frames_phase = output_contract.PHASE_NUMBERS["frames_preparing"]
    if not phase_file_service.has_phase_file(request.paths, 1) or not phase_file_service.has_phase_file(request.paths, frames_phase):
        return ""
    segments = phase_file_service.read_phase_file(request.paths, 1).get("segments") or []
    items = phase_file_service.read_phase_file(request.paths, frames_phase).get("items") or []
    screens = [str((other.get("record") or {}).get("screen") or "") for other in items
               if int(other.get("index", 0)) < int(item.get("index", 0))]
    pairs = garbled_name_utils.list_garbled_names(str(item.get("transcriptCurrent") or ""),
                                                  " ".join(str(segment.get("text") or "") for segment in segments), screens)

    return GARBLED_NAMES_NOTE.format(PAIRS="; ".join(f'"{heard}" where an earlier screen shows "{shown}"' for heard, shown in pairs)) if pairs else ""


# A slide struck out "gpu.js, onnx, wasm" before "WebGL!", and the outline struck only two of the three: the text readers
# return a struck word as any other (DEFECTS 627)
STRUCK_WORDS_NOTE = ("\n\nStruck through on the screen (a line drawn through the letters), as the text reader reads them, "
                     "a struck word often misread: {WORDS}. Outline each as struck through, with the screen's spelling, "
                     "and never state what it names as standing.")


def render_struck_words_note(request: AttemptRequest,
                             item: dict) -> str:
    """The words struck through on the frame, for its messages; empty when there are none or no text reader runs."""

    path = request.paths.output_root / str(item.get("path") or "")
    if not item.get("path") or not path.is_file() or not text_detection_service.is_available():
        return ""
    words = struck_word_service.list_struck_words(path)

    return STRUCK_WORDS_NOTE.format(WORDS=", ".join(f'"{word}"' for word in words)) if words else ""


# A demo's speech runs ahead of its screen: the notes gave a frame the value the presenter announced for the next one
# (DEFECTS 132)
ANNOUNCED_WORDS_NOTE = ("\n\nSaid over this frame but not on its screen, and on the next frame's screen: {WORDS}. The "
                        "speaker is announcing the next screen here: write each as said, never as shown on this frame.")


def _read_screen_words(request: AttemptRequest,
                       item: dict) -> list[str]:
    path = request.paths.output_root / str(item.get("path") or "")

    return [word["text"] for word in text_detection_service.find_words(
        path, text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE)] if path.is_file() else []


def render_announced_words_note(request: AttemptRequest,
                                item: dict) -> str:
    """The words said over the frame that only the next frame's screen shows, for its message; empty when there are
    none, or the frame is the last."""

    phase = output_contract.PHASE_NUMBERS["frames_preparing"]
    if not text_detection_service.is_available() or not phase_file_service.has_phase_file(request.paths, phase):
        return ""
    frames = phase_file_service.read_phase_file(request.paths, phase)
    later = sorted((other for other in frames.get("items") or [] if other.get("index", -1) > item.get("index", -1)),
                   key=lambda other: other["index"])
    if not later:
        return ""
    words = announced_words_utils.list_announced_words(str(item.get("transcriptCurrent") or ""),
                                                       _read_screen_words(request, item),
                                                       _read_screen_words(request, later[0]))

    return ANNOUNCED_WORDS_NOTE.format(WORDS=", ".join(f'"{word}"' for word in words)) if words else ""


# A question round said over a closing slide left `## Questions asked` empty (38c3-797 frame 63, DEFECTS 538)
ASKED_QUESTIONS_NOTE = ("\n\nAsked over this frame by someone other than the main speaker: {QUESTIONS}. Each one a person "
                        "asks the speaker goes under `## Questions asked` with the answer given; a filler or a "
                        "rhetorical question does not.")


def render_asked_questions_note(item: dict) -> str:
    """The questions other speakers ask over the frame, for its message; empty when there are none."""

    questions = asked_questions_utils.list_asked_questions(str(item.get("transcriptCurrent") or ""))

    return ASKED_QUESTIONS_NOTE.format(QUESTIONS="; ".join(f'"{question}"' for question in questions)) if questions else ""


def render_board_text_diff_note(item: dict) -> str:
    """The changes phase 2 read between a board step's text and the board frame before it (`boardTextDiff`), for its
    message; empty for every other frame, and with no change read."""

    diff = item.get("boardTextDiff")
    if not diff:
        return ""
    text = board_text_diff_utils.render_board_text_diff(diff, frame_slug_utils.read_frame_number(diff["against"]))

    return f"\n\n{text}" if text else ""


def _render_earlier_screen(screen: str) -> str:
    parts = [part for part in frame_markdown_utils.read_screen_parts(screen).parts if part.depth <= _EARLIER_SCREEN_DEPTH]

    return "".join(f"\n    {'  ' * part.depth}- {part.text}" for part in parts) if parts else " none"


def render_previous_frames(records: list[dict],
                           is_screen_shown: bool = True) -> str:
    """The accepted notes of the frames before this one, oldest first, one block per frame. The earlier frames a batch
    is handed as context carry no screen outline (`is_screen_shown` False): a worker copied a console's lines, a box's
    value and window bars from them into frames that showed otherwise."""

    if not records:
        return NO_EARLIER_FRAME
    blocks = []
    for record in records:
        screen = f"  screen:{_render_earlier_screen(str(record.get('screen') or ''))}\n" if is_screen_shown else ""
        blocks.append(f"Frame {record['index']} (at {_render_seconds(record['timestamp'])}), "
                      f"{record.get('screen_kind') or 'screen'}\n"
                      f"  facts:{_render_entries(record.get('facts', []))}\n"
                      f"{screen}"
                      f"  builds on: {_render_list(record.get('builds_on', []))}\n"
                      f"  open questions: {_render_list(record.get('open_questions', []))}")
    text = "\n".join(blocks)

    return text


def render_return_note(request: AttemptRequest,
                       item: dict) -> str:
    """For a frame that shows an earlier screen again: that showing's notes and words. Empty for any other frame."""

    shown_before = (request.inputs.get("returns") or {}).get(str(item.get("index")))
    if not shown_before:
        return ""
    record = shown_before.get("originalRecord")
    notes = (RETURN_NOTES_KNOWN.format(NOTES=render_previous_frames([record])) if record
             else RETURN_NOTES_IN_BATCH.format(ORIGINAL_NUMBER=shown_before["originalIndex"]))

    return RETURN_NOTE.format(ORIGINAL_NUMBER=shown_before["originalIndex"],
                              ORIGINAL_TIMESTAMP=_render_seconds(shown_before['originalTimestamp']),
                              ORIGINAL_NOTES=notes, ORIGINAL_SPEECH=shown_before.get("originalSpeech") or NOTHING_SPOKEN)


# The lines the check on `## Screen` counts, given to the worker beside its frame: an outline refused for unquoted text
# was written by a worker that never saw which lines are counted
SCREEN_TEXT_NOTE = ("\n\nThe program read these lines of text on this frame, between its top and bottom bands: text shown on "
                    "the screen, never words spoken, so they never go under \"## Speech visual alignment\" as speech. They "
                    "tell you WHICH texts to quote, never how they are spelt: the reader misreads letters, adds marks "
                    "that are icons and cuts words at a box edge; a \"…\" in a line is a character it could not read, which you "
                    "read off the image. Quote each text as the IMAGE spells it, "
                    "whole, in the part of the screen it sits in; a misreading copied into your notes is a wrong text, and so "
                    "is a note about it (\"(misread by OCR)\", \"(corrected from ...)\"): the reader is no part of the "
                    "screen. "
                    "Your `## Screen` is checked against these lines, and a word one letter off still counts as quoted, "
                    "so the image's spelling passes. Where a line here is garbled far past that (words missing, words "
                    "that are not on the image), still quote the image: one or two lines your outline cannot match never "
                    "refuse it. A line can join texts that stand side by side in "
                    "two columns: quote each text in the part it sits in. Each listed text appears quoted somewhere in "
                    "your outline, even in a part you write as one line (an application's ribbon or toolbar, a banner): "
                    "that line quotes the texts in it:\n")
SCREEN_TEXT_STEP_NOTE = "A line the slide's earlier steps already quote need not be quoted again.\n"
# How the frame's message lists a group of the lines the program read: running text and a side panel are named as one
# part each, so neither is summed up in one line of the outline
SCREEN_TEXT_GROUP_LEADS = {
    item_validator.PARAGRAPH_KIND: ("- one paragraph of running text, {COUNT} lines read: quote it whole as one part, "
                  "`paragraph \"<every sentence, as the image spells it>\"`, never \"a text block starting with ...\":\n"),
    item_validator.SIDE_PANEL_KIND: ("- the side panel on the left, {COUNT} lines read: outline it as one part with each entry on its own "
                   "line under it, `entry \"<its text>\"`, never \"sidebar: left side\":\n"),
}


# Marks the text reader writes for what it cannot read (an "=" read as "«", an icon as "©" or "@@"): a worker told to
# quote the image still copied them from the listed line, twice in one run, so the listed line holds none to copy
READER_MARKS = re.compile(r"[«»©®]|@@")


def hide_reader_marks(line: str) -> str:
    """A line the text reader read, each mark it invents for an unread character shown as "…", so the worker reads it
    off the image."""

    return READER_MARKS.sub("…", line)


def _render_text_group(group) -> str:
    """One group of the lines the program read: a paragraph or a side panel under its lead, any other line by line."""

    if group.kind not in SCREEN_TEXT_GROUP_LEADS:
        return "".join(f'- "{hide_reader_marks(line)}"\n' for line in group.lines)

    return (SCREEN_TEXT_GROUP_LEADS[group.kind].format(COUNT=len(group.lines))
            + "".join(f'  - "{hide_reader_marks(line)}"\n' for line in group.lines))


def render_screen_text_note(request: AttemptRequest,
                            item: dict) -> str:
    """The lines of text the program reads on the frame, each quoted, for its message. Empty when it reads none."""

    groups = _list_listed_text_groups(request, item)
    if not groups:
        return ""
    listed = "".join(_render_text_group(group) for group in groups)

    return SCREEN_TEXT_NOTE + listed + (SCREEN_TEXT_STEP_NOTE if item.get("stepOf") else "")


# A frame of a low-resolution recording whose text Tesseract misses at any scale: a file manager's names and a code
# slide's lines went unquoted, and nothing named them (DEFECTS 311)
MISSED_LINES_NOTE = ("\n\nA second text reader finds these lines too, which the list above lacks; it misreads letters "
                     "more often, so quote each text as the IMAGE spells it, in the part of the screen it sits in, and "
                     "leave out a line the image does not show:\n{LINES}")


def _list_missed_lines(request: AttemptRequest,
                       item: dict) -> list[str]:
    """The lines the second reader finds on the frame that its listed lines leave out."""

    listed = tuple(line for group in _list_listed_text_groups(request, item) for line in group.lines)

    return _read_missed_lines(str(request.paths.output_root / str(item.get("path") or "")), listed)


@functools.lru_cache(maxsize=64)
def _is_read(image_path: str) -> bool:
    return outline_reread_service.is_read(Path(image_path))


# A frame's message asks for it several times while it is built, and each asks the recognizer to read the frame again
@functools.lru_cache(maxsize=64)
def _read_missed_lines(image_path: str,
                       listed: tuple[str, ...]) -> list[str]:
    return outline_reread_service.list_missed_lines(Path(image_path), list(listed))


def render_missed_lines_note(request: AttemptRequest,
                             item: dict) -> str:
    """The lines Apple's text recognizer reads on the frame that the listed lines leave out, for its message; empty for
    a frame sent as its text, or when it reads none."""

    if _is_sent_as_text(request, item):
        return ""
    missed = _list_missed_lines(request, item)

    return MISSED_LINES_NOTE.format(LINES="".join(f'- "{line}"\n' for line in missed)) if missed else ""


# The listed lines are one reader's, and its misreads were copied into the notes as the screen's text (the reader
# circle, AGENTS.md; DEFECTS 579). Where the second reader reads a word otherwise, both readings are named
DIFFERING_WORDS_NOTE = ("\n\nThe two text readers read these words of the lines above differently; neither is the screen's "
                        "text until you check it on the IMAGE, and quote each as the image spells it:\n{PAIRS}")


def render_differing_words_note(request: AttemptRequest,
                                item: dict) -> str:
    """The words of the frame's listed lines the second reader reads otherwise, both readings, for its messages; empty
    when they agree or the recognizer cannot run."""

    if not item.get("path"):
        return ""
    listed = tuple(line for group in _list_listed_text_groups(request, item) for line in group.lines)
    pairs = _read_differing_words(str(request.paths.output_root / str(item["path"])), listed)

    return DIFFERING_WORDS_NOTE.format(PAIRS="".join(f'- "{first}" or "{second}"\n' for first, second in pairs)) if pairs else ""


# Asked for the first message and each follow-up's; each asks the recognizer to read the frame again
@functools.lru_cache(maxsize=64)
def _read_differing_words(image_path: str,
                          listed: tuple[str, ...]) -> list[tuple[str, str]]:
    return outline_reread_service.list_differing_words(Path(image_path), list(listed))


# A blocked frame is asked again only on a resume; like a phase 4 follow-up asked again after its
# block, its message opens with the refusal that blocked it
LAST_REFUSAL_NOTE = ("\n\nYour notes for this frame were refused before, for this reason; write them so it does not "
                     "happen again:\n{REFUSAL}\n")


def render_last_refusal(item: dict) -> str:
    """The refusal that blocked the frame last time, or nothing."""

    refusal = str(item.get("lastRefusal") or "").strip()

    return LAST_REFUSAL_NOTE.format(REFUSAL=refusal) if refusal else ""


def render_section_list() -> str:
    """Every section of a frame's notes, in order, from the record's own table: answers that wrote the sections from
    memory left some out."""

    headings = [f'"## {heading}"' for heading, _ in frame_record.FRAME_SECTIONS.values()]

    # The title line is named apart, and the frame's narration apart from the batch's: a worker counted the title as
    # the first of the sections and dropped `## What happens` to fit the count
    return (f"Under its title line, each document has these {len(headings)} sections, in this order, each under its "
            f'own heading, "{frame_record.EMPTY_MARK}" under one with nothing to say: ' + ", ".join(headings) + ". "
            '"## What happens" is each frame\'s own and is never left out: the batch\'s account under "# Batch" is '
            "another section.")


def _list_noted_slugs(request: AttemptRequest) -> set[str]:
    """The frames whose accepted notes this call is shown."""

    return {str(record.get("slug")) for record in request.inputs.get("previousRecords") or []}


def _list_build(items: list[dict],
                item: dict) -> tuple[str | None, list[dict]]:
    """The slug of the first frame of the screen built up step by step that `item` belongs to (None for a frame of no
    build), and the frames of `items` that are part of that build."""

    slug = item.get("slug")
    start = item.get("stepOf") or (slug if slug and any(other.get("stepOf") == slug for other in items) else None)
    build = [other for other in items if start and (other.get("slug") == start or other.get("stepOf") == start)]

    return start, build


def render_build_note(items: list[dict],
                      item: dict,
                      noted_slugs: set[str]) -> str:
    """For a frame of a screen built up step by step: its place in the build, from the frames of its call. Empty for any
    other frame, and for a step asked without its first step whose first step has no notes among the earlier ones
    (`noted_slugs`): with nothing to point to, the step is outlined whole like any frame."""

    start, build = _list_build(items, item)
    if not start:
        return ""
    place = find_build_position(items, item)
    if place is None:
        return BUILD_STEP_ALONE_NOTE.format(START=frame_slug_utils.read_frame_number(start)) if start in noted_slugs else ""
    position, count, first = place
    if position == 1:
        return BUILD_START_NOTE.format(START=first, END=int(build[-1]["index"]), COUNT=count)

    return BUILD_STEP_NOTE.format(NUMBER=position, COUNT=count, START=first)


def find_build_position(items: list[dict],
                        item: dict) -> tuple[int, int, int] | None:
    """The frame's place in a screen built up step by step among `items`: its step, the steps there are, and the build's
    first frame. None for a frame of no build, or one whose first step is not among `items`."""

    start, build = _list_build(items, item)
    if not any(other.get("slug") == start for other in build):
        return None
    numbers = [int(other["index"]) for other in build]

    return numbers.index(int(item["index"])) + 1, len(numbers), numbers[0]


def describe_review_frame(items: list[dict],
                          item: dict) -> str:
    """The line that opens a frame's message to the critique and the repair. A later step of a build says so: the
    critique, not told, judged a step's outline of only what it adds as a mistake and asked for a whole outline."""

    line = f"Frame {int(item['index'])}, at {_render_seconds(item.get('timestamp', 0.0))}"
    place = find_build_position(items, item)
    if place and place[0] > 1:
        step, count, first = place
        line += (f", step {step} of {count} of the screen first shown as frame {first}: its notes"
                 " outline only what this step adds, after their step line, which is the form for a step")

    return line + ":"


def render_open_questions_before(questions: list[str]) -> str:
    """The questions the batch right before left open, so a frame whose words answer one is read with its question:
    an answer that came one batch later was otherwise written as a wrong fact."""

    if not questions:
        return ""

    return ("\n\nQuestions the frames just before left open. When these frames' words or screen answer one, write the "
            "answer as a fact, naming what was asked; never repeat a question they do not answer:\n"
            + "\n".join(f"- {question}" for question in questions))


def render_tags_so_far(tags: list[str]) -> str:
    """R20: the subject tags earlier frames were given, most used first, so a frame about the same subject copies its
    string instead of coining another."""

    if not tags:
        return "\n\nTags so far: none yet."

    return ("\n\nTags so far, most used first. When this moment is about one of them, copy it exactly into `tags`:\n"
            + "\n".join(f"- {tag}" for tag in tags))


def _render_numbers(numbers: list) -> str:
    return ", ".join(str(number) for number in numbers)


def _is_account_failed(repair: dict | None) -> bool:
    """Whether the earlier call's account of the batch was refused, so a repair writes it again; else it is kept."""

    return bool(repair and repair.get("summaryReason"))


def render_repair_note(repair: dict | None) -> str:
    """What an earlier group call got right and why each of these frames failed in it; empty for a first call."""

    if not repair:
        return ""
    accepted = repair.get("accepted") or []
    failed = repair.get("failed") or []
    done = "It completed none of them."
    if accepted:
        done = (f"It completed frames {_render_numbers([record['index'] for record in accepted])}. Its accepted notes "
                f"for them, which your records may build on:\n{render_previous_frames(accepted)}")

    batch_numbers = _render_numbers(repair.get("batchNumbers") or [])
    summary = ""
    if _is_account_failed(repair):
        summary = REPAIR_SUMMARY_REJECTED.format(BATCH_NUMBERS=batch_numbers, REASON=repair["summaryReason"])

    return REPAIR_NOTE.format(BATCH_NUMBERS=batch_numbers,
                              WHY=REPAIR_CUT if repair.get("isCutAtLimit") else REPAIR_REJECTED, DONE=done,
                              FAILED_NUMBERS=_render_numbers([entry["frame"] for entry in failed]),
                              FAILED_REASONS="\n".join(f"- frame {entry['frame']}: {entry['reason']}" for entry in failed),
                              SUMMARY=summary)


def render_previous_findings(request: AttemptRequest) -> str:
    """What the last attempt got wrong, or nothing on a first attempt."""

    handoff = (compaction_prompt_loader.read_cut_thinking_handoff().format(summary=request.carry_over)
               if request.carry_over else "")
    if not request.findings:
        return handoff
    findings = "\n".join(f"  - {finding.get('code')}: {finding.get('detail', '')} {finding.get('fix', '')}".rstrip()
                         for finding in request.findings)

    return RETRY_NOTE.format(findings=findings) + handoff


def _render_seconds(seconds: object) -> str:
    """A time in the recording as the prompts write it: seconds with one decimal."""

    return f"{float(seconds):.1f} s"


def _render_previous_context(request: AttemptRequest) -> str:
    """What came before the frame or the group: the earlier frames' notes, the newest diagram, the tags so far and the
    open questions so far."""

    return (render_previous_frames(request.inputs.get("previousRecords") or [], is_screen_shown=False)
            + render_latest_diagram(request.inputs.get("latestDiagram"))
            + render_tags_so_far(request.inputs.get("tagsSoFar") or [])
            + render_open_questions_before(request.inputs.get("openQuestionsBefore") or []))


def _render_step_change_note(request: AttemptRequest,
                             item: dict) -> str:
    """A step's new lines when it is the frame before scrolled, else the places its pixels changed."""

    return render_scrolled_in_note(request, item) or render_changed_places_note(request, item)


def _render_frame_notes(request: AttemptRequest,
                        items: list[dict],
                        item: dict) -> str:
    """What the program adds to a frame's speech: the return note, its place in a build, the screen's text, the
    text-only note and the last refusal. One frame is a group of itself."""

    return (render_return_note(request, item) + render_build_note(items, item, _list_noted_slugs(request))
            + render_screen_text_note(request, item) + render_missed_lines_note(request, item)
            + render_differing_words_note(request, item)
            + render_text_columns_note(request, item)
            + render_split_panes_note(request, item)
            + render_announced_words_note(request, item) + render_garbled_names_note(request, item)
            + render_struck_words_note(request, item)
            + render_asked_questions_note(item)
            + _render_step_change_note(request, item)
            + render_text_only_note(request, item) + render_board_region_note(request, item) + render_board_text_diff_note(item) + render_last_refusal(item))


# A PDF's frames are pages and nobody speaks: its messages name the page's own text as what it is. Labelled as speech,
# the worker reasoned at length over whether the comments on a wiki page were spoken (pdf-01)
_PDF_LABELS = (("Spoken from this frame until the next one:", "This page's own text, read from the file:"),
               ("What was said before this frame:", "The text of the page before:"),
               ("What was said before the first of these frames:", "The text of the page before the first of these:"),
               ("when it was\ncaptured, the words spoken while it was on screen, and its frame",
                "its page, its own text, and the page's image"))


_PDF_FIGURE_LABELS = (("Spoken from this frame until the next one:", "The figure's caption, read from the file:"),
                      ("What was said before this frame:", "The page's text before the figure:"),
                      ("What was said before the first of these frames:", "The page's text before the first of these:"),
                      ("when it was\ncaptured, the words spoken while it was on screen, and its frame",
                       "its page, its caption, and the figure's image"))


def _rename_labels_for_pdf(text: str,
                     item: dict) -> str:
    """`text` with its speech labels named as a page's text, and "captured at <time>" as the page, for a PDF's page;
    as the figure's caption and its page's text for a figure of a PDF read as text only."""

    labels = {output_contract.PDF_MEDIUM: _PDF_LABELS, output_contract.PDF_FIGURE_MEDIUM: _PDF_FIGURE_LABELS}.get(
        str(item.get("medium") or ""))
    if labels is None:
        return text
    for old, new in labels:
        text = text.replace(old, new)
    page = item.get('page', int(item.get('index', 0)) + 1)

    return re.sub(r"captured at [0-9:]+", f"a figure of page {page}" if labels is _PDF_FIGURE_LABELS else f"page {page}", text)


def build_frame_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message and the human message, text and frame, of one call for one frame."""

    item = request.inputs.get("item") or {}
    system_text = system_prompt_builder.build_system_prompt(PHASE, item)
    human_text = FRAMES_PROCESSING_HUMAN_MESSAGE.format(
        PREVIOUS_FRAMES=_render_previous_context(request),
        TRANSCRIPT_BEFORE=item.get("transcriptBefore") or NOTHING_SPOKEN,
        FRAME_NUMBER=item.get("index"),
        TIMESTAMP=_render_seconds(item.get('timestamp', 0.0)),
        TRANSCRIPT_CURRENT=(item.get("transcriptCurrent") or NOTHING_SPOKEN) + _render_frame_notes(request, [item], item),
        prompt=_render_operator_question(),
        previous_findings=render_previous_findings(request), SECTIONS=render_section_list())

    return [SystemMessage(content=system_text),
            HumanMessage(content=[{"type": "text", "text": _rename_labels_for_pdf(human_text, item)},
                                  *_build_frame_parts(request, item)])]


def build_frame_batch_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message, one message of what came before, one message per frame with its words and its frame,
    and the closing instruction, of one call for a group of consecutive frames."""

    items = request.inputs.get("items") or []
    indices = [int(item.get("index")) for item in items]
    numbers = _render_numbers(indices)
    repair = request.inputs.get("repair")
    is_consecutive = indices == list(range(indices[0], indices[0] + len(indices)))
    system_text = system_prompt_builder.build_system_prompt(PHASE, items[0])
    context_text = GROUP_CONTEXT_MESSAGE.format(
        PREVIOUS_FRAMES=_render_previous_context(request),
        TRANSCRIPT_BEFORE=items[0].get("transcriptBefore") or NOTHING_SPOKEN, COUNT=len(items), NUMBERS=numbers,
        REPAIR=render_repair_note(repair), ORDER="consecutive " if is_consecutive else "")
    messages: list[BaseMessage] = [SystemMessage(content=system_text),
                                   HumanMessage(content=[{"type": "text", "text": _rename_labels_for_pdf(context_text, items[0])}])]
    for item in items:
        frame_text = GROUP_FRAME_MESSAGE.format(FRAME_NUMBER=item.get("index"),
                                                TIMESTAMP=_render_seconds(item.get('timestamp', 0.0)),
                                                TRANSCRIPT_CURRENT=item.get("transcriptCurrent") or NOTHING_SPOKEN)
        frame_text = _rename_labels_for_pdf(frame_text, item) + _render_frame_notes(request, items, item)
        messages.append(HumanMessage(content=[{"type": "text", "text": frame_text}, *_build_frame_parts(request, item)]))
    summary_ask = (SUMMARY_KEEP if not is_account_asked(request)
                   else SUMMARY_WRITE + (render_earlier_titles(request) or FIRST_BATCH_TITLES))
    closing_text = GROUP_CLOSING_MESSAGE.format(prompt=_render_operator_question(),
                                                previous_findings=render_previous_findings(request), NUMBERS=numbers,
                                                SECTIONS=render_section_list(), SUMMARY_ASK=summary_ask,
                                                ANSWER_OPENING="" if summary_ask == SUMMARY_KEEP else ANSWER_OPENING)
    messages.append(HumanMessage(content=[{"type": "text", "text": closing_text}]))

    return messages


def _render_speech(spoken: list[dict]) -> str:
    """One line per frame of the batch with what was said while it was shown; a frame that shows an earlier screen again
    names the frame it repeats, and a frame between the batch's frames that an earlier batch noted says so."""

    return "\n".join(f'Frame {entry["index"]} (at {_render_seconds(entry.get("timestamp", 0.0))}'
                     + (f', repeats frame {entry["repeatOfIndex"]}' if entry.get("repeatOfIndex") is not None else "")
                     + (", noted in an earlier batch" if entry.get("notedEarlier") else "")
                     + f'): "{entry.get("transcriptCurrent") or NOTHING_SPOKEN}"' for entry in spoken)


def _render_critique_rule(rule: dict,
                          accepted: list[dict]) -> str:
    """One rule the critique reported: its name, its lines, the frames it was seen on and, for each place it names, the
    notes' lines that place means."""

    frames = critique_markdown_utils.list_rule_frames(rule)
    lines = "".join(f"  {label}: {rule.get(field)}\n" for field, label in critique_record.RULE_LINES.items())
    seen_on = f"  On: frame{'s' if len(frames) != 1 else ''} {_render_numbers(frames)}\n" if frames else ""
    where = "".join(f"  Where: {place}\n"
                    + "".join(f"    The notes' line it names, whole: {line}\n"
                              for line in critique_markdown_utils.list_named_lines(place, accepted))
                    for place in rule.get("where") or [])

    return f"- {rule.get('name')}\n" + lines + seen_on + where


def render_critique_rules(request: AttemptRequest) -> str:
    """The rules the critique reported, for the repair's message: empty when the critique is off or found none."""

    batch = request.inputs.get("batch") or {}
    rules = batch.get("critiqueRules") or []
    if not rules:
        return ""
    accepted = batch.get("accepted") or []
    blocks = [_render_critique_rule(rule, accepted) for rule in rules]

    # A critique is a claim: two of four points on one batch were wrong (a blurred row called clear, a quote said a
    # second before its slide called the frame before's), and the fix pass, told to fix each, took a true fact out
    return ("\nWhat you said was wrong with these notes, when you criticised them a moment ago. Check each point "
            "against the frame and the words first: fix the ones they show, and leave a point they do not show, the "
            "notes unchanged by it. A line of the notes named below that you find wrong is replaced or removed under "
            "`## Edits` by the id the notes show in front of it:\n"
            + "".join(blocks))


def _render_speech_before(batch: dict) -> str:
    """What was said before the batch's first frame, as its first pass was handed it: a follow-up judging the notes
    without it took a point said before the batch for a mistake."""

    items = sorted(batch.get("items") or [], key=lambda item: int(item["index"]))

    return (items[0].get("transcriptBefore") if items else "") or NOTHING_SPOKEN


def build_critique_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The critique: the same material the repair gets - the notes as written, the speech, one frame per frame - and
    its own instruction, `critique.md`, which teaches the rule's shape. It reports; it changes nothing."""

    return _build_notes_review_messages(request, CRITIQUE_PHASE, CRITIQUE_MESSAGE, {})


def build_second_pass_messages(request: AttemptRequest) -> list[BaseMessage]:
    """R19: the system message, the batch's own notes as they were written, its speech, the rules its critique
    reported, and one frame per frame, so the repair can judge a screen's outline against the screen it outlines. Its
    system message is its own instruction, `second_pass.md`, which teaches the addition's shape."""

    return _build_notes_review_messages(request, SECOND_PASS_PHASE, SECOND_PASS_MESSAGE,
                                        {"CRITIQUE": render_critique_rules(request)})


def _list_spoken(batch: dict) -> list[dict]:
    """What was said while each frame of the batch was shown, in the frames' order."""

    return sorted(batch.get("spoken") or [], key=lambda entry: int(entry["index"]))


_NOT_PASSED = "(none passed)"


def _build_batch_system_text(system_phase: str,
                             batch: dict) -> str:
    """The system message of a batch's follow-up: the phase's own instruction, with the medium of the batch's frames,
    which a `when` rule reads: a PDF's pages add its rules. Handed no item, a PDF's critique judged its pages' notes as
    a meeting's, looped and was cut (pdf-01, 2026-10-21)."""

    medium = next((item.get("medium") for item in batch.get("items") or [] if item.get("medium")), None)

    return system_prompt_builder.build_system_prompt(system_phase, {"medium": medium} if medium else None)


def _read_review_fields(batch: dict,
                        is_numbered: bool = False) -> dict[str, str]:
    """The fields the critique, the repair and the open-questions follow-ups fill about a batch's notes: the notes as
    they stand, what was said before the batch, and its account."""

    return {"NOTES": "\n".join(frame_markdown_utils.render_notes_to_review(entry, is_numbered)
                               for entry in batch.get("accepted") or []),
            "SPEECH_BEFORE": _render_speech_before(batch), "SUMMARY": batch.get("summary") or _NOT_PASSED}


def _read_batch_fields(batch: dict,
                       spoken: list[dict]) -> dict[str, str]:
    """The fields every message about a batch fills: its frame numbers, its first and last second, and its speech."""

    return {"NUMBERS": _render_numbers(batch.get("frames") or []), "START": _render_seconds(batch["start"]),
            "END": _render_seconds(batch["end"]), "SPEECH": _render_speech(spoken)}


# DEFECTS 278: an outline listed parts its frame does not show, taken from the speech or a neighbouring frame, and the
# second pass kept them when the critique named them; DEFECTS 284: a terminal's table was outlined with numbers of other
# rows. No reader settles either alone, so the worker looks for each on the image
UNREAD_LABELS_NOTE = {
    CRITIQUE_PHASE: ("\nThe program's text reader finds none of these labels the notes quote under `## Screen` on this "
                     "frame: {LABELS}. It misses and misreads small labels, so look once for each on the image, in the "
                     "quote named, and judge each alone: every label the frame shows is right and nothing to report, "
                     "even when all of them are shown; every label the frame does not show came from the speech or "
                     "another frame, and is a mistake to report.\n"),
    SECOND_PASS_PHASE: ("\nThe program's text reader finds none of these labels the notes quote under `## Screen` on "
                        "this frame: {LABELS}. It misses and misreads small labels, so look once for each on the image, "
                        "in the quote named, and judge each alone: every label the frame shows stays, even when all of "
                        "them are shown; every label it does not show goes under `## Not on this frame`.\n"),
}
MISREAD_NUMBERS_NOTE = {
    CRITIQUE_PHASE: ("\nThe program's text reader reads these lines the notes quote under `## Screen` with other "
                     "numbers (it garbles letters and can misread a digit too, so the image decides, and a number the speaker reads out confirms it):\n{PAIRS}Check each number on the image: one the "
                     "notes got wrong is a mistake to report.\n"),
    SECOND_PASS_PHASE: ("\nThe program's text reader reads these lines the notes quote under `## Screen` with other "
                        "numbers (it garbles letters and can misread a digit too, so the image decides, and a number the speaker reads out confirms it):\n{PAIRS}Check each number on the image, and "
                        "where the notes got one wrong write `## Screen` again with it as the frame shows, and replace "
                        "a fact quoting it under `## Edits` by its id.\n"),
}


# DEFECTS 333: on a 545-pixel terminal an outline left a third of a table's rows and half a panel's fields out, and no
# reader reads them well enough to name the missing ones; the recognizer still counts the lines in each column
TEXT_COLUMNS_NOTE = {
    "": ("\n\nThe program's reader finds these columns of text lines on this frame (it garbles small letters, so "
         "quote the image, never its reading): {COLUMNS}. Under `## Screen` quote every line of each: a table's every "
         "row and a panel's every field."),
    CRITIQUE_PHASE: ("\nThe program's reader finds these columns of text lines on this frame (it garbles small "
                     "letters): {COLUMNS}. An outline that quotes fewer lines than a column holds left rows or fields "
                     "out, a mistake to report.\n"),
    SECOND_PASS_PHASE: ("\nThe program's reader finds these columns of text lines on this frame (it garbles small "
                        "letters, so quote the image, never its reading): {COLUMNS}. Where the outline quotes fewer "
                        "lines than a column holds, write `## Screen` again with every line of it.\n"),
}


# A line of an outline that quotes text, in double quotes or backticks
_QUOTING_LINE = re.compile(r'"[^"]+"|`[^`]+`')


def _render_text_columns(image_path: Path,
                         phase: str,
                         screen: str = "") -> str:
    """The columns note; for a follow-up, only the columns holding more lines than the outline quotes: told of a
    five-line column an outline quoted whole, a repair spent its thinking looking for the line it lacked (DEFECTS 508)."""

    quoted = sum(1 for line in screen.splitlines() if _QUOTING_LINE.search(line))
    columns = [column for column in outline_reread_service.list_text_columns(image_path)
               if not screen or column[3] > quoted]
    if not columns:
        return ""

    return TEXT_COLUMNS_NOTE[phase].format(COLUMNS="; ".join(
        f"{count} lines starting {round(left * 100)}% across, from {round(top * 100)}% to {round(bottom * 100)}% down"
        for left, top, bottom, count in columns))


def render_text_columns_note(request: AttemptRequest,
                             item: dict) -> str:
    """For a frame, the columns of text lines the recognizer counts on it; empty when it finds none."""

    if not item.get("path"):
        return ""

    return _render_text_columns(request.paths.output_root / str(item["path"]), "")


# DEFECTS 319: the program adds to an outline the lines its reader finds that the outline leaves out, and a misreading
# of a small editor ("Sore Code F") was added as a line; no pass saw these lines, so none could take one out
PROGRAM_LINES_NOTE = {
    CRITIQUE_PHASE: ("\nThe program added these lines its reader found on this frame to the outline, under its own part "
                     "(added after the notes above were written): {LINES}. Its reader misreads small text, so look for each "
                     "on the image: a line no part of the frame shows is a mistake to report.\n"),
    SECOND_PASS_PHASE: ("\nThe program added these lines its reader found on this frame to the outline, under its own "
                        "part (the notes above are shown without it): {LINES}. Its reader misreads small text, so look "
                        "for each on the image: name a line no part of the frame shows under `## Not on this frame`, "
                        "and the program leaves it out.\n"),
}


# DEFECTS 276: a slide's small link was quoted a letter off on one frame while the notes of the frames before spelled
# it right; the same test also names real other words (remote and remove), so the worker decides on the image
# A quoted word the frame's reading spells a letter or two off at the same place (DEFECTS 573): a screen's own typo
# written corrected, or a word misread; the reader misreads too, so the worker checks each on the image
READING_SPELLINGS_NOTE = {
    CRITIQUE_PHASE: ("\nThese words the notes quote under `## Screen` stand on the frame's reading, at the same place in "
                     "the same line, spelled a letter or two differently: {PAIRS}. The screen's own typo is quoted as "
                     "the screen spells it, and the reader also misreads, so check each on the image: a quote that "
                     "corrects or misreads what the frame shows is a mistake to report; one the frame shows as quoted "
                     "is right.\n"),
    SECOND_PASS_PHASE: ("\nThese words the notes quote under `## Screen` stand on the frame's reading, at the same place "
                        "in the same line, spelled a letter or two differently: {PAIRS}. Check each on the image: where "
                        "the frame shows the reading's spelling, a typo of the screen included, replace the line with "
                        "that spelling; where it shows the quote's, leave it.\n"),
}


NEAR_SPELLINGS_NOTE = {
    CRITIQUE_PHASE: ("\nThese words the notes quote under `## Screen` are one letter off a word the notes of earlier "
                     "frames quote often, and no earlier frame quotes them: {PAIRS}. Small text is easily misread by a "
                     "letter, so check each on the image: one the frame spells as the earlier frames do is a mistake "
                     "to report; one that is another word the frame shows is right.\n"),
    SECOND_PASS_PHASE: ("\nThese words the notes quote under `## Screen` are one letter off a word the notes of "
                        "earlier frames quote often, and no earlier frame quotes them: {PAIRS}. Small text is easily "
                        "misread by a letter, so check each on the image: where the frame spells it as the earlier "
                        "frames do, write `## Screen` again with that spelling; where it shows another word, leave "
                        "it.\n"),
}


# DEFECTS 376: a frame's table wrote a word four letters off its own outline's, the screen's spelling, and a note then
# called the screen's word a typo; which of the two is right only the image says, so the worker reads it
TABLE_SPELLINGS_NOTE = {
    CRITIQUE_PHASE: ("\nThese words `## As markdown` writes are spelled close to, but not as, a word `## Screen` quotes "
                     "for the same frame: {PAIRS}. Read each on the image: the one the frame does not show is a mistake "
                     "to report.\n"),
    SECOND_PASS_PHASE: ("\nThese words `## As markdown` writes are spelled close to, but not as, a word `## Screen` "
                        "quotes for the same frame: {PAIRS}. Read each on the image and write the part that spells it "
                        "otherwise again with the frame's spelling.\n"),
}


SLIDE_SUBJECT_NOTE = {
    CRITIQUE_PHASE: ("\nThese facts have the slide, the screen or the talk as their subject, not the thing taught: {FACTS}. "
                     "One that states nothing about the subject beyond what the frame's other facts state is a mistake "
                     "to report.\n"),
    SECOND_PASS_PHASE: ("\nThese facts have the slide, the screen or the talk as their subject, not the thing taught: "
                        "{FACTS}. Replace each with what it teaches about the subject itself, or remove it when the frame's "
                        "other facts already state that.\n"),
}


# DEFECTS 426: the critique said nothing while the speech held facts no note had, on four of twelve batches; the program
# names the sentences of the frame's speech that its notes carry little of, so the gap is handed over as data
THINLY_NOTED_NOTE = {
    CRITIQUE_PHASE: ("\nThese sentences said over this frame have little of their words in any note of it: {SENTENCES}. "
                     "One that states a fact the notes lack is a mistake to report: a fact said and never written.\n"),
    SECOND_PASS_PHASE: ("\nThese sentences said over this frame have little of their words in any note of it: "
                        "{SENTENCES}. Add each one that states a fact the notes lack as a fact; leave one that is small "
                        "talk or that the notes hold in other words.\n"),
}


def _name_label_in_quote(label: str,
                         screen: str) -> str:
    """A label with the quote of the outline it stands in, when that quote holds more: named alone, "PKCS" of the quote
    "sig_id: PKCS#7" sent a critique looking for a label of its own until its thinking looped (DEFECTS 335)."""

    quote = next((text for text in re.findall(r'"([^"]+)"', screen) if label in text), label)

    return f'"{label}"' if quote == label else f'"{label}" (in "{quote}")'


def render_outline_reread_note(request: AttemptRequest,
                               item: dict,
                               phase: str) -> str:
    """What the reader reads differently from the frame's outline, for the critique and the second pass: the labels it
    finds nowhere on the frame, the lines it reads with other numbers, and the quoted words a letter off the spelling
    earlier outlines hold. Empty for any other step or when it finds
    nothing."""

    if phase not in UNREAD_LABELS_NOTE or not item.get("slug") or not item.get("path"):
        return ""
    try:
        record = record_file_service.read_frame_record(request.paths, str(item["slug"]))
        screen = str(record.get("screen") or "")
    except (OSError, ValueError):
        return ""
    image_path = request.paths.output_root / str(item["path"])
    labels = outline_reread_service.list_unread_labels(image_path, screen)
    pairs = outline_reread_service.list_misread_numbers(image_path, screen)
    note = UNREAD_LABELS_NOTE[phase].format(LABELS=", ".join(_name_label_in_quote(label, screen) for label in labels)) if labels else ""
    if pairs:
        listed = "".join(f'- the notes: "{quote}"; read: "{line}"\n' for quote, line in pairs)
        note += MISREAD_NUMBERS_NOTE[phase].format(PAIRS=listed)
    note += _render_text_columns(image_path, phase, screen)
    note += render_struck_words_note(request, item)
    note += render_differing_words_note(request, item)
    program_lines = item_validator.list_program_screen_lines(screen)
    if program_lines:
        note += PROGRAM_LINES_NOTE[phase].format(LINES=", ".join(f'"{line}"' for line in program_lines))
    table_pairs = item_validator.list_table_outline_spellings(record)
    if table_pairs:
        note += TABLE_SPELLINGS_NOTE[phase].format(PAIRS=", ".join(f'"{word}" (the outline "{other}")' for word, other in table_pairs))
    slide_facts = frame_markdown_utils.list_slide_subject_facts({**record, "frame": record.get("frame") or item.get("index")})
    if slide_facts:
        note += SLIDE_SUBJECT_NOTE[phase].format(FACTS=", ".join(f'[{line_id}] "{fact}"' for line_id, fact in slide_facts))
    read_spellings = item_validator.list_reading_spellings(item, screen)
    if read_spellings:
        note += READING_SPELLINGS_NOTE[phase].format(
            PAIRS=", ".join(f'"{word}" (read "{seen}")' for word, seen in read_spellings))
    near = item_validator.list_near_spellings(request.paths, str(item["slug"]), screen)
    if near:
        note += NEAR_SPELLINGS_NOTE[phase].format(PAIRS=", ".join(f'"{word}" (earlier "{other}")' for word, other in near))
    thin = frame_markdown_utils.list_thinly_noted_sentences(record, str(item.get("transcriptCurrent") or ""))
    if thin:
        note += THINLY_NOTED_NOTE[phase].format(SENTENCES=" ".join(f'"{sentence}"' for sentence in thin))

    return note


def _build_frame_review_messages(request: AttemptRequest,
                                 batch: dict,
                                 phase: str = "") -> list[BaseMessage]:
    """Every frame of the batch again, each under its own number: the one thing the notes cannot be judged against in
    text."""

    items = batch.get("items") or []
    messages: list[BaseMessage] = []
    for item in items:
        text = describe_review_frame(items, item) + render_outline_reread_note(request, item, phase)
        parts: list[dict] = [{"type": "text", "text": text}]
        parts += _build_follow_up_frame_parts(request, item)
        messages.append(HumanMessage(content=parts))

    return messages


def _build_notes_review_messages(request: AttemptRequest,
                                 system_phase: str,
                                 template: PromptTemplate,
                                 extra_fields: dict[str, str]) -> list[BaseMessage]:
    """What the critique and the repair are both sent: the batch's notes as they stand, its speech, its account, and
    one message per frame carrying that frame. The two differ only in their instruction, their closing ask and the
    `extra_fields` their template fills."""

    batch = request.inputs.get("batch") or {}
    spoken = _list_spoken(batch)
    system_text = _build_batch_system_text(system_phase, batch)
    human_text = template.format(
        **_read_batch_fields(batch, spoken), **_read_review_fields(batch, system_phase == SECOND_PASS_PHASE), **extra_fields,
        ATMOSPHERE=batch.get("atmosphere") or _NOT_PASSED, previous_findings=render_previous_findings(request))

    return [SystemMessage(content=system_text), HumanMessage(content=[{"type": "text", "text": human_text}]),
            *_build_frame_review_messages(request, batch, system_phase)]


def _build_batch_follow_up_messages(request: AttemptRequest,
                                    system_phase: str,
                                    template: PromptTemplate) -> list[BaseMessage]:
    """The system message of the follow-up's own instruction, the batch's notes, its speech and its account, and one
    frame per frame, so an answer can be looked for in the screen as well as in the notes."""

    batch = request.inputs.get("batch") or {}
    spoken = _list_spoken(batch)
    system_text = _build_batch_system_text(system_phase, batch)
    human_text = template.format(
        **_read_batch_fields(batch, spoken), **_read_review_fields(batch),
        SPEECH_AFTER=batch.get("speechAfter") or NOTHING_SPOKEN,
        QUESTIONS="\n".join(f"- {question}" for question in batch.get("questions") or []),
        EARLIER=render_earlier_lines(batch),
        previous_findings=render_previous_findings(request))

    return [SystemMessage(content=system_text), HumanMessage(content=[{"type": "text", "text": human_text}]),
            *_build_frame_review_messages(request, batch)]


# DEFECTS 268: the answers step read its own batch only, and kept a question open that an earlier batch's slide answered
EARLIER_LINES_NOTE = ("\nLines of earlier frames' notes that use a rare word of a question (an answer may stand there; "
                      "cite the frame when it does):\n{LINES}")


def render_earlier_lines(batch: dict) -> str:
    """The earlier frames' lines the program found for the batch's questions; empty when it found none."""

    lines = batch.get("earlierLines") or []
    if not lines:
        return ""

    return EARLIER_LINES_NOTE.format(LINES="".join(f"- frame {frame}: {line}\n" for frame, line in lines))


def build_open_questions_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The batch's follow-up after the second pass: the questions it leaves open."""

    return _build_batch_follow_up_messages(request, OPEN_QUESTIONS_PHASE, OPEN_QUESTIONS_MESSAGE)


def build_answer_questions_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The follow-up after the open questions: the answers found for those questions."""

    return _build_batch_follow_up_messages(request, ANSWER_QUESTIONS_PHASE, ANSWER_QUESTIONS_MESSAGE)


def render_earlier_titles(request: AttemptRequest) -> str:
    """The titles the meeting's earlier batches took, so this batch's title names something else; nothing before the
    first batch."""

    batches = [batch for batch in batch_summaries_service.read_batch_summaries(request.paths)
               if str(batch.get("title") or "").strip()]
    if not batches:
        return ""
    # The batch just before is named: a demo going on over two batches titled the second one with the first's topic,
    # "Bug fixing workflow with coding agent" after "Bug fixing workflows with coding agents" (Lecture 7, DEFECTS 562).
    # It is the one ending last before this batch's first frame, since a batch finished after a block is stored later
    first = _GROUP_FIRST_FRAME.match(str(request.inputs.get("slug") or ""))
    before = [batch for batch in batches if first and batch.get("frames") and max(batch["frames"]) < int(first.group(1))]
    previous = max(before, key=lambda batch: max(batch["frames"])) if before else None
    listed = [f"- {str(batch['title']).strip()}" + (_JUST_BEFORE if batch is previous else "") for batch in batches]

    return "\nThe earlier batches' titles, none to be repeated:\n" + "\n".join(listed)


# A batch's slug names its first frame ("frames-069-077")
_GROUP_FIRST_FRAME = re.compile(r"frames-(\d+)")
_JUST_BEFORE = " (the batch just before this one: if these frames go on with its topic, the title names what they add to it)"


def build_batch_summary_messages(request: AttemptRequest) -> list[BaseMessage]:
    """R15: the system message and one text-only message asking for a batch's account alone, handed the batch's
    accepted notes and everything said while its frames were shown."""

    batch = request.inputs.get("batch") or {}
    spoken = _list_spoken(batch)
    system_text = _build_batch_system_text(PHASE, batch)
    human_text = BATCH_SUMMARY_MESSAGE.format(
        MIN_SENTENCES=item_validator.SUMMARY_MIN_SENTENCES, MAX_SENTENCES=item_validator.SUMMARY_MAX_SENTENCES, MAX_WORDS=item_validator.SUMMARY_MAX_WORDS,
        **_read_batch_fields(batch, spoken), REASON=batch.get("reason") or "",
        NOTES=render_previous_frames(batch.get("accepted") or []), TITLES=render_earlier_titles(request),
        previous_findings=render_previous_findings(request))

    return [SystemMessage(content=system_text), HumanMessage(content=[{"type": "text", "text": human_text}])]
