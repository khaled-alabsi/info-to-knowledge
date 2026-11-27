"""What one frame's notes hold, and the markdown headings the model writes them under. REQUIREMENTS.md R3, layer 1.

Knowledge, not a description of the frame. The frames are shared screens of business meetings:
slides, diagrams, code, web applications. What a reader who did not attend needs from one of them is
what it teaches: what the named things are, how they relate, which steps, rules and numbers apply.
`take_aways` comes first and is the model's own reading of the moment: each thing said tied to the part of the screen
it is about, then what a reader carries away and what stayed unclear. It is the one field where a guess is allowed,
and it is kept rather than thrown away, so a reader can see how the facts were arrived at.
Then `facts` is the core field, `diagram` carries a flow or an architecture the screen draws or the
speech explains, and `builds_on` ties the frame to the earlier notes, where a diagram built up across
several frames is EXTENDED, never drawn again from nothing.

`screen` is the frame itself, as an outline: one part of the screen per line, `- <what it is>: <where it sits, its
colour, its state>`, its exact text in double quotes, a part inside another indented under it, like HTML in words.

THE REPLY IS MARKDOWN, not a function call: one `# Frame N · <screen kind>` document per
frame, each field under its own `##` heading in `FRAME_SECTIONS`, a batch's account under `# Batch`, and the second
pass's "no change" as `# Nothing to change`. `utils/frame_markdown_utils.py` reads it into a dict keyed by the field
names below and writes a dict back as the same document, which is also how the notes are stored on disk. The
headings are taught in `agent-context/instructions/frames_processing.md` and `second_pass.md`, and a test holds the two in step.

A field's section may hold `(none)`; which fields must say something (`tags` always, `facts` on a first showing)
is judged by the validator, from disk.
"""

from __future__ import annotations

__all__ = [
    "SCREENSHOT_NEEDED", "SCREENSHOT_HELPS", "SCREENSHOT_IMPORTANCE_VALUES", "KIND_TAGS",
    "FRAME_TITLE", "DOCUMENT_FRAME_TITLES", "BATCH_TITLE", "FRAME_DATA_HEADING", "BATCH_DATA_HEADING", "NOTHING_TO_CHANGE_TITLE", "FEEDBACK_TITLE",
    "SECTION_SHAPES", "LIST_SHAPE", "OUTLINE_SHAPE", "DIAGRAM_SHAPE", "MARKDOWN_SHAPE", "TEXT_SHAPE", "LABELLED_SHAPE",
    "TAGS_SHAPE", "FRAME_SECTIONS", "FORMER_HEADINGS", "LABELLED_SECTIONS", "BATCH_SECTIONS", "BATCH_HEADING_ALIASES", "BATCH_FOLLOW_UP_SECTIONS",
    "ANSWER_SEPARATOR", "WHAT_CHANGED_HEADING", "NOT_ON_FRAME_HEADING", "WRONG_DIAGRAM_HEADING", "EDITS_HEADING",
    "WRONG_NARRATION_HEADING", "FRAME_FIELDS", "EMPTY_MARK"]

# The only words a screenshot entry's `importance` may hold. Phase 3 checks and phase 4 reads them letter for letter.
# A screenshot a reader cannot do without
SCREENSHOT_NEEDED = "needed"
# A screenshot that helps
SCREENSHOT_HELPS = "helps"
SCREENSHOT_IMPORTANCE_VALUES = (SCREENSHOT_NEEDED, SCREENSHOT_HELPS)
# REQUIREMENTS.md R12: what kind of facts a moment gives, from DITA's information types and the RAID log's
# categories, keeping only the kinds something reads. The fields that exist already (action items, questions, URLs,
# open questions, diagrams, the screen kind) are not repeated here.
KIND_TAGS = ("concept", "procedure", "reference", "problem", "risk", "decision", "status")

# The level-1 headings a reply is made of. `# Frame 12 · web application` carries the frame's number and its
# `screen_kind`.
FRAME_TITLE = "Frame"
# A PDF's stored notes name what the frame is to a reader, a page or a figure (the owner: "what is frames in pdf?"); the
# model is still asked and answers under FRAME_TITLE, and the reader takes all three
DOCUMENT_FRAME_TITLES = {"pdf": "Page", "pdf-figure": "Figure"}
BATCH_TITLE = "Batch"
# The section that holds what the program knows about a frame or a batch beside the model's notes: it is written to the file and
# skipped when the file is read back, so a file that holds it still reads.
FRAME_DATA_HEADING = "Frame data"
BATCH_DATA_HEADING = "Batch data"
NOTHING_TO_CHANGE_TITLE = "Nothing to change"
# A report on the instructions or a check, never part of the notes: written into worker-feedback.md
FEEDBACK_TITLE = "Feedback"

# How a section is written:
#   list      one `- ` line per entry
#   outline   the screen's parts, `- ` lines indented two spaces per level, kept as written
#   diagram   one ```mermaid fence
#   markdown  markdown blocks kept as written: the screen's code, table or list a document shows as text, not a picture
#   text      a paragraph
#   labelled  one `- ` line per entry, its details on indented `- <label>: <value>` lines (`LABELLED_SECTIONS`)
#   tags      one line of tags separated by commas
LIST_SHAPE = "list"
OUTLINE_SHAPE = "outline"
DIAGRAM_SHAPE = "diagram"
MARKDOWN_SHAPE = "markdown"
TEXT_SHAPE = "text"
LABELLED_SHAPE = "labelled"
TAGS_SHAPE = "tags"
SECTION_SHAPES = (LIST_SHAPE, OUTLINE_SHAPE, DIAGRAM_SHAPE, MARKDOWN_SHAPE, TEXT_SHAPE, LABELLED_SHAPE, TAGS_SHAPE)
# field -> (its `##` heading, its shape), in the order a document holds them
FRAME_SECTIONS = {
    "take_aways": ("Take aways", LIST_SHAPE),
    "facts": ("Facts", LIST_SHAPE),
    "screen": ("Screen", OUTLINE_SHAPE),
    "diagram": ("Diagram", DIAGRAM_SHAPE),
    "as_markdown": ("As markdown", MARKDOWN_SHAPE),
    "speech_visual_alignment": ("Speech visual alignment", TEXT_SHAPE),
    "third_person_description": ("What happens", TEXT_SHAPE),
    "builds_on": ("Builds on", LIST_SHAPE),
    "contradicts_earlier": ("Contradicts earlier", LIST_SHAPE),
    "open_questions": ("Open questions", LIST_SHAPE),
    "urls": ("Web addresses", LABELLED_SHAPE),
    "action_items": ("Action items", LIST_SHAPE),
    "questions_asked": ("Questions asked", LIST_SHAPE),
    "screenshots": ("Screenshots", LABELLED_SHAPE),
    "tags": ("Tags", TAGS_SHAPE),
}
# A labelled entry: the field its first line fills, then label -> field for its detail lines. An address alone cannot
# tell a reader whether it was opened, quoted on a slide or only named (R8); a screenshot names its part, how much it
# matters and why (R10).
LABELLED_SECTIONS = {
    "urls": ("url", {"what it is": "what_it_is", "how it appears": "how_it_appears"}),
    "screenshots": ("part", {"importance": "importance", "why": "why"}),
}
# REQUIREMENTS.md R15 and R18: the three sections a batch's own call writes under `# Batch`:
#   batch_title    names the topic of the batch's own frames, never the meeting's, so a reader of the notes and the
#                  phase 4 writer find a batch by what it is about
#   batch_summary  the batch's account
#   atmosphere     how the batch felt
BATCH_SECTIONS = {"batch_title": "Title", "batch_summary": "Story of the batch", "atmosphere": "Atmosphere"}
# The batch account's former heading, read so the answers and files written under it still read back (DEFECTS 310):
# "What happens in the batch" opened with the frame section's name, and batches left "## What happens" out of frames
BATCH_HEADING_ALIASES = {"What happens in the batch": "batch_summary"}
# The two that come after the batch's notes, each from a follow-up call: the questions the batch leaves open, then the
# replies the follow-up found for them. Lists of `- ` entries, and present in a batch only once a follow-up ran.
# The answers step's third section: a question that is no question (a screen detail the talk never raises, what the
# notes or the speech already say, a misheard name the screen spells), with why; it never reaches the document's open
# points. With only open and answered, such questions stayed open and reached the document (DEFECTS 486, 580)
BATCH_FOLLOW_UP_SECTIONS = {"open_questions": "Open questions", "answered_questions": "Answered questions",
                            "not_questions": "Not a question"}
# The open-questions step's worksheet: one line per candidate it weighed with its decision, written once so the
# decision is never made again (an answer of "(none)" took a median 18,638 characters of thinking over 24 public calls,
# re-deciding settled candidates, DEFECTS 641). Read and set aside: no later step reads it.
CONSIDERED_HEADING = "Considered"
# What separates a question from its answer inside one `## Answered questions` entry.
ANSWER_SEPARATOR = " → "
# R19: the five headings only an addition has. The sentence saying what the notes were missing.
WHAT_CHANGED_HEADING = "What changed"
# R19: each part the notes put on this frame that the frame does not show, by the
# text the notes quote it with. The one way a second look may take something out of the screen's outline and diagram.
NOT_ON_FRAME_HEADING = "Not on this frame"
# R19: each line of the accepted diagram the frame shows otherwise (an arrow drawn backwards, from the wrong box), quoted
# as the diagram writes it: the one way a second look may correct a diagram it may otherwise only extend.
WRONG_DIAGRAM_HEADING = "Wrong in the diagram"
# R19: each sentence of the accepted narration (`## What happens`) said only over another frame, quoted whole: the one way
# a second look may make the narration shorter.
WRONG_NARRATION_HEADING = "Said over another frame"
# The repair's edits of its frames' facts and take-aways, each naming the line it changes by the id the repair request
# shows in front of it ("- replace F158.2: <new text>", "- remove T158.1"): the program applies each as named and guesses
# nothing (DEFECTS 357)
EDITS_HEADING = "Edits"
# heading -> field for a heading a past run's notes were written under, so they still read back. `## Knowledge` was
# renamed `## Facts`: a model reading "Knowledge" wrote lines about the meeting, not facts.
FORMER_HEADINGS = {"Knowledge": "facts"}
# Every field of a frame's record, in the order a document holds them.
FRAME_FIELDS = ("frame", "screen_kind", *FRAME_SECTIONS)
# What an empty section may say instead of nothing.
EMPTY_MARK = "(none)"
