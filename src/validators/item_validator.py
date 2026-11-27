"""Validate one frame's record. A pure function over bytes on disk.

The order is fixed. HOW THE CALL ENDED is judged first, from the call log, and a finding there is an
early return: a reply that was cut short, or that held only thinking, has no record worth reading,
and the record an earlier attempt left on disk must never be judged as this attempt's. That early
return is what stops the defect this project was rebuilt over, 17 empty answers reported as success.

What a program can check is checked here. A check either REJECTS (the record is unusable: it says nothing, a
screen full of text yielded no facts, a diagram is not Mermaid, the screen's outline is missing or not an
outline, the narration is missing or too long) or NOTES (a style slip in text no reader sees: a first-person word in
the narration, a `facts` entry that narrates the screen or repeats the narration). A note keeps the record and
costs no second call; the instruction teaches those rules with good and bad lines instead, because a model asked
again over one word costs minutes per frame (the owner, 2026-09-28: "this is costing us too much ... instead of
validation just give example for good and bad"). What it cannot, whether the facts is accurate and whether a
diagram extends the earlier one faithfully, is a prompt rule on trust, stated in
`agent-context/instructions/frames_processing.md`.

The record is read from its markdown file (`services/record_file_service.py`), and every fix names the heading the
model writes the field under (`## Facts`, `## Screen`), since the field names are the program's, never the model's.
"""

from __future__ import annotations

import collections
import dataclasses
import difflib
import functools
import re
from typing import Callable
from pathlib import Path

import cv2
import numpy

from models import output_contract
from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from services import apple_text_service
from services import batch_summaries_service
from services import call_log_service
from services import frame_records_service
from services import phase_file_service
from services import critique_collection_service
from services import outline_reread_service
from services import record_file_service
from services import text_detection_service
from schemas import critique_record
from schemas import frame_record
from utils import announced_words_utils
from utils import critique_markdown_utils
from utils import frame_markdown_utils
from utils import instruction_example_utils
from utils import mermaid_text_utils
from utils import screenshot_suggestion_utils
from validators import chain_response_validator

# The fields that SAY something. A record with all four empty establishes nothing.
_SAYING_FIELDS = ("facts", "diagram", "screen", "speech_visual_alignment")
_SLUG_PREFIX = "frame-"
# The heading the model writes a frame's facts under, named where it is taught.
_FACTS = frame_record.FRAME_SECTIONS["facts"][0]
# A screen with at least this many pieces of meaningful text teaches something; a title card does not.
_TEXT_THAT_TEACHES = 3
# A slide's page number, a lone number of at most three digits: no text that teaches
_PAGE_NUMBER = re.compile(r"\d{1,3}")
# A fact this short ("The page is in German.") can be true of two frames by itself; a longer one copied word for word is
# another frame's
_COPIED_FACT_MIN_WORDS = 6
# A frame's outline quoting this many texts, this share of them in an earlier frame's outline, shows that frame's screen
SAME_SCREEN_MIN_QUOTES = 3
SAME_SCREEN_SHARE = 0.8
# A screen with fewer quoted texts than this, over a participant's question, may teach nothing of its own
_QUESTION_SCREEN_QUOTES = 8
# A frame with at most this many words spoken while it was shown is a transition (a loading screen, a window switch, a
# cut-off word or two): its `## Facts` may be `(none)`, because content on screen that nobody discusses is not facts.
_TRANSITION_WORDS = 8
# The frames before this one whose outlines count as what the notes already hold, as many as a frame's message hands it
_EARLIER_OUTLINE_FRAMES = 12
# A screen asks for a fact only when its outline quotes this many words the earlier frames' outlines do not
_NEW_WORDS_THAT_TEACH = 5
# An entry about the screen rather than the subject: "The slide shows ...", "The diagram displays ...", or about what
# the presenter does in front of it: "The presenter is initiating a login", "The user has logged into the portal".
_SCREEN_TALK = re.compile(r"^(?:the|this)\s+(?:(?:slide|screen|frame|image|page|screenshot|diagram|flowchart|chart|"
                          r"window|browser)\s+(?:shows|displays|contains|depicts|presents|lists|is showing)"
                          r"|(?:presenter|speaker|user)\s+(?:is|has|was|opens|clicks|navigates|logs|scrolls|types|shows)"
                          r")\b", re.IGNORECASE)
# REQUIREMENTS.md R15: the limits of the third-person narration a frame and a batch carry.
_THIRD_PERSON_DESCRIPTION_MAX_WORDS = 120
# REQUIREMENTS.md R17: the floor under the screen's outline. The screen itself and one part in it is the least any
# shared screen has, so one line is an answer the model did not write; how much of the frame it really carries is
# read, never counted.
_SCREEN_MIN_PARTS = 2
# An outline that sends the reader to another frame ("same as frame 5", "as in frame 9") outlines nothing: a later step
# shows one frame's notes without the others. Quoted screen text is left out before the search (`_QUOTED`).
_POINTS_TO_FRAME = re.compile(r"\b(?:same\s+as|as\s+(?:in|on)|like|identical\s+to|unchanged\s+from)\s+(?:in\s+)?frames?\s*(\d+)",
                              re.IGNORECASE)
# A step of a slide built up step by step outlines only what it adds, under the line its message gives it
_BUILD_STEP_LINE = re.compile(r"^- (?:a )?step (?:\d+ )?of the (?:slide|screen) of frame (?P<frame>\d+)", re.IGNORECASE)
_NOTHING_TO_READ = re.compile(r"\b(?:black|blank|empty|camera|webcam|video feed)\b", re.IGNORECASE)
# Three sentences tell the aim, the steps and the outcome; at five, an account of three sentences blocked ten accepted
# frames under zero repair, though its length is form and the frames hold the knowledge (2026-10-05)
SUMMARY_MIN_SENTENCES, SUMMARY_MAX_SENTENCES, SUMMARY_MAX_WORDS = 3, 10, 300
# Quoted text, straight or curly: speech quoted in the narration keeps its own person, and quoted screen text is no
# pointer to another frame
_QUOTED = re.compile(r'"[^"]*"|\u201c[^\u201d]*\u201d')
# "I" and "us" only as written: a capital "US" is a country, a lowercase "i" is a typo rather than a person.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# A table's vertical border the text reader reads as a bar at the start or end of a cell's line: Lecture 7's frame 136
# got "| Debugging and Profiling" among the program's added lines, and the critique named it a misread (DEFECTS 575);
# a bar inside the line, a shell pipe, stays
_TABLE_BORDER_AT_END = re.compile(r"^[|¦│]\s+|\s+[|¦│]$")


# The text reader's words below this confidence are left out: a meme's lettering, a logo, a shadow
_SCREEN_TEXT_MIN_CONFIDENCE = text_detection_service.SCREEN_TEXT_MIN_CONFIDENCE
# A line the text reader finds counts as quoted when this share of its words is in the outline's quotes
_LINE_QUOTED_SHARE = 0.7
# The outline must quote this share of the lines the text reader finds on the frame; one or two lines left out are never
# enough to refuse it, since the reader misreads a line now and then and two labels missed are a slip of form, not a
# loss of knowledge (the owner's bar)
_SCREEN_TEXT_MIN_SHARE = 0.9
# A take-away quoting words said over one of this many frames before it is refused, as one quoting the next frame's
_EARLIER_FRAMES = 3
_SCREEN_TEXT_MIN_MISSING = 3
# Every outline has the lines it leaves out added by the program, under a part that says the program read them: the
# worker cut long lists short at every batch size tried (8, 6, 4 frames) and asked again with its refusal, a board's rows
# by their titles alone, 7 of 13 rows; with half the lines as the floor, a batch grown to 11 frames by two builds wrote
# its outlines as a few lines (1 to 9 of 19 to 38 lines quoted) and blocked on 8 frames. The program reads every line,
# so it keeps them whatever the outline holds; FRAME_SCREEN_TEXT_MISSING stays as the guard should the reading fail
SCREEN_COMPLETION_MIN_SHARE = 0.0
SCREEN_COMPLETION_PART = "- text the program read on the frame that the parts above leave out (its reader may misread a letter):"
_WORD = re.compile(r"[^\W_]{3,}")
# A slide's header band (a section label, a logo) and footer band (date, company, page number) are form, not
# knowledge (the owner: a miss of form never stops the run); lines whose middle lies in them are not checked
_HEADER_SHARE = 0.09
_DATA_ROW_SHARE = 0.5
# A line of code (markup, an assignment) may be given within a block's line count and its first and last lines, as
# frames_processing.md teaches; a line holding this many of these marks is such a line
_CODE_MARKS = ("<", ">", "=", "{", "}", ";")
_CODE_MARK_COUNT = 2
# An identifier of code (camelCase, a dotted member) counts as a mark, and a line made largely of them is code: an
# editor's project tree is read joined with the code beside it ("mappers .getBasicConfigurat"), and the code may be
# given as a block (2026-10-05: three IDE frames refused for such lines). A slide line naming two camelCase products
# is read as code too, a small loss of form
_CODE_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*[A-Z]\w*|\b[A-Z][a-z]+[A-Z]\w*|\.[A-Za-z_]\w*")
_CODE_IDENTIFIER_SHARE = 0.4
# A declaration is code even when the reader drops its marks: an access word followed straight by a type or a modifier
_DECLARATION = re.compile(r"\b(?:public|private|protected)\s+(?:(?:final|static|abstract)\s+)*"
                          r"(?:[A-Z]\w*|void|int|long|boolean|class)\b|\bclass\b.*\bimplements\b")
# An editor's debugger repeats its own hints on every frame it is stopped in, as the browser's developer tools do: never
# a line to quote (2026-10-05: a section of twenty frames stopped at a breakpoint refused for them)
_DEBUGGER_HINT = re.compile(r"add to watches|hidden frames|not showing null elements|threads\s*&\s*(?:variables|console)"
                            r"|\bexpression:", re.IGNORECASE)
# A person's name in a chat app's or a meeting tool's list ("Lastname, Firstname", "(extern)") is the tool's own panel and
# personal data, never a miss: frames_processing.md keeps it out of the outline
_PERSON_NAME = re.compile(r"\b[A-ZÄÖÜ][\wäöüß-]+, [A-ZÄÖÜ][\wäöüß-]+|\((?:extern|external|Sie|you)\)", re.IGNORECASE)
_FOOTER_SHARE = 0.08
# The browser's developer tools are the browser's own panel, chrome as a meeting tool's participant list is: their
# controls, tabs and console tips read the same in every meeting. A read line most of whose words are these labels is
# never one to quote; the requests and messages the speaker shows are counted as any text
_DEVTOOLS_WORDS = frozenset((
    "elements console sources network performance memory application lighthouse recorder welcome "
    "preserve log disable cache throttling filter filters invert more hide data urls blocked "
    "fetch xhr doc css font img media manifest socket wasm other all "
    "name status type initiator size time waterfall fulfilled headers payload preview response timing cookies "
    "requests transferred resources finish domcontentloaded load selected characters "
    "explain errors using copilot edge click learn show again default levels hidden issues conditions top"
).split())
_DEVTOOLS_SHARE = 0.6
# A log is rows as a data table is: its line count, its first and last line and the lines the talk points at are quoted,
# as frames_processing.md teaches. A line opening with a date or a time, or made mostly of dotted class names (a log
# line wrapped onto the next), is such a row (2026-10-05: a forty-line log refused three times for quoting half of it)
_LOG_STAMP = re.compile(r"^\W*(?:\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{2,4}|\d{1,2}:\d{2}:\d{2})")
_DOTTED_NAME = re.compile(r"[\w$]+(?:\.[\w$@]+){2,}")
# A line that is only addresses (an email, a fediverse handle, a web address with its scheme) is no code and no log:
# "@korbi@mastodon.korbip.de" counted ".korbip" and ".de" as code members and its host as a dotted log name, was never
# listed for 38c3-openpv's frame 90, and the worker misread it unchecked (DEFECTS 628)
_ADDRESS_LINE = re.compile(r"^\s*(?:(?:@?[\w.+-]+@[\w-]+(?:\.[\w-]+)+|https?://\S+)[\s,;]*)+$")
_DOTTED_SHARE = 0.5
_NEARBY_FRAMES = 3
# A line in which no word is a word of the dictionary is text the reader could not read (code half covered by a popup
# read as "ntorde eate"): no outline can quote it. Without the system's word list every line counts. The playbook's own
# lists (`word-lists/`, German from Tesseract's model) are read beside it: with English alone a dialog's German title,
# "Wirtschaftlichkeit der Anlage berechnen", counted as unreadable and nothing asked for it (DEFECTS 623)
_DICTIONARY = Path("/usr/share/dict/words")
_WORD_LISTS_DIR = Path(__file__).resolve().parents[2] / "word-lists"
_LEAST_LISTED_WORD_LETTERS = 4
_NAME_PART = re.compile(r"[A-ZÄÖÜ]?[a-zäöüß]+|[A-ZÄÖÜ]+(?![a-zäöüß])")
# One part per outline line: its quoted text runs from the line's first quote mark to its last, so a quote mark inside
# the text (a German „ closed by a straight ") never shifts the pairing of the lines after it. Text in backticks is
# quoted too: a slide's legend line `XY = Extra yield` is written exactly in them
_LINE_QUOTE = re.compile(r'["`\u201c\u201e](.*)["`\u201d\u201c]')
# A vertical border running down most of the frame (an editor's project tree beside its code): the reader joins the
# words either side of it into one line no outline can quote, so a line is split where it crosses one (2026-10-05:
# six editor frames refused for "<folder> <code fragment>" lines). Measured on such a frame: the tree's border changes
# by more than 4 grey levels on 57% of the rows, no other column inside the frame's middle 90% on half of them
_BORDER_STEP = 4
_BORDER_SHARE = 0.5
# ... and it runs unbroken down this share of the height: dense terminal text changes on most rows of nearly every
# column, in runs of one text line (5% to 10% of the height), and every column read as a border, a side panel was named
# on a plain terminal and its lines split apart (froscon talk 3211, DEFECTS 213); real side panels' faint borders ran
# unbroken over 35% to 84%, a schematic's lines 19% to 28%
_BORDER_RUN_SHARE = 0.3
_BORDER_MARGIN = 0.05
_BORDER_SLACK = 3
# Running text: listed loose, a screenshot's small-print paragraph was summed up as "a text block starting with ..." by
# a worker that had quoted it whole one call before. A diagram's labels the reader put in one paragraph ran 2 to 5
# words a line, running text 10 to 15
_PARAGRAPH_MIN_LINES = 3
_PARAGRAPH_MIN_WORDS_PER_LINE = 8
# A side panel: listed loose among the page's lines, a wiki's page tree of ten entries was written as one line,
# "sidebar: left side", and refused. Its border stands in the frame's left third, with at least this many lines in it:
# a table's first column, left of its column border, held 4 and was no panel; the page tree held 12
_SIDE_PANEL_MAX_SHARE = 0.35
_SIDE_PANEL_MIN_LINES = 5
# ... and its lines run down most of the screen: a timeline slide's first lane, left of a lane border, held its boxes in
# a band a sixth of the height tall
_SIDE_PANEL_MIN_SPAN = 0.4
# A bar of short entries across the top (a browser's bookmarks, an application's menu) is window chrome, which the
# instructions write as one line with its items never listed: the check counts none of its lines and the message lists
# none (a bookmarks bar just below the band the check leaves out was refused twice, while the instructions forbade
# listing it). At least this many pieces of at most this many words on one row in the top fifth, spanning this share of
# the width; a slide's title on that row is one or two long pieces
_TOP_BAR_MIN_PIECES = 3
_TOP_BAR_MAX_WORDS = 4
_TOP_BAR_MAX_SHARE = 0.2
_TOP_BAR_MIN_SPAN = 0.4
_ROW_SLACK_PX = 6
# ... and a line in the top fifth that stands at the same height on a nearby frame, with most of its words, is chrome
# too: the bookmarks and a document program's ribbon stay while the content below them changes. The reader joined each
# such bar into one line of a few words, which no row of three pieces matched, and three frames of one batch were
# refused for not quoting them. Measured over a 43-frame recording: the bookmarks row at 9% of the height and the ribbon
# row at 13% to 15% repeated on the frames either side; the reader's words for one ribbon differed by a word or two
_STANDING_ROW_SLACK_SHARE = 0.02
_STANDING_ROW_WORD_SHARE = 0.75
# A word the text reader misread by a letter or two ("Orcer") still counts as the quoted word ("order")
_WORD_MATCH_RATIO = 0.75
# A word the reader cut where its box ends ("valid" of "validator", two boxes side by side read as one line) counts when
# it begins a quoted word and keeps this many letters
_CUT_WORD_MIN_LETTERS = 4
# A three-letter label misread by one letter ("new" for "neu", a digit read as a letter) is only 67% alike, under the
# ratio longer words meet; it counts when the other two letters stand in their places
_SHORT_WORD_LETTERS = 3
# A batch's title: a few words naming its own topic (the owner, 2026-10-03: "very focused on the topic in the batch, not
# the generic meeting topic ... as unique as possible")
# The instruction asks for about 4 to 8 words; the check allows up to 14: every title refused at 10 across the runs had
# 11 or 12 words (13 refusals, each a call spent), the model aiming at the limit and landing past it (2026-10-15)
_TITLE_MIN_WORDS, _TITLE_MAX_WORDS = 2, 14
# Two titles this alike, by their letters or by their words, name one topic: one batch's title repeated with a word
# changed is no title of its own
_TITLE_ALIKE_RATIO = 0.85
_TITLE_ALIKE_WORDS = 0.8
_TITLE_WORD = re.compile(r"[^\W_]+")


def _normalise_sentence(text: str) -> str:
    return " ".join(text.split()).casefold().rstrip(".")


def _is_empty(field_content: object) -> bool:
    if isinstance(field_content, str):
        return not field_content.strip()

    return not field_content


def _read_frame_items(paths: RunPaths) -> list[dict]:
    """The items of phase 2's frames file; none when the file is not there."""

    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    if not phase_file_service.has_phase_file(paths, frames_number):
        return []

    return phase_file_service.read_phase_file(paths, frames_number).get("items", [])


def read_frame_items(paths: RunPaths) -> list[dict]:
    """The items of phase 2's frames file, for a message that looks at the frame before one."""

    return _read_frame_items(paths)


def _find_frame_item(items: list[dict],
                     slug: str) -> dict | None:
    return next((entry for entry in items if entry.get("slug") == slug), None)


def _count_spoken_words(paths: RunPaths,
                        slug: str) -> int | None:
    """The words spoken while this frame was shown, from its item in the frames file; None when the item is not there,
    so a record judged without a frames file is judged as strictly as before."""

    item = _find_frame_item(_read_frame_items(paths), slug)

    return None if item is None else len(str(item.get("transcriptCurrent") or "").split())


# A take-away opens with the words it ties to the screen: `"<words>" → <part>: <what it tells>`
_TAKE_AWAY_QUOTE = re.compile(r'^\W*"([^"]+)"\s*(?:→|->)')
_MIN_QUOTE_WORDS = frame_markdown_utils.TAKE_AWAY_MIN_QUOTE_WORDS
_normalise_speech = frame_markdown_utils.normalise_speech


def _list_quotes_said_later(take_aways: list[str],
                            own_speech: str,
                            later_speech: str) -> list[str]:
    """The take-aways' quoted words that this frame's speech does not hold and the next frame's does: speech from a later
    frame written under an earlier one, which one run's critique collection named in 18 rules (2026-10-12)."""

    own, later = _normalise_speech(own_speech), _normalise_speech(later_speech)
    quotes = [match.group(1).strip() for line in take_aways if (match := _TAKE_AWAY_QUOTE.match(str(line)))]

    return [quote for quote in quotes if len(_normalise_speech(quote).split()) >= _MIN_QUOTE_WORDS
            and _normalise_speech(quote) not in own and _normalise_speech(quote) in later]


def _is_partly_said(quote: str,
                    speech: str) -> bool:
    """Whether a run of _MIN_QUOTE_WORDS of the quote's words stands in `speech`, already normalised."""

    words = _normalise_speech(quote).split()

    return any(" ".join(words[start:start + _MIN_QUOTE_WORDS]) in speech
               for start in range(max(1, len(words) - _MIN_QUOTE_WORDS + 1)))


def _list_quotes_said_before(take_aways: list[str],
                             own_speech: str,
                             earlier_speech: str) -> list[str]:
    """The take-aways' quoted words said over one of the frames before this one, no run of _MIN_QUOTE_WORDS of them in
    this frame's own speech: a frame with no speech of its own quoted what the speaker had said two frames earlier, and
    the next-frame check let it through (a critique named it)."""

    own, earlier = _normalise_speech(own_speech), _normalise_speech(earlier_speech)
    # Every quote in the line, not only one opening it: the frame that did it wrote its take-away as a sentence with
    # the quote inside
    quotes = [quote.strip() for line in take_aways for quote in re.findall(r'"([^"]+)"', str(line))]

    return [quote for quote in quotes if len(_normalise_speech(quote).split()) >= _MIN_QUOTE_WORDS
            and _normalise_speech(quote) in earlier and not _is_partly_said(quote, own)]


def _list_quotes_on_screen(quotes: list[str],
                           screen) -> list[str]:
    """The quotes the frame's own outline also quotes: the slide's text, which the speaker reads out only over the next
    frame. Told only "said over the next frame", a worker kept the quote on two tries (2026-10-12)."""

    text = _normalise_speech("\n".join(screen) if isinstance(screen, list) else str(screen or ""))
    return [quote for quote in quotes if _normalise_speech(quote) in text]


def _list_screen_quotes_unsaid(take_aways: list[str],
                               own_speech: str,
                               screen) -> list[str]:
    """The take-aways' quoted words that the frame's own outline quotes and its speech does not hold: the slide's text
    written where the speaker's words belong. On one lecture 30 of 172 frames did it, with the rule in the instructions
    (2026-10-15)."""

    own = _normalise_speech(own_speech)
    quotes = [match.group(1).strip() for line in take_aways if (match := _TAKE_AWAY_QUOTE.match(str(line)))]

    return _list_quotes_on_screen([quote for quote in quotes if len(_normalise_speech(quote).split()) >= _MIN_QUOTE_WORDS
                                   and _normalise_speech(quote) not in own], screen)


def _read_outline_text(outline) -> str:
    """A record's `## Screen` as one text, whether stored as a string or as lines."""

    return outline if isinstance(outline, str) else " ".join(str(line) for line in outline or [])


def _find_misplaced_quotes(paths: RunPaths,
                           slug: str,
                           record: dict) -> tuple[list[str], list[str], list[str]]:
    """The take-aways' quotes that are the frame's own screen text, those said over the next frame, and those said over
    one of the frames before it, each list leaving out what an earlier one holds; three empty lists for a frame the
    frames file does not hold."""

    items = sorted(_read_frame_items(paths), key=lambda entry: entry.get("index", 0))
    position = next((number for number, entry in enumerate(items) if entry.get("slug") == slug), None)
    if position is None:
        return [], [], []
    take_aways = list(record.get("take_aways") or [])
    own_speech = str(items[position].get("transcriptCurrent") or "")
    later_speech = str(items[position + 1].get("transcriptCurrent") or "") if position + 1 < len(items) else ""
    on_screen = _list_screen_quotes_unsaid(take_aways, own_speech, record.get("screen"))
    later = [quote for quote in _list_quotes_said_later(take_aways, own_speech, later_speech) if quote not in on_screen]
    # Words said over the frames before it; a repeat is left out, since its message hands it the earlier showing's words
    earlier_speech = " ".join(str(entry.get("transcriptCurrent") or "") for entry in items[max(0, position - _EARLIER_FRAMES):position])
    # A quote the frame's own outline holds is its screen's text, even when said over an earlier frame too: a summary
    # line naming a slide's new label ("What is the best coffee machine") was refused as earlier speech (froscon, 19)
    screen = _read_outline_text(record.get("screen")).casefold()
    # ... and a quote announcing this frame's screen is about it: said just before the slide showed what it names, the
    # program's own announced-words rule gives it to this frame (38c3-797 frames 50 and 55, the circle in AGENTS.md)
    earlier_screens = [_read_outline_text((entry.get("record") or {}).get("screen"))
                       for entry in items[max(0, position - _EARLIER_FRAMES):position]]
    earlier = [quote for quote in _list_quotes_said_before(take_aways, own_speech, earlier_speech)
               if quote not in on_screen and quote not in later and quote.casefold() not in screen
               and not announced_words_utils.is_announcing(quote, screen, earlier_screens)] if not _is_repeat(paths, slug) else []

    return on_screen, later, earlier


# Number words of the two languages the recordings are in, read as their digits so "zwei" and "2" are one number
_NUMBER_WORDS = {**{word: str(number) for number, word in enumerate(
                    "zero one two three four five six seven eight nine ten eleven twelve".split())},
                 **{word: str(number) for number, word in enumerate(
                    "null eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf".split())},
                 "ein": "1", "eine": "1", "twenty": "20", "zwanzig": "20"}
_NUMBER_TOKEN = re.compile(r"^\d+$")
_OPENING_QUOTE = re.compile(r'^\W*"([^"]+)"')


def _read_numbers_as_digits(text: str) -> list[str]:
    return [_NUMBER_WORDS.get(word, word) for word in _normalise_speech(text).split()]


def _list_quotes_with_changed_number(take_aways: list[str],
                                     own_speech: str) -> list[str]:
    """The quotes opening a take-away that are the frame's speech with a number changed: every word but a number
    stands in the speech in that order, and a number does not. A worker quoted "4 Analog-Ausgänge mit
    4096-Stufenauflösung" where "zwei" was said, its own doubt written after the quote (froscon talk 3211, frame 5);
    over 1,301 opening quotes of 13 public runs this names that one and a lecture's "1, 3" for "1 3 3"."""

    speech = _read_numbers_as_digits(own_speech)
    spoken, masked = " ".join(speech), " ".join("#" if _NUMBER_TOKEN.match(word) else word for word in speech)
    changed = []
    for line in take_aways:
        match = _OPENING_QUOTE.match(str(line))
        words = _read_numbers_as_digits(match.group(1)) if match else []
        if len(words) < 3 or not any(_NUMBER_TOKEN.match(word) for word in words) or " ".join(words) in spoken:
            continue
        if " ".join("#" if _NUMBER_TOKEN.match(word) else word for word in words) in masked:
            changed.append(match.group(1).strip())

    return changed


def drop_misplaced_take_aways(paths: RunPaths,
                              slug: str,
                              record: dict) -> dict:
    """The record without the take-away lines quoting words said over the next frame or the frames before it, when at
    least one line remains; the frame that heard the words writes them. A check and four instruction changes did not
    stop it: a build step with no words of its own quoted its first step's, and blocked a batch (Lecture 9, frame 75).
    A line opening with the screen's own text unsaid goes too: kept for the check, it blocked a run under zero repair
    while the frame's other lines stood (froscon, frame 19). So does a line quoting its speech with a number changed
    (`_list_quotes_with_changed_number`). A frame whose every line is misplaced loses them all: left to the check, a
    board step's one take-away quoting the summary said over the next frame blocked a batch under zero repair, though
    the frame that heard the words writes them (Lecture 7 frame 44, DEFECTS 556)."""

    on_screen, later, earlier = _find_misplaced_quotes(paths, slug, record)
    item = _find_frame_item(_read_frame_items(paths), slug)
    misquoted = _list_quotes_with_changed_number(list(record.get("take_aways") or []),
                                                 str((item or {}).get("transcriptCurrent") or ""))
    misplaced = on_screen + later + earlier + misquoted
    lines = list(record.get("take_aways") or [])
    kept = [line for line in lines if not any(f'"{quote}"' in str(line) for quote in misplaced)]
    if not misplaced or len(kept) == len(lines):
        return record

    return {**record, "take_aways": kept}


# A take-away line's opening speech quotes, up to its arrow: `"<words>" / "<more words>" → <part>`
_OPENING_QUOTES = re.compile(r'^(\W*(?:"[^"]+"\s*/?\s*)+)(?:→|->)')
# How much of the speech before the frame is searched for a quote, in characters
_BEFORE_SPEECH_CHARS = 2000
# The shortest quote judged said or not: "drains laptop batteries", three words, was made up
_LEAST_UNSAID_QUOTE_WORDS = 3


# The pieces a quote is stitched from: "we have length ... or max ... and min"
_QUOTE_PIECE_BREAK = re.compile(r"\s*(?:\.\.\.|…)\s*")


def _is_piece_unsaid(quote: str,
                     speech: str) -> bool:
    """Whether `quote` is stitched from pieces and `speech` (normalised) lacks one of them whole: a quote stitched from
    said words and a slide's text passed as partly said (froscon 3147 frame 156's "... and Min", the speaker saying "and
    so on"; 36 of 172 stitched quotes over 20 public runs, most a slide's text, DEFECTS 354)."""

    pieces = [_normalise_speech(piece) for piece in _QUOTE_PIECE_BREAK.split(quote)]
    pieces = [piece for piece in pieces if piece]

    return len(pieces) > 1 and any(piece not in speech for piece in pieces)


def _drop_unsaid_quote_marks(line: str,
                             speech: str) -> str:
    """The take-away line with each opening quote no run of whose words `speech` (normalised) holds, or one stitched from
    a piece it lacks, written bare."""

    opening = _OPENING_QUOTES.match(line)
    if not opening:
        return line
    quotes = opening.group(1)
    for quote in re.findall(r'"([^"]+)"', quotes):
        if (len(_normalise_speech(quote).split()) >= _LEAST_UNSAID_QUOTE_WORDS and not _is_partly_said(quote, speech)
                or _is_piece_unsaid(quote, speech)):
            quotes = quotes.replace(f'"{quote}"', quote, 1)

    return quotes + line[len(opening.group(1)):]


def drop_quote_marks_of_unsaid_words(paths: RunPaths,
                                     slug: str,
                                     record: dict) -> dict:
    """The record with each take-away quote of speech that no one said, no run of _MIN_QUOTE_WORDS of its words in the
    speech of this frame, the next, the frames before it or the speech before it (a quote shorter than _MIN_QUOTE_WORDS
    whole), written without its quote marks: the
    worker's paraphrase or a slide's text, put in quotes as the speaker's words ("drains laptop batteries" where the
    speaker said "not exactly what your laptop battery is signed up for"), would reach the document as a quote (DEFECTS
    325). Over the public runs 201 of 1,425 opening speech quotes of 3 words or more were such."""

    items = sorted(_read_frame_items(paths), key=lambda entry: entry.get("index", 0))
    position = next((number for number, entry in enumerate(items) if entry.get("slug") == slug), None)
    if position is None or not record.get("take_aways"):
        return record
    near = [str(entry.get("transcriptCurrent") or "") for entry in items[max(0, position - _EARLIER_FRAMES):position + 2]]
    speech = _normalise_speech(" ".join(near) + " " + str(items[position].get("transcriptBefore") or "")[-_BEFORE_SPEECH_CHARS:])

    lines = [_drop_unsaid_quote_marks(str(line), speech) for line in record["take_aways"]]

    return record if lines == list(record["take_aways"]) else {**record, "take_aways": lines}


def _check_speech_quotes(report: ValidationReport,
                         paths: RunPaths,
                         slug: str,
                         record: dict) -> None:
    report.mark_ran("FRAME_SPEECH_FROM_NEXT_FRAME")
    on_screen, later, earlier = _find_misplaced_quotes(paths, slug, record)
    if on_screen:
        report.add("FRAME_SPEECH_FROM_NEXT_FRAME",
                   f"{slug}: these quoted words are this frame's screen text, and the speaker does not say them over "
                   "this frame: " + "; ".join(repr(quote) for quote in on_screen),
                   fix="a take-away opens with words SAID over this frame, never with the screen's own text: quote "
                       "the words under \"Spoken from this frame until the next one\" that are about that part, or "
                       "leave the part out of the take-aways; its text stays quoted in ## Screen")
    if later:
        report.add("FRAME_SPEECH_FROM_NEXT_FRAME",
                   f"{slug}: these quoted words are said over the next frame, not this one: "
                   + "; ".join(repr(quote) for quote in later),
                   fix="tie to this frame only the words under \"Spoken from this frame until the next one\"; take the "
                       "take-away and every fact built on those words out, the next frame writes them")
    if earlier:
        report.add("FRAME_SPEECH_FROM_NEXT_FRAME",
                   f"{slug}: these quoted words are said over an earlier frame, not this one: "
                   + "; ".join(repr(quote) for quote in earlier),
                   fix="tie to this frame only the words under \"Spoken from this frame until the next one\"; a frame "
                       "with no words of its own has no take-away from speech, and the earlier frame holds them")


def _is_repeat(paths: RunPaths,
               slug: str) -> bool:
    """Whether the frame shows again a screen an earlier frame showed (`repeatOf` in the frames file)."""

    return bool((_find_frame_item(_read_frame_items(paths), slug) or {}).get("repeatOf"))


def _list_build_frames(paths: RunPaths,
                       slug: str) -> set[int]:
    """For a step of a slide built up step by step (`stepOf` in the frames file): the frame numbers of its build up to
    it, and of the screens its frames repeat, which its outline may point to. Empty for any other frame, or when the frames file is not there."""

    items = _read_frame_items(paths)
    item = _find_frame_item(items, slug)
    start = (item or {}).get("stepOf")
    if not start:
        return set()

    build = [entry for entry in items
             if (entry.get("slug") == start or entry.get("stepOf") == start) and int(entry["index"]) < int(item["index"])]
    # A build whose first frame shows again an earlier screen holds that screen too: a step pointed at the frame the
    # build's first frame repeats ("same as frame 5", its build starting at frame 7, a repeat of 5), and was blocked
    repeated = {str(entry.get("repeatOf")) for entry in build if entry.get("repeatOf")}

    return ({int(entry["index"]) for entry in build}
            | {int(entry["index"]) for entry in items if entry.get("slug") in repeated})


def _list_repeated_frames(paths: RunPaths,
                          slug: str) -> set[int]:
    """For a frame showing again a screen an earlier frame showed (`repeatOf`): that frame's number, and the frames of
    its build when it is a build's step, which its outline may point to. Empty for any other frame."""

    items = _read_frame_items(paths)
    repeated = str((_find_frame_item(items, slug) or {}).get("repeatOf") or "")
    numbers = {int(entry["index"]) for entry in items if entry.get("slug") == repeated}
    # A repeat of a build's later step points at that step, whose outline holds only what it added: the build's other
    # frames hold the rest of the screen, so they count as the frame it repeats
    return numbers | (_list_build_frames(paths, repeated) if repeated else set())


def _is_code_line(text: str) -> bool:
    identifiers = len(_CODE_IDENTIFIER.findall(text))

    return (sum(text.count(mark) for mark in _CODE_MARKS) + identifiers >= _CODE_MARK_COUNT
            or identifiers >= _CODE_IDENTIFIER_SHARE * max(1, len(_list_words(text)))
            or bool(_DECLARATION.search(text)))


@functools.cache
def _read_dictionary() -> frozenset[str]:
    """The system's word list, folded, read once; empty when the machine has none."""

    try:
        return frozenset(word.strip().casefold() for word in _DICTIONARY.read_text(errors="ignore").splitlines())
    except OSError:
        return frozenset()


@functools.cache
def _read_playbook_words() -> frozenset[str]:
    """The playbook's own word lists (`word-lists/*.txt`), folded, read once."""

    words: set[str] = set()
    for path in sorted(_WORD_LISTS_DIR.glob("*.txt")):
        words.update(word.strip().casefold() for word in path.read_text(encoding="utf-8", errors="ignore").splitlines())

    return frozenset(words)


def _is_unreadable(text: str) -> bool:
    """A line of two parts or more, none of them a word: of the system's list at three letters, or of the playbook's
    lists at `_LEAST_LISTED_WORD_LETTERS`, since short German words ("der", "de") matched misread fragments ("ile isk",
    "States: Adde:12 de Be") on public frames, while every German line looked at held a longer word."""

    dictionary, listed = _read_dictionary(), _read_playbook_words()
    parts = [part.casefold() for token in re.findall(r"[A-Za-zÄÖÜäöüß]+", text) for part in _NAME_PART.findall(token)
             if len(part) >= 3]

    return (bool(dictionary or listed) and len(parts) >= 2 and not any(
        part in dictionary or (len(part) >= _LEAST_LISTED_WORD_LETTERS and part in listed) for part in parts))


def _is_log_row(text: str) -> bool:
    dotted = sum(len(match) for match in _DOTTED_NAME.findall(text))

    return bool(_LOG_STAMP.match(text)) or dotted >= _DOTTED_SHARE * len(text.replace(" ", ""))


def _list_words(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text)]


def _list_quoted_words(outline: str) -> set[str]:
    return {word for line in outline.splitlines() for match in [_LINE_QUOTE.search(line)] if match
            for word in _list_words(match.group(1))}


def _list_markdown_words(record: dict) -> set[str]:
    """The words the record's `## As markdown` writes: a table, a list or code copied there is quoted exactly, and
    asked to quote it again under `## Screen`, a PDF page's outline wrote every row of every table a second time
    (pdf-02: 35 outline lines a page)."""

    return set(_list_words(str(record.get("as_markdown") or "")))


def _join_words(words: list[dict]) -> str:
    return " ".join(word["text"] for word in sorted(words, key=lambda word: word["box"][0]))


def _count_longest_run(flags) -> int:
    """The most rows in a row that `flags` holds true."""

    padded = numpy.concatenate([[0], flags.astype(numpy.int8), [0]])
    edges = numpy.flatnonzero(numpy.diff(padded))

    return int((edges[1::2] - edges[::2]).max()) if len(edges) else 0


def _find_pane_borders(image) -> list[int]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype("int16")
    height, width = gray.shape
    steps = abs(gray[:, 1:] - gray[:, :-1]) > _BORDER_STEP
    share = steps.sum(axis=0) / height

    return [x for x in range(int(_BORDER_MARGIN * width), int((1 - _BORDER_MARGIN) * width))
            if share[x] >= _BORDER_SHARE and _count_longest_run(steps[:, x]) >= _BORDER_RUN_SHARE * height]


def _split_at_borders(words: list[dict],
                      borders: list[int]) -> list[list[dict]]:
    pieces = [[words[0]]] if words else []
    for left, right in zip(words, words[1:]):
        gap = (left["box"][2] - _BORDER_SLACK, right["box"][0] + _BORDER_SLACK)
        if any(gap[0] <= border <= gap[1] for border in borders):
            pieces.append([right])
        else:
            pieces[-1].append(right)

    return pieces


def _find_top_bar(pieces: list[dict]) -> list[dict]:
    """The pieces of the first row in the top fifth that is a bar of short entries, or none."""

    for piece in sorted(pieces, key=lambda each: each["y"]):
        if piece["y"] > _TOP_BAR_MAX_SHARE * piece["height"]:
            break
        row = [other for other in pieces if abs(other["y"] - piece["y"]) <= _ROW_SLACK_PX]
        if (len(row) >= _TOP_BAR_MIN_PIECES and all(len(other["text"].split()) <= _TOP_BAR_MAX_WORDS for other in row)
                and max(other["x1"] for other in row) - min(other["x0"] for other in row) >= _TOP_BAR_MIN_SPAN * piece["width"]):
            return sorted(row, key=lambda other: other["x0"])

    return []


def _is_standing_row(piece: dict,
                     nearby_pieces: list[dict]) -> bool:
    """Does this line of the top fifth stand at the same height, with most of its words, on a nearby frame?"""

    words = set(_list_words(piece["text"]))
    if not words or piece["y"] > _TOP_BAR_MAX_SHARE * piece["height"]:
        return False

    return any(abs(other["y"] / other["height"] - piece["y"] / piece["height"]) <= _STANDING_ROW_SLACK_SHARE
               and len(words & set(_list_words(other["text"]))) >= _STANDING_ROW_WORD_SHARE * len(words)
               for other in nearby_pieces)


def _read_nearby_top_pieces(paths: RunPaths,
                            slug: str) -> list[dict]:
    """The lines of the top fifth of the frames up to `_NEARBY_FRAMES` either side."""

    frame_number = int(slug.removeprefix(_SLUG_PREFIX))
    numbers = set(range(max(0, frame_number - _NEARBY_FRAMES), frame_number + _NEARBY_FRAMES + 1)) - {frame_number}

    return [piece for number in sorted(numbers) for piece in _read_screen_pieces(paths, f"{_SLUG_PREFIX}{number:03d}")
            if piece["y"] <= _TOP_BAR_MAX_SHARE * piece["height"]]


def _is_screen_tall(pieces: list[dict]) -> bool:
    return max(piece["y"] for piece in pieces) - min(piece["y"] for piece in pieces) >= _SIDE_PANEL_MIN_SPAN * pieces[0]["height"]


def _is_running_text(lines: list[str]) -> bool:
    return (len(lines) >= _PARAGRAPH_MIN_LINES
            and sum(len(line.split()) for line in lines) >= _PARAGRAPH_MIN_WORDS_PER_LINE * len(lines))


# The text reader reads a capital I standing alone as a bar: "(for good reasons), | don't use Rust", and the notes
# quoted it so (DEFECTS 336). A line whose every bar stands alone before a word that follows I (don't, have) is prose;
# over the public runs' frame messages 3 lines are such, each one, and the 62 holding a bar beside table bars stay
_BAR_PRONOUN = re.compile(r"(?<=[a-z,;)] )\|(?= (?:am|was|do|don't|did|didn't|have|had|haven't|will|would|can|can't|could|"
                          r"think|thought|use|used|miss|know|want|wanted|need|also|just|really|guess|see|mean)\b)")


def _fix_bar_pronoun(line: str) -> str:
    """`line` with each lone bar read for the word I written I, when every bar of it is one."""

    return _BAR_PRONOUN.sub("I", line) if line.count("|") == len(_BAR_PRONOUN.findall(line)) else line


def _read_screen_pieces(paths: RunPaths,
                        slug: str) -> list[dict]:
    """Each line the check counts, in reading order, with its reader paragraph, its left and right edge, and the
    frame's width and pane borders."""

    if not text_detection_service.is_available():
        return []
    item = _find_frame_item(_read_frame_items(paths), slug)
    frame_path = paths.output_root / str((item or {}).get("path") or "")
    if not item or not item.get("path") or not frame_path.is_file():
        return []
    image = cv2.imread(str(frame_path))
    if image is None:
        return []
    height, width = image.shape[:2]
    lines: dict[int, list[dict]] = {}
    # A board sent as its text (`boardHandler` text-only-integration) is checked against the words it was sent, the
    # board reader's, never a second reading; a frame stored shrunk, against the words phase 2 read on the recording
    # at its own size, where a small code font is still readable (DEFECTS 41, 69)
    words = (item.get("boardWords") or item.get("screenWords")
             or text_detection_service.find_words(frame_path, _SCREEN_TEXT_MIN_CONFIDENCE))
    for word in words:
        lines.setdefault(word["line"], []).append(word)
    borders = _find_pane_borders(image)
    pieces = [{"paragraph": words[0].get("block"), "text": _fix_bar_pronoun(" ".join(word["text"] for word in piece)),
               "x0": min(word["box"][0] for word in piece), "x1": max(word["box"][2] for word in piece),
               "y": sum(word["box"][1] + word["box"][3] for word in piece) / (2 * len(piece)),
               "width": width, "height": height, "borders": borders}
              for words in lines.values()
              if _HEADER_SHARE * height <= sum(word["box"][1] + word["box"][3] for word in words) / (2 * len(words))
              <= (1 - _FOOTER_SHARE) * height
              for piece in _split_at_borders(sorted(words, key=lambda word: word["box"][0]),
                                             [] if _is_log_row(_join_words(words)) else borders)]

    # A line of a data table (dates, codes, amounts) may be given as a count with its first and last row, as frames_processing.md
    # teaches; a line with a digit in half of its words or more, or one written only in capitals (security names and
    # codes in a list), is such a row, and is not checked; so is a line of code, and a line of a log
    return [piece for piece in pieces if len(_list_words(piece["text"])) >= 2
            and sum(any(character.isdigit() for character in word) for word in _list_words(piece["text"]))
            < _DATA_ROW_SHARE * len(_list_words(piece["text"]))
            and not all(word.isupper() for word in _WORD.findall(piece["text"]) if any(character.isalpha() for character in word))
            and (_ADDRESS_LINE.match(piece["text"]) or not _is_code_line(piece["text"]))
            and not _DEBUGGER_HINT.search(piece["text"])
            and not _PERSON_NAME.search(piece["text"])
            and (_ADDRESS_LINE.match(piece["text"]) or not _is_log_row(piece["text"]))
            and not _is_unreadable(piece["text"])
            and sum(word in _DEVTOOLS_WORDS for word in _list_words(piece["text"]))
            < _DEVTOOLS_SHARE * len(_list_words(piece["text"]))]


def _is_one_letter_off(word: str,
                       other: str) -> bool:
    return len(other) == len(word) and sum(left != right for left, right in zip(word, other)) == 1


def _is_word_quoted(word: str,
                    quoted: set[str]) -> bool:
    return (word in quoted or bool(difflib.get_close_matches(word, quoted, n=1, cutoff=_WORD_MATCH_RATIO))
            or (len(word) >= _CUT_WORD_MIN_LETTERS and any(other.startswith(word) for other in quoted))
            or (len(word) == _SHORT_WORD_LETTERS and any(_is_one_letter_off(word, other) for other in quoted)))


def _check_screen_text(report: ValidationReport,
                       paths: RunPaths,
                       slug: str,
                       record: dict,
                       build_frames: set[int]) -> None:
    """R17: the outline quotes the text on the frame. The text reader's lines are compared with the words quoted under
    `## Screen` (with the outlines of up to three frames either side, its build and the frame it repeats); a summary such as "bullet points
    about the goals" quotes none of them. Measured on one batch: the frames judged right quoted 91% to 100% of their
    lines, the frames that summed their text up 11% to 75%."""

    lines, missing = _list_unquoted_lines(paths, slug, record, build_frames)
    if not lines:
        return
    report.mark_ran("FRAME_SCREEN_TEXT_MISSING")
    if len(missing) >= _SCREEN_TEXT_MIN_MISSING and (len(lines) - len(missing)) / len(lines) < _SCREEN_TEXT_MIN_SHARE:
        report.add("FRAME_SCREEN_TEXT_MISSING",
                   f"frame {record.get('frame')}'s `## Screen` quotes {len(lines) - len(missing)} of the {len(lines)} "
                   f"lines of text on the frame. Not quoted (as the program's text reader read them, misreadings "
                   f"included): " + "; ".join(f'"{line}"' for line in missing[:20]),
                   fix="find each of these texts on the frame and outline it as a part with its text quoted whole, "
                       "spelt as the IMAGE shows it, never as listed here, under the part it sits in: every bullet, "
                       "label, box, header and footer line; a paragraph of small print inside a screenshot is one part, "
                       "`paragraph \"<every sentence>\"`. A summary such as \"bullet points about ...\" quotes "
                       "nothing")


def _read_quoted_share(line: str,
                       quoted: set[str]) -> float:
    """The share of the line's words its notes quote."""

    words = _list_words(line)

    return sum(_is_word_quoted(word, quoted) for word in words) / len(words)


def _list_unquoted_lines(paths: RunPaths,
                         slug: str,
                         record: dict,
                         build_frames: set[int]) -> tuple[list[str], list[str]]:
    """The lines the text reader finds on the frame, and those its notes do not quote, with the outlines of up to three
    frames either side, its build and the frame it repeats."""

    lines = read_screen_lines(paths, slug)
    if not lines:
        return [], []
    outlines = [str(record.get("screen") or "")]
    # A line a nearby frame quoted is not lost when this frame sums the same screen up (the owner's bar: no loss of
    # knowledge stops the run, a repeated screen outlined short does not). Nearby is up to three frames either way: a
    # dialog opened over an editor for a moment left the editor out three times, and the frames around it held it whole
    frame_number = int(slug.removeprefix(_SLUG_PREFIX))
    nearby = set(range(max(0, frame_number - _NEARBY_FRAMES), frame_number + _NEARBY_FRAMES + 1)) - {frame_number}
    for number in sorted(build_frames | nearby):
        try:
            outlines.append(str(record_file_service.read_frame_record(paths, f"{_SLUG_PREFIX}{number:03d}").get("screen") or ""))
        except (OSError, ValueError):
            continue
    quoted = _list_quoted_words("\n".join(outlines)) | _list_markdown_words(record)
    missing = [line for line in lines if _read_quoted_share(line, quoted) < _LINE_QUOTED_SHARE]

    return lines, missing


def _check_diagram(report: ValidationReport,
                   slug: str,
                   record: dict,
                   build_frames: set[int] = frozenset()) -> None:
    """The diagram is Mermaid, of a flow and never a chart, and draws."""

    report.mark_ran("FRAME_DIAGRAM_NOT_MERMAID")
    diagram = mermaid_text_utils.strip_fence(str(record.get("diagram") or ""))
    pointers = list(_POINTS_TO_FRAME.finditer(diagram))
    points_only_to_build = bool(build_frames) and bool(pointers) and all(int(match.group(1)) in build_frames
                                                                         for match in pointers)
    if diagram and diagram.startswith(frame_record.EMPTY_MARK):
        # The empty mark with a reason after it ("(none) - same as the last frame"): the worker read "nothing to draw"
        # as "nothing new to draw", so the fix says which one it means.
        report.add("FRAME_DIAGRAM_NOT_MERMAID", f"{slug}: `## Diagram` holds `{frame_record.EMPTY_MARK}` with a reason "
                   "after it", fix=f"write `{frame_record.EMPTY_MARK}` alone only when the screen and the speech show "
                                   "no flow, architecture or sequence at all; a diagram an earlier frame drew that is "
                                   "on screen again, whole, marked or in part, is written again, extended")
    elif diagram and diagram.split()[0] in mermaid_text_utils.CHART_KINDS:
        report.add("FRAME_DIAGRAM_NOT_MERMAID", f"{slug}: `## Diagram` draws a {diagram.split()[0]!r} chart, and "
                   "`## Diagram` is for a flow, an architecture, a call sequence or a state change",
                   fix=f"write the chart's values under `## {_FACTS}`, each with its label and unit, and outline the chart "
                       f"under `## Screen`; write `{frame_record.EMPTY_MARK}` under `## Diagram` unless a flow is also shown")
    # A step of a built slide whose diagram is its build's adds nothing to draw, read as `(none)`
    elif diagram and not points_only_to_build and not mermaid_text_utils.is_mermaid(diagram):
        report.add("FRAME_DIAGRAM_NOT_MERMAID", f"{slug}: `## Diagram` starts with {diagram.split()[0]!r}, not a "
                   f"Mermaid diagram type", fix="start the diagram with a Mermaid type such as `flowchart LR` or "
                                                "`sequenceDiagram`, inside a ```mermaid fence, or leave `## Diagram` out")

    # a diagram Mermaid cannot draw is refused where it is written: one passed here and was copied into a chapter
    report.mark_ran("FRAME_DIAGRAM_SYNTAX")
    problems = mermaid_text_utils.find_syntax_problems(diagram) if diagram and mermaid_text_utils.is_mermaid(diagram) else []
    if problems:
        report.add("FRAME_DIAGRAM_SYNTAX", f"{slug}: Mermaid cannot draw `## Diagram`: {'; '.join(problems)}",
                   fix="write one statement per line, close every quote and bracket on its line, use a direction of TB, "
                       "TD, BT, LR or RL, and give each group a name no box of the diagram has")


def _check_tags(report: ValidationReport,
                slug: str,
                record: dict) -> None:
    """The record names its tags; a record without them is noted, never refused."""

    # Tags are how phase 4 finds this moment again, and the program cannot invent them. A refusal of a frame with no
    # tags blocked a whole recording's phase 3 on one frame whose reply the host had cut (the hard-fix rule): the
    # frame's other notes stand, phase 4 still finds it by its time and its text, and the operator reads the note.
    report.mark_ran("FRAME_TAGS_MISSING")
    if not [tag for tag in record.get("tags") or [] if str(tag).strip()]:
        report.note("FRAME_TAGS_MISSING", f"{slug}: `## Tags` is empty",
                   fix="name three to ten tags under `## Tags`, separated by commas: every system, tool, component, "
                       "standard or term this moment establishes something about, plus the kinds of facts it gives")


# A text a fact quotes, in backticks or double quotes
_FACT_QUOTE = re.compile(r"`([^`]+)`|\"([^\"]+)\"")


def _is_fact_shown_by_own_screen(fact: str,
                                 screen: str) -> bool:
    """Whether the fact quotes at least one text and this frame's outline holds every text it quotes: then the fact is
    this frame's own, though an earlier frame holds it too (an answer said over one frame and printed by the console on
    the next, lecture-1 frames 105 and 107, 2026-10-13). A fact quoting nothing is never excused this way."""

    quotes = [first or second for first, second in _FACT_QUOTE.findall(fact)]

    return bool(quotes) and all(quote.casefold() in screen for quote in quotes)


def _is_same_screen(screen: str,
                    owner_screen: str) -> bool:
    """Whether this frame's outline quotes at least SAME_SCREEN_MIN_QUOTES texts and the owner's outline holds
    SAME_SCREEN_SHARE of them: the same screen shown again, whose facts are the owner's facts. Phase 2 left a slide
    shown again unmarked when its screen box moved by a pixel, and the frame, refused for restating the slide's facts
    and told to state what its own screen shows, could do neither (lecture-1 frames 134 and 144, 2026-10-14)."""

    quotes = {quote.casefold() for quote in frame_markdown_utils.list_screen_quotes(screen)}
    held = {quote.casefold() for quote in frame_markdown_utils.list_screen_quotes(owner_screen)}

    return len(quotes) >= SAME_SCREEN_MIN_QUOTES and len(quotes & held) >= SAME_SCREEN_SHARE * len(quotes)


def _list_copied_facts(paths: RunPaths,
                       slug: str,
                       record: dict) -> list[tuple[str, int]]:
    """Each fact of a frame not yet accepted that an accepted frame holds word for word, with that frame's number,
    unless this frame's own screen shows it or both frames show the same screen."""

    accepted = frame_records_service.read_frame_records(paths)
    if any(entry.get("slug") == slug for entry in accepted):
        return []
    owners = {_normalise_sentence(str(fact)): int(entry["index"]) for entry in accepted
              for fact in entry.get("facts") or [] if len(str(fact).split()) >= _COPIED_FACT_MIN_WORDS}
    screen = str(record.get("screen") or "").casefold()
    screens = {int(entry["index"]): str(entry.get("screen") or "") for entry in accepted}

    return [(str(fact), owners[_normalise_sentence(str(fact))]) for fact in record.get("facts") or []
            if _normalise_sentence(str(fact)) in owners and not _is_fact_shown_by_own_screen(str(fact), screen)
            and not _is_same_screen(screen, screens.get(owners[_normalise_sentence(str(fact))], ""))]


def drop_copied_facts(paths: RunPaths,
                      slug: str,
                      record: dict) -> dict:
    """The record without the facts an accepted frame already holds word for word, when at least one fact of its own
    remains: the copy adds nothing, and the frame that holds it keeps it (a build step repeated its first frame's fact
    about a box scrolled out of view, and blocked a batch). A frame whose every fact is a copy is left as it is, and
    named on the console."""

    copied = {fact for fact, _ in _list_copied_facts(paths, slug, record)}
    kept = [fact for fact in record.get("facts") or [] if str(fact) not in copied]
    if not copied or not kept:
        return record

    return {**record, "facts": kept}


# A value a fact and a table row share: a number, a date or a code holding a digit ("162", "2026-01-10", "INC-3100")
_ROW_VALUE = re.compile(r"(?<![\w-])[\w-]*\d[\w.,-]*(?<![.,])")
# A fact restating a row names at least this many of the row's values
_ROW_FACT_MIN_VALUES = 2


def _list_row_values(text: str) -> set[str]:
    return {value.casefold() for value in _ROW_VALUE.findall(text)}


# A fact that says only where the page's structure turns: a section begins, a table continues
_STRUCTURE_FACT = re.compile(r"^(?:an?|the)\s+(?:new\s+)?(?:[\"'\u201c\u2018][^\"'\u201d\u2019]+[\"'\u201d\u2019]\s+)?"
                             r"(?:section|table|heading|chapter)\b[^.]*?\b(?:begins|starts|continues)\b", re.IGNORECASE)


def drop_figure_diagram(paths: RunPaths,
                        slug: str,
                        record: dict) -> dict:
    """A PDF figure's record without its diagram, when it has a fact: the figure is placed whole as its cut, and the
    merge leaves such a diagram out (`frame_merge_utils`), so it only ever blocked a frame. Wiki run 3's frame 4 was
    blocked twice for a diagram Mermaid could not draw, the second time after a wrong fix text."""

    if not record.get("facts") or not str(record.get("diagram") or "").strip():
        return record
    if frame_records_service.read_frame_medium(paths, slug) != output_contract.PDF_FIGURE_MEDIUM:
        return record

    # empty, as the notes' parser leaves a `(none)` diagram: a stored `(none)` reads to the check as one with a reason
    # after it (wiki run 3)
    return {**record, "diagram": ""}


# A line that starts code or a table: a fence, or a table's row
_CODE_OR_TABLE_LINE = re.compile(r"^\s*(?:```|~~~|\|)")


def drop_figure_markdown(paths: RunPaths,
                         slug: str,
                         record: dict) -> dict:
    """A PDF figure's record with its `## As markdown` empty when it holds neither code nor a table, the only blocks a
    figure's instruction asks for (`base/medium-pdf-figure.md`): the NIST tier chart's four tier names, written there
    as bare lines, reached the chapter as a slide's sentences and were written both as a sentence and run together on
    one line (DEFECTS 533). The figure's facts and outline keep what such lines say."""

    markdown = str(record.get("as_markdown") or "")
    if not markdown.strip() or any(_CODE_OR_TABLE_LINE.match(line) for line in markdown.splitlines()):
        return record
    if frame_records_service.read_frame_medium(paths, slug) != output_contract.PDF_FIGURE_MEDIUM:
        return record

    return {**record, "as_markdown": ""}


def _read_sentence_key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


# A fact's own words: four letters or more, not a joining word; the share of them from the page's text past which a
# fact naming nothing the figure shows is the page's. Measured over the 134 figure facts of seven figure runs
# (temp/figure-page-facts/measure.py): at half, 5 facts, each a page sentence written or reworded as a figure's fact;
# the next, a third from the page, names the figure's own small labels the recognizer missed
_FACT_WORD = re.compile(r"[a-z0-9][a-z0-9-]{3,}")
_JOINING_WORDS = frozenset("that this with from their they them which when where there these those into have been were "
                           "will would each also only than then over under about after before between through while "
                           "what because".split())
_PAGE_WORD_SHARE = 0.5


def _list_fact_words(text: str) -> set[str]:
    return {word for word in _FACT_WORD.findall(text.casefold()) if word not in _JOINING_WORDS}


def _is_page_fact(fact: str,
                  page_words: set[str],
                  figure_words: set[str]) -> bool:
    """Whether a figure's fact is its page's text: half or more of its words are the page's, and none stands on the
    figure or in its caption."""

    words = _list_fact_words(fact)

    return (len(words) >= 3 and len(words & page_words) / len(words) >= _PAGE_WORD_SHARE
            and not words & figure_words)


def drop_figure_page_facts(paths: RunPaths,
                           slug: str,
                           record: dict) -> dict:
    """A PDF figure's record without the facts that are its page's text, while another fact remains: a sentence of
    the page word for word, or, where Apple's text recognizer reads the figure, a fact most of whose words are the
    page's and none of whose words the figure or its caption shows. A figure's notes are only what the figure shows
    (base/medium-pdf-figure.md), the page's text is written into the document from its own paragraphs, and a scan's
    figure kept two of its page's sentences as facts (DEFECTS 524), another the same sentences reworded (529)."""

    facts = list(record.get("facts") or [])
    if len(facts) < 2 or frame_records_service.read_frame_medium(paths, slug) != output_contract.PDF_FIGURE_MEDIUM:
        return record
    before = frame_records_service.read_frame_speech_before(paths, slug)
    # Split at each sentence's end and at each line: the page's text opens on its heading's own line
    page = {_read_sentence_key(sentence) for sentence in re.split(r"(?<=[.!?])\s+|\n+", before)}
    kept = [fact for fact in facts if _read_sentence_key(str(fact)) not in page]
    figure_words = _read_figure_words(paths, slug)
    if figure_words is not None:
        page_words = _list_fact_words(before)
        kept = [fact for fact in kept if not _is_page_fact(str(fact), page_words, figure_words)]

    return {**record, "facts": kept} if kept and len(kept) < len(facts) else record


def _read_figure_words(paths: RunPaths,
                       slug: str) -> set[str] | None:
    """The words Apple's text recognizer reads on the figure, with its caption's; None where the recognizer or the
    frame's file is missing, so nothing is judged against a reading that was never made."""

    image = cv2.imread(str(frame_records_service.read_frame_image_path(paths, slug)))
    if image is None or not apple_text_service.is_available():
        return None
    read = " ".join(word["text"] for word in apple_text_service.find_words_in_image(image))

    return _list_fact_words(f"{read} {frame_records_service.read_frame_speech(paths, slug)}")


# A quoted text an outline line gives, of four words or more: a sentence, never a box's label
_QUOTED_SENTENCE = re.compile(r'"([^"]+)"')
_QUOTED_SENTENCE_WORDS = 4


def drop_figure_page_outline_lines(paths: RunPaths,
                                   slug: str,
                                   record: dict) -> dict:
    """A PDF figure's record without the outline lines quoting its page's text as text on the figure: a quote of four
    words or more, half of its words the page's and none read on the figure or in its caption. Told by a critique that
    the page's sentences were text left out of the outline, a repair wrote them in as a "title text" and a "paragraph
    text" of a figure showing three box labels (DEFECTS 529); over 54 such quotes of seven figure runs this names those
    two alone (temp/figure-page-facts/outline.py)."""

    screen = str(record.get("screen") or "")
    if not screen or frame_records_service.read_frame_medium(paths, slug) != output_contract.PDF_FIGURE_MEDIUM:
        return record
    figure_words = _read_figure_words(paths, slug)
    if figure_words is None:
        return record
    page_words = _list_fact_words(frame_records_service.read_frame_speech_before(paths, slug))
    kept = [line for line in screen.splitlines()
            if not any(len(quote.split()) >= _QUOTED_SENTENCE_WORDS and _is_page_fact(quote, page_words, figure_words)
                       for quote in _QUOTED_SENTENCE.findall(line))]

    return {**record, "screen": "\n".join(kept)} if len(kept) < len(screen.splitlines()) else record


# A word stating how a value stands, which a figure's fact may use only where the figure, its caption, its page or its
# outline holds it; written before a noun ("the default token lifetime") or after the value ("... by default"). Over
# the 406 facts of the public and invented PDF runs (temp/figure-page-facts/claims.py) one figure fact used one with
# no source, DEFECTS 512's
_STANDING_WORD = re.compile(r"\b(?:by\s+)?(defaults?|recommended|required|mandatory|optional|maximum|minimum|deprecated"
                            r"|preferred)\b\s?", re.I)


def drop_figure_unsourced_standing_words(paths: RunPaths,
                                         slug: str,
                                         record: dict) -> dict:
    """A PDF figure's record with each word of its facts that states how a value stands ("default", "required") taken
    out where neither the figure, read by Apple's text recognizer, its caption, its page nor its outline holds it: a
    settings form showing "3600 s" was noted "The default token lifetime is 3600 seconds", which no source says, and the
    chapter printed it (DEFECTS 512). The value stays; only the word goes."""

    facts = list(record.get("facts") or [])
    if not facts or frame_records_service.read_frame_medium(paths, slug) != output_contract.PDF_FIGURE_MEDIUM:
        return record
    figure_words = _read_figure_words(paths, slug)
    if figure_words is None:
        return record
    held = {word.rstrip("s") for word in figure_words | _list_fact_words(
        f"{frame_records_service.read_frame_speech_before(paths, slug)} {record.get('screen') or ''}")}

    def drop_word(match: re.Match) -> str:
        return "" if match.group(1).casefold().rstrip("s") not in held else match.group(0)

    kept = [re.sub(r"\s+([.,;:])", r"\1", _STANDING_WORD.sub(drop_word, str(fact))).strip() for fact in facts]

    return {**record, "facts": kept} if kept != [str(fact) for fact in facts] else record


# Words that say how a figure looks: colour, fill, edge, size, shape and place. A figure's fact 70% or more of whose own
# words (quotes aside) are these says how it looks, which its outline holds. Over the 134 figure facts of seven figure
# runs (temp/figure-page-facts/looks.py): at 0.7, 2, each looks alone (three boxes' fill and outline, a circuit
# pattern's hues); at 0.5 three more, among them "a three-component architecture", which states the figure's subject
_LOOK_WORDS = frozenset(
    "black white grey gray red orange yellow green blue purple violet pink brown teal cyan magenta navy beige light dark "
    "pale bright colour color colours colors colored coloured color-coded colour-coded hues hue shade shades fill filled "
    "fills background outline outlined border bordered borders dimensions size sized same identical shape shapes "
    "rectangle rectangles rectangular square circle circular rounded oval box boxes font bold italic text thin thick "
    "solid dashed dotted line lines arrow arrows icon icons pattern gradient transparent left right top bottom centre "
    "center middle above below beside horizontally vertically arranged positioned share shares three four five both "
    "each three-component".split())
_LOOK_STOP_WORDS = frozenset("that this with from their they them which when where there these those into have been "
                             "were will would also only than then over under about after before between through while "
                             "what because all the and are has its".split())
_LOOK_WORD = re.compile(r"[a-z0-9][a-z0-9-]{2,}")
_LOOK_SHARE = 0.7


def _is_look_fact(fact: str) -> bool:
    words = [word for word in _LOOK_WORD.findall(re.sub(r'"[^"]*"', " ", fact.casefold())) if word not in _LOOK_STOP_WORDS]

    return bool(words) and sum(word in _LOOK_WORDS for word in words) / len(words) >= _LOOK_SHARE


def drop_figure_look_facts(paths: RunPaths,
                           slug: str,
                           record: dict) -> dict:
    """A PDF figure's record without the facts that only say how it looks, while another fact remains: a three-box
    diagram's facts held "All three boxes share the same dimensions, light blue background fill, and dark blue outline",
    and the chapter printed it as a paragraph (DEFECTS 504). The figure's outline holds its looks, and the document
    places the figure itself."""

    facts = list(record.get("facts") or [])
    if len(facts) < 2 or frame_records_service.read_frame_medium(paths, slug) != output_contract.PDF_FIGURE_MEDIUM:
        return record
    kept = [fact for fact in facts if not _is_look_fact(str(fact))]

    return {**record, "facts": kept} if kept and len(kept) < len(facts) else record


# A screenshot's part that names one quoted text and nothing else: `the label "…"`, `title "…"`
_QUOTED_PART = re.compile(r'^[^"]{0,40}"([^"]+)"\s*$')


def set_text_screenshots_to_helps(record: dict) -> dict:
    """The record with each `needed` screenshot whose part is only a quoted text its own notes hold word for word stored
    as `helps`: the instruction says such a part is never needed, and the document carries the words. A worker marked a
    slide's one label needed, and the writer cut a picture of a line of text, through the drawing beside it."""

    held = " ".join(str(record.get(field) or "") for field in ("facts", "screen", "as_markdown", "take_aways")).casefold()
    held = " ".join(held.split())
    softened = []
    for suggestion in record.get("screenshots") or []:
        match = _QUOTED_PART.match(str(suggestion.get("part") or "")) if isinstance(suggestion, dict) else None
        quote = " ".join(match.group(1).casefold().split()) if match else ""
        if match and suggestion.get("importance") == frame_record.SCREENSHOT_NEEDED and quote in held:
            suggestion = {**suggestion, "importance": frame_record.SCREENSHOT_HELPS}
        softened.append(suggestion)

    return {**record, "screenshots": softened} if softened else record


def drop_structure_facts(record: dict) -> dict:
    """The record without the facts that say a section begins or a table continues, when at least one fact remains: a
    heading is structure, never knowledge. Told so in the PDF instructions, a worker still wrote "An "Incidents" section
    begins, listing incidents by ..." (pdf-02, twice), and the chapter's writer started the chapter at it."""

    kept = [fact for fact in record.get("facts") or [] if not _STRUCTURE_FACT.match(str(fact).strip())]
    if not kept or len(kept) == len(record.get("facts") or []):
        return record

    return {**record, "facts": kept}


# A fact that is a question asked told as the recording: most of its words a question of the record's own questions
# asked, worded as asking ("Participant D asks how ...", "the presenter clarifies whether ...", "the next question
# comes from ..."). Over 2,577 facts of the public runs 7 were mostly a question's words; these 4 asked, and the 3 others
# state the answer ("the division operator always returns a float") (DEFECTS 267)
_QUESTION_FACT_SHARE = 0.6
_QUESTION_FACT_WORD = re.compile(r"[a-z0-9]{4,}")
_ASKING = re.compile(r"\b(?:asks?|asked|asking|question|questions|whether)\b", re.IGNORECASE)
# ... or someone asking, in a frame whose questions asked hold the question: all 10 such facts of the public runs told
# a question being asked ("A participant asks about handling large memory usage ...", froscon talk 3143 frame 29,
# worded otherwise in its field)
_ASKED = re.compile(r"\b(?:asks|asked)\b", re.IGNORECASE)


def drop_question_facts(record: dict) -> dict:
    """The record without the facts that only tell a question of its `## Questions asked` as the recording, when at
    least one fact remains: the question stands in its own field, and phase 4 wrote the fact into the document as a
    sentence about the meeting ("A participant asks about handling large memory usage", froscon talk 3143)."""

    questions = [set(_QUESTION_FACT_WORD.findall(str(entry.get("question") if isinstance(entry, dict) else entry).lower()))
                 for entry in record.get("questions_asked") or []]
    questions = [words for words in questions if words]
    if not questions:
        return record
    facts = list(record.get("facts") or [])
    kept = [fact for fact in facts
            if not (_ASKED.search(str(fact))
                    or (_ASKING.search(str(fact))
                        and (words := set(_QUESTION_FACT_WORD.findall(str(fact).lower())))
                        and any(len(words & question) / len(words) >= _QUESTION_FACT_SHARE for question in questions)))]
    if not kept or len(kept) == len(facts):
        return record

    return {**record, "facts": kept}


# A name in the facts one letter off a name the frame's own outline quotes, where the outline holds it nowhere: the
# facts misread the slide ("RFM6SCW" beside the outline's "RFM69CW", froscon talk 3211 frames 68 and 72). Over 867 frames
# of the public runs it changes those 2 names and no other. A name holds a digit or two capitals, five characters or more
_OUTLINE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{4,}")
_OUTLINE_LEAST_CAPITALS = 2


def _is_outline_name(token: str) -> bool:
    return any(letter.isdigit() for letter in token) or sum(letter.isupper() for letter in token) >= _OUTLINE_LEAST_CAPITALS


def use_outline_spelling_in_facts(record: dict) -> dict:
    """The record with each fact's name that the outline holds nowhere and that is one letter off exactly one name the
    outline quotes written as the outline quotes it, and each such word of `## As markdown` too (DEFECTS 276)."""

    screen = str(record.get("screen") or "")
    quoted = {token for line in screen.splitlines() for match in [_LINE_QUOTE.search(line)] if match
              for token in _OUTLINE_NAME.findall(match.group(1))}
    lowered = screen.casefold()
    facts = []
    for fact in record.get("facts") or []:
        text = str(fact)
        for token in set(_OUTLINE_NAME.findall(text)):
            if not _is_outline_name(token) or token.casefold() in lowered:
                continue
            near = [other for other in quoted if _is_one_letter_off(token, other)]
            if len(near) == 1:
                text = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", near[0], text)
        facts.append(text)
    markdown = _use_outline_spelling_in_markdown(str(record.get("as_markdown") or ""), screen)
    if facts == [str(fact) for fact in record.get("facts") or []] and markdown == str(record.get("as_markdown") or ""):
        return record

    return {**record, "facts": facts, **({"as_markdown": markdown} if record.get("as_markdown") else {})}


# A take-away's side of the screen and the item it names there: `the right column item "Remote Accessibility"`. Over
# the public runs 11 take-away lines name a side the outline places their item under, 1 the wrong side (DEFECTS 328)
_TAKE_AWAY_SIDE = re.compile(r'\b(left|right)(\s+(?:column|panel|side|half)\b[^"\n:]{0,20}")([^"]{3,60})"', re.IGNORECASE)
_OUTLINE_SIDE = re.compile(r"\b(left|right)\b", re.IGNORECASE)


def _find_outline_side(lines: list[str],
                       item: str) -> str | None:
    for index, line in enumerate(lines):
        if f'"{item}"' not in line:
            continue
        indent = len(line) - len(line.lstrip())
        for parent in reversed(lines[:index]):
            parent_indent = len(parent) - len(parent.lstrip())
            match = _OUTLINE_SIDE.search(parent.split(":")[0])
            if parent_indent < indent and match:
                return match.group(1).casefold()
            indent = min(indent, parent_indent)
        return None

    return None


def drop_page_number_facts(record: dict) -> dict:
    """The record without the facts that only tell the slide's page number, when another fact remains."""

    facts = list(record.get("facts") or [])
    kept = [fact for fact in facts if not (isinstance(fact, str) and frame_markdown_utils.is_page_number_fact(fact))]

    return {**record, "facts": kept} if kept and len(kept) < len(facts) else record


# A question line the notes themselves call rhetorical: "<question>": rhetorical question by the presenter (DEFECTS 384)
_RHETORICAL = re.compile(r"\brhetorical\b", re.IGNORECASE)


def drop_rhetorical_questions(record: dict) -> dict:
    """The record without the questions asked whose own line calls them rhetorical: the worker filed a presenter's
    framing question as asked and labelled it so, and phase 4, which must keep every question, wrote it as an open
    point the talk answers (DEFECTS 384; 3 of 85 public question lines, all such)."""

    questions = list(record.get("questions_asked") or [])
    kept = [question for question in questions if not (isinstance(question, str) and _RHETORICAL.search(question))]

    return {**record, "questions_asked": kept} if len(kept) < len(questions) else record


def unwrap_speaker_facts(record: dict) -> dict:
    """The record with each fact that only reports the speaker saying a clause written as that clause (DEFECTS 267)."""

    facts = list(record.get("facts") or [])
    unwrapped = [frame_markdown_utils.unwrap_speaker_fact(str(fact)) if isinstance(fact, str) else fact for fact in facts]

    return {**record, "facts": unwrapped} if unwrapped != facts else record


def drop_remarks_about_notes(paths: RunPaths,
                             slug: str,
                             record: dict) -> dict:
    """The record without the facts and take-aways that only remark on the notes ("(No new facts.)"), when one of
    each field remains (DEFECTS 330), or when the frame shows a screen again, which passes with no fact: its lone
    remark went on as its only fact (release test 2, frame 31, DEFECTS 424)."""

    changed = {}
    is_repeat = _is_repeat(paths, slug)
    for field in ("facts", "take_aways"):
        entries = list(record.get(field) or [])
        kept = [entry for entry in entries if not frame_markdown_utils.is_remark_about_notes(str(entry))]
        if (kept or (is_repeat and field == "facts")) and len(kept) < len(entries):
            changed[field] = kept

    return {**record, **changed} if changed else record


def use_outline_side_in_take_aways(record: dict) -> dict:
    """The record with each take-away line that names an item on one side of the screen written with the side the
    outline places that item under: a take-away called a left column's bullet the right column's, beside an outline
    and a slide holding it on the left (release test 1, frame 72; DEFECTS 328)."""

    lines = str(record.get("screen") or "").splitlines()
    take_aways = []
    for line in record.get("take_aways") or []:
        text = str(line)
        for match in reversed(list(_TAKE_AWAY_SIDE.finditer(text))):
            side = _find_outline_side(lines, match.group(3))
            if side and side != match.group(1).casefold():
                word = side.capitalize() if match.group(1)[0].isupper() else side
                text = text[:match.start(1)] + word + text[match.end(1):]
        take_aways.append(text)
    if take_aways == [str(line) for line in record.get("take_aways") or []]:
        return record

    return {**record, "take_aways": take_aways}


# A word of `## As markdown` one letter off a word the outline quotes, letter case aside, that the outline holds nowhere:
# the markdown copy kept a correction the outline gave up ("Eingebautes" beside a slide's own "Eingabautes", froscon
# talk 3211 frame 9, after the second pass quoted it as the slide writes it). Over 377 frames with markdown of the public
# runs it changes that word and no other
_MARKDOWN_WORD = re.compile(r"[^\W\d_]{6,}")


def _use_outline_spelling_in_markdown(markdown: str,
                                      screen: str) -> str:
    quoted = {word for line in screen.splitlines() for match in [_LINE_QUOTE.search(line)] if match
              for word in _MARKDOWN_WORD.findall(match.group(1))}
    held = {word.casefold() for word in _MARKDOWN_WORD.findall(screen)}
    for word in set(_MARKDOWN_WORD.findall(markdown)):
        if word.casefold() in held:
            continue
        near = [other for other in quoted if _is_one_letter_off(word.casefold(), other.casefold())]
        if len(near) == 1:
            markdown = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", near[0], markdown)

    return markdown


def drop_table_row_facts(record: dict) -> dict:
    """The record without the facts that restate one row of a table its `## As markdown` writes, every value of the
    fact (at least two) standing in that row, when at least one fact remains: the row is written once, in the table.
    Told to write a table's rows once, a PDF page's worker still wrote one fact per incident beside the incident
    table, 31 a page, and the document then wrote the log twice (pdf-02)."""

    rows = [_list_row_values(line) for line in str(record.get("as_markdown") or "").splitlines()
            if line.strip().startswith("|")]
    rows = [row for row in rows if row]
    if not rows:
        return record
    kept = [fact for fact in record.get("facts") or []
            if not (len(values := _list_row_values(str(fact))) >= _ROW_FACT_MIN_VALUES
                    and any(values <= row for row in rows))]
    if not kept or len(kept) == len(record.get("facts") or []):
        return record

    return {**record, "facts": kept}


def _check_facts_copied(report: ValidationReport,
                        paths: RunPaths,
                        slug: str,
                        record: dict) -> None:
    """No fact is another frame's, word for word: a frame asked again alone was handed its batch's accepted notes and
    wrote 9 of the next frame's 11 facts as its own, and a repair may only add, so the copies stayed. Only a frame not
    yet accepted is judged, against the facts of frames already accepted: two frames of one answer are never both
    refused for one fact, and an accepted frame judged again (a repair's additions) is not refused for its old facts."""

    report.mark_ran("FRAME_FACTS_COPIED")
    copied = _list_copied_facts(paths, slug, record)
    # A console note, never a refusal (hard fix): a meeting's frame whose three facts frame 19 also held blocked phase 3
    # under zero repair (DEFECTS 702), the fifth time the check stopped a run. A fact written twice is said once by
    # phase 4, which refuses a sentence an earlier section holds
    if copied:
        report.note("FRAME_FACTS_COPIED",
                   f"{slug}: `## {_FACTS}` holds {len(copied)} fact(s) that frame "
                   f"{', '.join(sorted({str(owner) for _, owner in copied}))} already holds word for word: "
                   + "; ".join(f'"{fact}"' for fact, _ in copied[:5]),
                   fix=f"state under `## {_FACTS}` only what this frame's own screen and the words said while it was "
                       "shown establish; a fact another frame holds belongs to that frame, and this one may point to it "
                       "under `## Builds on`")


def _list_earlier_quoted_words(paths: RunPaths,
                               slug: str) -> set[str]:
    """The words the outlines of the frames before this one quote, and the lines the text reader reads on them (up to
    _EARLIER_OUTLINE_FRAMES of them, as many as a frame's message hands it): what the earlier frames already showed."""

    items = sorted(_read_frame_items(paths), key=lambda entry: entry.get("index", 0))
    position = next((number for number, entry in enumerate(items) if entry.get("slug") == slug), None)
    words: set[str] = set()
    # Every earlier frame's outline: a lecture went back to its terminal after a dozen blackboard frames, the terminal's
    # words read as new, and the frame was refused for empty facts (Lecture 7 frame 48, DEFECTS 557)
    for distance, entry in enumerate(reversed(items[:position or 0])):
        # A text an earlier frame showed is shown before though its outline left it out: a terminal scrolled back to a
        # tips panel earlier outlines had skipped, its words read as new, and the frame was refused for empty facts
        # (Lecture 7 frame 21, DEFECTS 554). Read on the nearest frames only, each reading costing a text reader's run
        if distance < _EARLIER_OUTLINE_FRAMES:
            words |= {word for line in read_screen_lines(paths, entry["slug"]) for word in _list_words(line)}
        try:
            words |= _list_quoted_words(str(record_file_service.read_frame_record(paths, entry["slug"]).get("screen") or ""))
        except (OSError, ValueError, KeyError):
            continue

    return words


# A take-away line about speech no part of the screen shows
_NOTHING_ON_SCREEN = "nothing on screen"


def _list_take_away_teachings(record: dict) -> list[str]:
    """What the frame's take-away lines say a screen part tells, the words after their colon, or after the arrow when
    there is none: the knowledge a worker read and kept in its own reading instead of `## Facts`. A line about nothing on screen, a part nobody mentions and
    the closing `Take aways:` line are left out."""

    teachings = []
    for line in record.get("take_aways") or []:
        text = str(line).strip()
        if "→" not in text or _NOTHING_ON_SCREEN in text or text.casefold().startswith("take aways"):
            continue
        part = text.split("→", 1)[1].strip()
        if part.casefold().startswith("not mentioned"):
            continue
        # `<part of the screen>: <what it tells>`, or a reading written with no colon, taken whole (froscon 3211 frame
        # 14: "→ the presenter zooms into the circuit diagram to show the relay connections ...")
        teachings.append(part.split(":", 1)[1].strip() if ":" in part else part)

    return [teaching for teaching in teachings if teaching]


def _check_facts(report: ValidationReport,
                 slug: str,
                 record: dict,
                 spoken_words: int | None = None,
                 build_frames: set[int] = frozenset(),
                 is_repeat: bool = False,
                 earlier_words: set[str] = frozenset()) -> None:
    """A screen full of text yielded facts, and the facts are about the subject, never the screen."""

    facts = [str(entry) for entry in record.get("facts") or [] if str(entry).strip()]
    # Each text once, and never a page number: a section card ("1990", "Time has passed...") counted its year twice, the
    # second a place named after it, and its page number "23", and was refused as a screen full of text (DEFECTS 316)
    quotes = [quote for quote in dict.fromkeys(frame_markdown_utils.list_screen_quotes(str(record.get("screen") or "")))
              if not _PAGE_NUMBER.fullmatch(quote.strip())]
    # Only text the earlier notes do not hold asks for a fact: a file shown again beside a changing terminal is no
    # repeat to phase 2, and a frame of it, six words said over it, was refused for writing none (Lecture 9, frame 113:
    # 0 of its 45 quoted words new). Words, never quotes: a code line's own quote marks cut an outline into fragments
    is_shown_before = bool(earlier_words) and len(
        _list_quoted_words(str(record.get("screen") or "")) - earlier_words) < _NEW_WORDS_THAT_TEACH
    report.mark_ran("FRAME_FACTS_MISSING")
    is_transition = spoken_words is not None and spoken_words <= _TRANSITION_WORDS
    # A moment that is a participant's question over a screen with little text (a popup, a list nobody discusses) holds
    # its content under `## Questions asked`: forced to write a fact, a worker wrote one about the popup (2026-10-01)
    is_question_moment = bool(record.get("questions_asked")) and len(quotes) < _QUESTION_SCREEN_QUOTES
    # A step of a slide built up step by step holds only what it adds: its build's text is in its first frame's facts,
    # and a step that adds nothing but decoration writes `(none)`, as its instruction says
    # A screen shown again holds only what this showing adds, and nothing new is a right answer: its text is in the
    # earlier showing's facts (2026-10-03: a demo's closing frame, a repeat, refused for writing none)
    if (not facts and len(quotes) >= _TEXT_THAT_TEACHES and not is_transition and not is_question_moment
            and not build_frames and not is_repeat and not is_shown_before):
        teachings = _list_take_away_teachings(record)
        # The worker's own reading named what the screen tells, and none of it reached the facts (froscon 3211, frame
        # 14: each relay's OFF and MAIN pins and its diode, read in the take-away, and `## Facts` empty)
        from_reading = ("" if not teachings else " Your own reading under `## Take aways` already says what the screen "
                        "tells: " + "; ".join(f'"{teaching}"' for teaching in teachings[:3])
                        + f". State each as facts about the subject under `## {_FACTS}`.")
        report.add("FRAME_FACTS_MISSING",
                   f"{slug}: the screen carries {len(quotes)} quoted texts and `## {_FACTS}` is empty",
                   fix=f"state what the screen and the speech establish under `## {_FACTS}`: facts about the subject, "
                       "one per `- ` line. When the quoted texts are the window's or the app's own controls (a toolbar, "
                       "a call's buttons), they are chrome: one line under `## Screen`, never listed, and `(none)` "
                       f"under `## {_FACTS}` when the screen and the speech add nothing new.{from_reading}")

    report.mark_ran("FRAME_FACTS_REPEAT_THIRD_PERSON_DESCRIPTION")  # noted, not rejected
    narration = {_normalise_sentence(sentence)
                for sentence in _SENTENCE_END.split(str(record.get("third_person_description") or ""))}
    repeated = [entry for entry in facts if _normalise_sentence(entry) in narration]
    if repeated:
        report.note("FRAME_FACTS_REPEAT_THIRD_PERSON_DESCRIPTION",
                   f"{slug}: `## {_FACTS}` repeats the frame's `## What happens` word for word: {repeated[0]!r}",
                   fix=f"`## What happens` tells what happened; `## {_FACTS}` states what it teaches about the "
                       f"subject. Write the fact the moment establishes, or write `(none)` under `## {_FACTS}` when it "
                       "teaches nothing new")

    report.mark_ran("FRAME_FACTS_DESCRIBE_SCREEN")
    screen_talk = [entry for entry in facts if _SCREEN_TALK.match(entry.strip())]
    if facts and len(screen_talk) * 2 > len(facts):
        report.note("FRAME_FACTS_DESCRIBE_SCREEN",
                   f"{slug}: {len(screen_talk)} of {len(facts)} `## {_FACTS}` entries describe the screen, "
                   f"such as {screen_talk[0]!r}",
                   fix="state each entry as a fact about the subject: write what the thing IS or DOES, never what "
                       "the slide or screen shows")


# A line "<label>: <claim> (actually, <correction>)": the worker corrected itself in brackets, and the correction is what
# it settled on. Frame 66 of a talk wrote under `## Builds on` "frame 65: step 3 of the screen first shown as frame 63
# (actually, this is a new callout ...)" and was blocked as thinking aloud
_CORRECTED_CLAIM = re.compile(r"^(?P<label>[^:()\"]{1,80}:)\s*[^()\"]+\(\s*actually,?\s*(?P<correction>[^()\"]+)\)\s*\.?\s*$",
                              re.IGNORECASE)


def _write_correction_in_place(line: object) -> object:
    """`line` as "<label>: <correction>" when it corrects its own claim in brackets; any other line as it is."""

    match = _CORRECTED_CLAIM.match(line.strip()) if isinstance(line, str) else None
    if not match:
        return line
    correction = match.group("correction").strip().rstrip(".")

    return f"{match.group('label')} {correction[:1].upper() + correction[1:]}."


# A build's step line ("step 2 of the screen first shown as frame 73"), which each step's message gives, copied into
# `## Builds on` by frames that are no step of that build: frames 72 and 75 to 78 of a talk, a photo slide among them,
# each claimed to be a step of frame 73 or 63 (39c3 Photon Science talk, critique of frames 66 to 78)
_STEP_CLAIM = re.compile(r"\s*(?:same screen,\s*)?\bstep \d+ of the (?:screen|slide) (?:first shown as|of) frame (?P<frame>\d+)\b\.?",
                         re.IGNORECASE)


def drop_false_step_claims(paths: RunPaths,
                           slug: str,
                           record: dict) -> dict:
    """The record with each step claim under `## Builds on` taken out when the frames file does not make this frame a step
    of that build; the rest of the line kept. Unchanged for a frame the frames file does not hold."""

    items = _read_frame_items(paths)
    item = _find_frame_item(items, slug)
    if item is None or not record.get("builds_on"):
        return record
    build = _list_build_frames(paths, slug)
    if item.get("stepOf"):
        build |= {int(entry["index"]) for entry in items if entry.get("slug") == item.get("stepOf")}

    lines = []
    for line in record.get("builds_on") or []:
        kept = _STEP_CLAIM.sub(lambda match: match.group(0) if int(match.group("frame")) in build else "", str(line))
        kept = re.sub(r":\s*$", "", re.sub(r":\s+", ": ", kept)).strip()
        lines.append(kept)

    return {**record, "builds_on": [line for line in lines if line]}


def replace_corrected_claims(record: dict) -> dict:
    """The record with each line written "<label>: <claim> (actually, <correction>)" kept as "<label>: <correction>"; any
    other thinking aloud is left for the check."""

    settled = dict(record)
    for name, value in record.items():
        if name == "diagram":
            continue
        if isinstance(value, list):
            settled[name] = [_write_correction_in_place(line) for line in value]
        elif isinstance(value, str) and "\n" not in value:
            settled[name] = _write_correction_in_place(value)

    return settled


def _check_third_person_description(report: ValidationReport,
                                    record: dict,
                                    is_document: bool = False) -> None:
    """R15: the frame's narration is there and short. Its person and whether it is accurate are read, never checked
    (the owner removed the person check and its warning). A PDF's page or figure, or a frame nobody spoke over
    (`is_document`), may have none: nothing happens on it, and a worker left the section out of every figure of a batch
    and of a page read alone, though the request named it three times and the PDF's instructions twice (pdf-04,
    pdf-figures-05-manual), and out of a slide shown in silence (froscon talk 3143, frame 16)."""

    frame = record.get("frame")
    text = str(record.get("third_person_description") or "").strip()
    report.mark_ran("FRAME_THIRD_PERSON_DESCRIPTION_MISSING")
    if not text and is_document:
        return
    if not text:
        # Noted, never refused (hard fix): a frame left without its narration blocked a meeting's batch on 4 frames
        # under zero repair, after instruction lines and the silent-frame and PDF exceptions had not stopped it.
        report.note("FRAME_THIRD_PERSON_DESCRIPTION_MISSING", f"frame {frame} has no `## What happens`.",
                    fix="say under `## What happens` what happens on screen, in the third person")

        return
    words = len(text.split())
    report.mark_ran("FRAME_THIRD_PERSON_DESCRIPTION_TOO_LONG")
    if words > _THIRD_PERSON_DESCRIPTION_MAX_WORDS:
        report.add("FRAME_THIRD_PERSON_DESCRIPTION_TOO_LONG",
                   f"frame {frame}'s `## What happens` has {words} words, at most {_THIRD_PERSON_DESCRIPTION_MAX_WORDS}.",
                   fix="keep the screen and its focus, what the presenter does, what they say it is for and any question; "
                       "drop word-for-word screen text and repetition")


def _find_copied_example_lines(record: dict) -> list[str]:
    """The entries of `record` that are a line of the sample answer, word for word, or hold one of the instructions'
    placeholders (`<what the speaker says ...>` written in front of the worker's own sentence)."""

    entries = [entry for value in record.values() if isinstance(value, (list, str))
               for entry in (value if isinstance(value, list) else str(value).splitlines()) if isinstance(entry, str)]

    return [entry for entry in entries
            if instruction_example_utils.is_example_line(entry) or instruction_example_utils.find_placeholder(entry)]


def _check_screen(report: ValidationReport,
                  record: dict,
                  build_frames: set[int] = frozenset(),
                  has_frames_file: bool = True) -> None:
    """R17: the screen's outline is there, is an outline, and holds more than the screen itself. Whether a designer
    could redraw the screen from it is read, never checked."""

    frame = record.get("frame")
    screen = str(record.get("screen") or "")
    report.mark_ran("FRAME_SCREEN_MISSING")
    if not screen.strip():
        report.add("FRAME_SCREEN_MISSING", f"frame {frame} has no `## Screen`.",
                   fix="write the screen under `## Screen` as an outline: one part per `- ` line, "
                       '`- <what it is>: <where it sits, its colour, its state>`, its text quoted exactly in "double '
                       'quotes", a part inside another indented two spaces under it')

        return
    parts, problems = frame_markdown_utils.read_screen_parts(screen)
    report.mark_ran("FRAME_SCREEN_NOT_AN_OUTLINE")
    if problems:
        report.add("FRAME_SCREEN_NOT_AN_OUTLINE", f"frame {frame}'s `## Screen`: " + "; ".join(problems[:3]),
                   fix="write every line of `## Screen` as `- <what it is>: <where, colour, state>`, the first part "
                       "not indented and each part inside another two spaces deeper than it")
    report.mark_ran("FRAME_SCREEN_POINTS_ELSEWHERE")
    # A step of a slide built up step by step outlines only what it adds; the rest is its build's, by design: it may
    # point to a frame of its own build, in the given line or in its own words, and never to any other frame
    step_line = _BUILD_STEP_LINE.match(screen.strip())
    # With no frames file to read a build from, the step line the message gave stands for the build
    is_line_build = step_line is not None and not has_frames_file
    is_build_step = bool(build_frames) or is_line_build
    pointers = list(_POINTS_TO_FRAME.finditer(_QUOTED.sub('""', screen)))
    allowed = build_frames or ({int(step_line.group("frame"))} if is_line_build else set())
    pointer = next((match for match in pointers if int(match.group(1)) not in allowed), None)
    if pointer:
        report.add("FRAME_SCREEN_POINTS_ELSEWHERE",
                   f"frame {frame}'s `## Screen` points to another frame (\"{pointer.group(0)}\") instead of outlining "
                   "this one.",
                   fix="outline this frame's whole screen, part by part, as if no other frame existed: a later step "
                       "shows these notes alone, and the reader never sees the frame it points to")
    report.mark_ran("FRAME_SCREEN_TOO_THIN")
    # A screen with nothing to read on it (black while a share starts, a blank page, one person's camera) is outlined
    # whole in one line that says so and quotes no text. A black frame and two camera frames were blocked for it; their
    # facts came from the speech, so the facts say nothing about the screen.
    is_empty_screen = (len(parts) == 1 and not frame_markdown_utils.list_screen_quotes(screen)
                       and bool(_NOTHING_TO_READ.search(screen)))
    if not problems and len(parts) < _SCREEN_MIN_PARTS and not is_empty_screen and not is_build_step:
        report.add("FRAME_SCREEN_TOO_THIN",
                   f"frame {frame}'s `## Screen` has {len(parts)} part, at least {_SCREEN_MIN_PARTS}.",
                   fix="write every part you can see, in reading order, each under the part it sits in: the panels, "
                       "boxes, arrows, tables, fields and buttons, where each sits, its colour and its text quoted, so "
                       "a designer could redraw the screen and get at least 80% of it right")


def _check_screenshots(report: ValidationReport,
                       slug: str,
                       record: dict) -> None:
    """Each suggestion carries a word phase 5 can act on, and says why (R10). Whether showing it is worth it is phase
    5's judgement, never this check's. An empty list is a complete answer."""

    suggestions = record.get("screenshots") or []
    wanted = ", ".join(f"`{word}`" for word in screenshot_suggestion_utils.list_importance_words())
    report.mark_ran("FRAME_SCREENSHOT_VALUE")
    report.mark_ran("FRAME_SCREENSHOT_UNEXPLAINED")
    for position, suggestion in enumerate(suggestions, start=1):
        if not isinstance(suggestion, dict):
            report.add("FRAME_SCREENSHOT_VALUE", f"{slug}: screenshot {position} is a {type(suggestion).__name__}, "
                                                 "not an object",
                       fix=f"write each entry of `screenshots` as an object with `part`, `why` and `importance` ({wanted})")
            continue
        importance = str(suggestion.get("importance") or "")
        if importance not in screenshot_suggestion_utils.list_importance_words():
            # Rejected: phase 4 and phase 5 read this word letter for letter, so an entry they cannot act on is lost.
            report.add("FRAME_SCREENSHOT_VALUE", f"{slug}: screenshot {position}'s `importance` is {importance!r}",
                       fix=f"write its `- importance:` line as exactly one of {wanted}, or leave `## Screenshots` out")
            continue
        if _is_empty(suggestion.get("why")):
            # A note: the suggestion still works, it just costs the reader of the notes a guess.
            report.note("FRAME_SCREENSHOT_UNEXPLAINED", f"{slug}: screenshot {position} gives no `why`",
                        fix=f"say on its `- why:` line, in one sentence, what this part shows that `## {_FACTS}` "
                            "and `## Diagram` cannot")


def _explain_missing(report: ValidationReport,
                     paths: RunPaths,
                     slug: str,
                     call_slug: str) -> None:
    """Why a frame of a call has no record, from the log of the call that answered it: the call itself failed, its
    document for this frame could not be read, or it wrote none."""

    expected_index = int(slug.removeprefix(_SLUG_PREFIX))
    call_log_path = call_log_service.build_call_log_path(paths, call_slug) if call_slug else None
    if call_log_path is not None and call_log_path.is_file():
        call_report = chain_response_validator.validate_chain_response(call_log_path, slug)
        if not call_report.ok:
            report.findings += call_report.findings

            return
        parsed = call_log_service.read_call_log(call_log_path).get("response", {}).get("parsed") or {}
        unreadable = str((parsed.get("unreadable") or {}).get(str(expected_index)) or "")
        report.mark_ran("FRAME_NOTES_UNREADABLE")
        if unreadable:
            report.add("FRAME_NOTES_UNREADABLE", f"{slug}: {unreadable}",
                       fix=f'write frame {expected_index} again under "# Frame {expected_index} · <screen kind>", each '
                           f'field under its own "##" heading as your instructions show')

            return
    report.add("FRAME_RECORD_MISSING", f"{slug}: no notes were written for frame {expected_index}",
               fix=f'write its notes under "# Frame {expected_index} · <screen kind>", each field under its own "##" '
                   f"heading")


def _check_record(report: ValidationReport,
                  paths: RunPaths,
                  slug: str,
                  call_slug: str = "") -> None:
    """Everything a program can check about one frame's record on disk, after the call that wrote it ended well."""

    # --- the record exists and reads back ------------------------------------------------------
    report.mark_ran("FRAME_RECORD_MISSING")
    record_path = record_file_service.build_record_path(paths, slug)
    if not record_path.is_file() or not record_path.stat().st_size:
        _explain_missing(report, paths, slug, call_slug)

        return                 # every later check would cascade off a missing file

    report.mark_ran("FRAME_RECORD_UNPARSEABLE")
    try:
        record = record_file_service.read_frame_record(paths, slug)
    except ValueError as exc:
        report.add("FRAME_RECORD_UNPARSEABLE", f"{slug}: {exc}", fix="this is a playbook bug: the program writes this file")

        return

    # --- it is THIS frame's record ------------------------------------------------------------
    report.mark_ran("FRAME_INDEX_MISMATCH")
    expected_index = int(slug.removeprefix(_SLUG_PREFIX))
    if record.get("frame") != expected_index:
        report.add("FRAME_INDEX_MISMATCH", f"{slug}: the notes are headed frame {record.get('frame')!r}",
                   fix=f'head them "# Frame {expected_index}", the number the message gave you')

    # --- it says something at all -------------------------------------------------------------
    report.mark_ran("FRAME_RECORD_EMPTY")
    if all(_is_empty(record.get(name)) for name in _SAYING_FIELDS):
        report.add("FRAME_RECORD_EMPTY",
                   f"{slug}: `## {_FACTS}`, `## Diagram`, `## Screen` and `## Speech visual alignment` are all empty",
                   fix=f"state what this moment teaches under `## {_FACTS}`, and write the screen's parts under "
                       "`## Screen`")

        return

    # --- nothing copied from the instruction's own invented example --------------------------------
    report.mark_ran("FRAME_EXAMPLE_COPIED")
    copied = _find_copied_example_lines(record)
    if copied:
        report.add("FRAME_EXAMPLE_COPIED", f"{slug}: a line is the invented example of your instructions, word for "
                   f"word: {copied[0]!r}",
                   fix="write only what this frame and its speech show; the example in your instructions is invented "
                       "and belongs to no meeting")

    item = _find_frame_item(_read_frame_items(paths), slug) or {}
    # A frame nobody spoke over may have none too: froscon talk 3143's frame 16, a slide shown in silence, came back
    # without the section, every other one written (DEFECTS 256)
    is_silent = bool(item) and not str(item.get("transcriptCurrent") or "").strip()
    _check_third_person_description(report, record, is_silent or item.get("medium") in (output_contract.PDF_MEDIUM,
                                                                                        output_contract.PDF_FIGURE_MEDIUM))
    # A frame showing a screen again may point at the frame it repeats: the program puts that frame's own outline in
    # its place when it stores the notes, so what a later step reads still stands alone (2026-10-06).
    build_frames = _list_build_frames(paths, slug) | _list_repeated_frames(paths, slug)
    _check_screen(report, record, build_frames, bool(_read_frame_items(paths)))
    _check_screen_text(report, paths, slug, record, build_frames)
    _check_facts(report, slug, record, _count_spoken_words(paths, slug), build_frames, _is_repeat(paths, slug),
                 _list_earlier_quoted_words(paths, slug))
    _check_facts_copied(report, paths, slug, record)
    _check_speech_quotes(report, paths, slug, record)
    _check_diagram(report, slug, record, build_frames)
    _check_tags(report, slug, record)
    _check_screenshots(report, slug, record)


def _list_title_words(text: str) -> list[str]:
    return [word.casefold() for word in _TITLE_WORD.findall(text)]


def _is_title_alike(title: str,
                    other: str) -> bool:
    """Do two titles name the same topic: the same words, or nearly the same letters?"""

    words, other_words = _list_title_words(title), _list_title_words(other)
    if not words or not other_words:
        return False
    if difflib.SequenceMatcher(None, " ".join(words), " ".join(other_words)).ratio() >= _TITLE_ALIKE_RATIO:
        return True

    return len(set(words) & set(other_words)) / len(set(words) | set(other_words)) >= _TITLE_ALIKE_WORDS


def _read_meeting_name(paths: RunPaths) -> str:
    """The recording's name as words, its dates, times and ids left out: what a title naming the meeting would say."""

    return " ".join(word for word in _TITLE_WORD.findall(Path(str(paths.module)).stem.replace("_", " "))
                    if not any(character.isdigit() for character in word))


def _check_batch_title(report: ValidationReport,
                       paths: RunPaths,
                       summary_slug: str,
                       title: str) -> None:
    """The batch's title: there, a few words, never the meeting's name, and no earlier batch's title again."""

    report.mark_ran("BATCH_TITLE_MISSING")
    if not title:
        report.add("BATCH_TITLE_MISSING", "the batch answer has no `## Title`.",
                   fix='write under "# Batch", "## Title" the topic of these frames in a few words, such as "Refund '
                       'split limits at the AcmeSync gateway"')

        return
    for code in ("BATCH_TITLE_SHAPE", "BATCH_TITLE_GENERIC", "BATCH_TITLE_NOT_UNIQUE"):
        report.mark_ran(code)
    words = len(title.split())
    if not _TITLE_MIN_WORDS <= words <= _TITLE_MAX_WORDS:
        report.add("BATCH_TITLE_SHAPE", f"`## Title` has {words} word(s): {title!r}",
                   fix=f"name the batch's topic in {_TITLE_MIN_WORDS} to {_TITLE_MAX_WORDS} words, on one line")
    meeting = _read_meeting_name(paths)
    if meeting and _is_title_alike(title, meeting):
        report.add("BATCH_TITLE_GENERIC", f"`## Title` {title!r} names the meeting ({meeting!r}), not this batch",
                   fix="name what these frames are about: the system, the step, the rule or the question they show")
    group_slug = batch_summaries_service.read_group_slug(summary_slug)
    for batch in batch_summaries_service.read_batch_summaries(paths):
        earlier = str(batch.get("title") or "")
        if batch.get("slug") != group_slug and earlier and _is_title_alike(title, earlier):
            frames = batch.get("frames") or []
            report.add("BATCH_TITLE_NOT_UNIQUE",
                       f"`## Title` {title!r} repeats the title of the batch of frames "
                       f"{frames[0] if frames else '?'} to {frames[-1] if frames else '?'}: {earlier!r}",
                       fix="name what THIS batch adds or turns to, in words no earlier batch's title uses")
            break


def _read_answer_record(paths: RunPaths,
                        slug: str,
                        reader: Callable[..., dict],
                        report: ValidationReport,
                        code: str) -> dict | None:
    """The answer `reader` reads for `slug`; an empty record when the file is absent or empty, and, when it is there
    but cannot be read, None with `code` added to `report`, naming why: the caller judges nothing more."""

    record_path = record_file_service.build_record_path(paths, slug)
    report.mark_ran(code)
    if not record_path.is_file() or not record_path.stat().st_size:
        return {}
    try:
        return reader(paths, slug)
    except ValueError as exc:
        report.add(code, f"{slug}: the answer could not be read: {exc}",
                   fix="write the answer in the shape its request shows, each section under its own `##` heading")

        return None


def _check_rule_is_generic(report: ValidationReport,
                           critique_slug: str,
                           position: int,
                           rule: dict,
                           screen_texts: list[str],
                           code: str = "CRITIQUE_RULE_NOT_GENERIC") -> None:
    """A rule carried into the instructions must hold for any meeting: it names no frame of this batch in its own
    lines, and quotes no text off this meeting's screens."""

    name = str(rule.get("name") or "").strip()
    written = " ".join(str(rule.get(field) or "") for field in critique_record.RULE_LINES)
    numbered = critique_markdown_utils.find_frame_in_rule(f"{name} {written}")
    if numbered:
        report.add(code,
                   f"{critique_slug}: rule {position} ({name}) says \"{numbered}\" in the rule itself",
                   fix=f'state the rule for any meeting and put the frame numbers on the '
                       f'"{critique_record.SEEN_ON_LABEL}" line alone')

        return
    quoted = critique_markdown_utils.find_screen_quotes(f"{name} {written}", screen_texts)
    if quoted:
        report.add(code,
                   f"{critique_slug}: rule {position} ({name}) quotes this meeting's screen: \"{quoted[0]}\"",
                   fix="name what KIND of text was missed, never the words this meeting showed: the file is carried "
                       "into the worker's instructions, where one meeting's words do not belong")


# The kind of a row of short entries across the top fifth of a frame: window chrome, neither checked nor listed
TOP_BAR_KIND = "top bar"
# The kind of every line left of a pane border in the frame's left third, and of lines that make one paragraph of text
SIDE_PANEL_KIND = "side panel"
PARAGRAPH_KIND = "paragraph"


@dataclasses.dataclass(frozen=True)
class ScreenTextItem:
    """Lines of the frame's text, grouped: `kind` is "top bar" (a row of short entries across the top fifth, window
    chrome no reader lists), "side panel" (every line left of a pane border in the frame's left third), "paragraph"
    (running text: one reader paragraph of at least _PARAGRAPH_MIN_LINES lines of _PARAGRAPH_MIN_WORDS_PER_LINE words
    on average) or "line"."""

    kind: str
    lines: list[str]


def _list_missing_rule_lines(rule: dict) -> list[str]:
    """The lines a critique's or a rule set's rule leaves empty, by their label."""

    return [label for field, label in critique_record.RULE_LINES.items() if not str(rule.get(field) or "").strip()]


def list_screen_text_items(paths: RunPaths,
                           slug: str) -> list[ScreenTextItem]:
    """The lines of text the text reader finds on the frame, grouped: a top bar first, then a side panel's lines, then
    the rest in reading order, each run of running text one paragraph."""

    pieces = _read_screen_pieces(paths, slug)
    if not pieces:
        return []
    width = pieces[0]["width"]
    panel_edge = next((border for border in pieces[0]["borders"]
                       if _BORDER_MARGIN * width <= border <= _SIDE_PANEL_MAX_SHARE * width
                       and sum(piece["x1"] <= border for piece in pieces) >= _SIDE_PANEL_MIN_LINES
                       and _is_screen_tall([piece for piece in pieces if piece["x1"] <= border])
                       and any(piece["x0"] >= border for piece in pieces)), None)
    panel = [piece["text"] for piece in pieces if panel_edge is not None and piece["x1"] <= panel_edge]
    rest = [piece for piece in pieces if panel_edge is None or piece["x1"] > panel_edge]
    bar = _find_top_bar(rest)
    nearby_top = _read_nearby_top_pieces(paths, slug) if any(
        piece["y"] <= _TOP_BAR_MAX_SHARE * piece["height"] and piece not in bar for piece in rest) else []
    bar += [piece for piece in rest if piece not in bar and _is_standing_row(piece, nearby_top)]
    rest = [piece for piece in rest if piece not in bar]
    runs: list[list[dict]] = []
    for index, piece in enumerate(rest):
        if index and piece["paragraph"] is not None and rest[index - 1]["paragraph"] == piece["paragraph"]:
            runs[-1].append(piece)
        else:
            runs.append([piece])
    items = ([ScreenTextItem(TOP_BAR_KIND, [piece["text"] for piece in bar])] if bar else []) + (
        [ScreenTextItem(SIDE_PANEL_KIND, panel)] if panel else [])
    for run in runs:
        texts = [piece["text"] for piece in run]
        if _is_running_text(texts):
            items.append(ScreenTextItem(PARAGRAPH_KIND, texts))
        else:
            items += [ScreenTextItem("line", [text]) for text in texts]

    return items


def _is_copy_of(line: str,
                other: str) -> bool:
    """Is every word of `line` a word of `other` (a letter misread allowed), so quoting `other` quotes `line` too?"""

    words = _list_words(line)

    return bool(words) and all(_is_word_quoted(word, set(_list_words(other))) for word in words)


def _drop_copied_lines(lines: list[str]) -> list[str]:
    """`lines` without a line the text reader read again inside another (a button label read twice, a cell's words
    repeated in a longer cell): it is quoted when the other is, so it counts once. Of two copies the first stays."""

    kept = []
    for position, line in enumerate(lines):
        others = [other for index, other in enumerate(lines) if index != position
                  and (len(_list_words(other)) > len(_list_words(line))
                       or (len(_list_words(other)) == len(_list_words(line)) and index < position))]
        if not any(_is_copy_of(line, other) for other in others):
            kept.append(line)

    return kept


# The line a build step's outline opens with, in either of the forms its message names
_STEP_LINE = re.compile(r"^\s*-\s*(?:a\s+)?step\b.*\bof the screen of frame\s+(\d+)", re.IGNORECASE | re.MULTILINE)
_STEP_PREFIX = re.compile(r"^(?P<indent>\s*-\s*)(?:a\s+)?step\b.*?\bof the screen of frame\s+\d+", re.IGNORECASE | re.MULTILINE)


# A sentence of a PDF page's own text the notes hold when this share of its words stands in them
_PAGE_SENTENCE_HELD_SHARE = 0.8
_PAGE_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def add_missing_page_sentences(paths: RunPaths,
                               slug: str,
                               record: dict) -> dict:
    """A PDF page's record with each sentence of the page's own text (its item's `transcriptCurrent`, read from the
    file) that no fact, outline, table or take-away holds added as a fact, word for word; any other record unchanged.
    The text layer is the page's exact text: two bullets a picture covered on a slide were in the file's text and in
    no note, and the document would have lost them (pdf-03)."""

    item = _find_frame_item(_read_frame_items(paths), slug)
    if not item or item.get("medium") != "pdf":
        return record
    held = set(_list_words(" ".join(str(part) for part in [*(record.get("facts") or []), record.get("screen") or "",
                                                        record.get("as_markdown") or "",
                                                        *(record.get("take_aways") or [])])))
    added = []
    for line in str(item.get("transcriptCurrent") or "").splitlines():
        stripped = line.strip().lstrip("-*• ").strip()
        if not stripped or stripped.startswith(("#", "|", "(")):
            continue
        for sentence in (match.group(0).strip() for match in _PAGE_SENTENCE.finditer(stripped)):
            words = _list_words(sentence)
            # A chart's labels read off its picture ("ai Q2 Q3 Q4 Qs") are no sentence: three words of three letters
            # at least make one (pdf-03)
            is_sentence = sum(1 for word in words if len(word) >= 3 and word.isalpha()) >= 3
            if is_sentence and sum(word in held for word in words) < _PAGE_SENTENCE_HELD_SHARE * len(words):
                added.append(sentence)
    if not added:
        return record

    return {**record, "facts": [*(record.get("facts") or []), *added]}


def add_step_line(paths: RunPaths,
                  slug: str,
                  record: dict) -> dict:
    """The record of a build step whose outline lacks its step line with that line opening it, the parts as written
    under it; a step line naming another frame than the build's first corrected, and one on a frame of no build named
    the screen; any other record unchanged. Its message names the line, and a worker left it out (Lecture 9, frame 54):
    the frame's notes read alone named no screen the parts belong to."""

    item = _find_frame_item(_read_frame_items(paths), slug)
    start = (item or {}).get("stepOf")
    screen = str(record.get("screen") or "")
    written = _STEP_LINE.search(screen)
    # A step line naming another frame is the program's to correct: a worker copied a neighbour's line into the 13
    # frames of another build, one of them no step at all (Lecture 9, frames 83 to 95)
    if not start:
        return record if item is None or not written else {**record, "screen": _STEP_PREFIX.sub(r"\g<indent>the screen", screen, count=1)}
    number = int(str(start).rsplit("-", 1)[-1])
    if written:
        return record if int(written.group(1)) == number else {
            **record, "screen": screen[:written.start(1)] + str(number) + screen[written.end(1):]}
    indented = "\n".join(f"  {line}" if line.strip() else line for line in screen.splitlines())

    return {**record, "screen": f"- a step of the screen of frame {number}: the parts below\n{indented}"}


# A part of an outline that quotes the frame before instead of this one: its quoted words, three or more, stand on the
# frame before (this share at least) and not on this frame (under this share). Frame 117 of a lecture outlined
# frame 116's file; a phone's new article page kept the old page's headlines; a chart shown again kept the slide
# before's title and address (DEFECTS 130). A part quoting both frames, a header that stays, is kept
_NEIGHBOUR_LINE_MIN_WORDS = 3
_NEIGHBOUR_SHARE = 0.8
_OWN_SHARE = 0.3


def _split_screen_line(line: str) -> tuple[str, frozenset[str]]:
    """A `## Screen` line as the part it names and the words of what it says after it: up to its last closing quote
    when it quotes, the words after that apart; whole, with no words apart, otherwise. A step wrote
    `title "<text>": top left` for its first frame's `title "<text>": top left, large bold font` (38c3 talk 797, frames
    15, 19, 20; DEFECTS 479)."""

    text = line.strip()
    if text.count('"') < 2:
        return text, frozenset()
    end = text.rfind('"') + 1

    return text[:end], frozenset(re.findall(r"\w+", text[end:].casefold()))


def _is_held_line(line: str,
                  held: dict[str, set[str]]) -> bool:
    """Whether an earlier frame of the build outlines the part this line names, saying at least every word this line
    says after the part: a place written shorter is a repeat, a state the step adds ("highlighted") is not."""

    part, words = _split_screen_line(line)

    return part in held and words <= held[part]


def _is_step_line(text: str) -> bool:
    """Whether an outline line is a step's opening line, in the form its message asks for (`- step <k> of <n> ...`) or
    the one the program writes (`- a step of the screen of frame <n>: ...`)."""

    return text.startswith("- step ") or text.startswith("- a step of the screen of frame")


def drop_build_repeated_lines(paths: RunPaths,
                              slug: str,
                              record: dict) -> dict:
    """The record of a step of a slide built up step by step without the `## Screen` lines its build's earlier frames
    outline word for word, a line standing under a line it keeps kept: a step outlines only what it adds, and 43 of 192
    steps of the public runs outlined 70% or more of the slide again (release test 2's frame 61 all of it, its one new
    bullet among them). Unchanged for any other frame, when no line is left of the step's own, or when none is a repeat."""

    held: dict[str, set[str]] = {}
    for number in sorted(_list_build_frames(paths, slug)):
        try:
            screen = str(record_file_service.read_frame_record(paths, f"{_SLUG_PREFIX}{number:03d}").get("screen") or "")
        except (OSError, ValueError):
            continue
        for line in screen.splitlines():
            part, words = _split_screen_line(line)
            held.setdefault(part, set()).update(words)
    held.pop("", None)
    lines = str(record.get("screen") or "").splitlines()
    if not held or not lines:
        return record
    entries = [(len(line) - len(line.lstrip()), line.strip()) for line in lines]
    is_kept = [False] * len(entries)
    for index in range(len(entries) - 1, -1, -1):
        indent, text = entries[index]
        under = []
        for after in range(index + 1, len(entries)):
            if entries[after][1] and entries[after][0] <= indent:
                break
            under.append(after)
        is_kept[index] = (not text or _is_step_line(text) or not _is_held_line(text, held)
                          or any(is_kept[child] for child in under))
    # A step that outlines nothing its build lacks keeps its step line alone, the form its message gives it, and one with
    # no step line is left as it is: an outline is never emptied
    if all(is_kept) or not any(_is_step_line(text) for _, text in entries):
        return record

    return {**record, "screen": "\n".join(line for line, is_line_kept in zip(lines, is_kept) if is_line_kept)}


def drop_neighbour_screen_lines(paths: RunPaths,
                                slug: str,
                                record: dict) -> dict:
    """The record with each `## Screen` line whose quote the text reader finds on the frame before and not on this
    frame taken out; unchanged when the frame or the one before cannot be read, or for a frame with no frame before.
    A frame shown again shows its original's screen, so the words the reader finds there are its own too: froscon
    3147's frame 151 repeated 148, the reader missed its tab bar, and its tab line went while 148's and 149's stayed."""

    items = sorted(_read_frame_items(paths), key=lambda entry: entry.get("index", 0))
    position = next((number for number, entry in enumerate(items) if entry.get("slug") == slug), None)
    if not position:
        return record
    original = items[position].get("repeatOf")
    shown = [slug, str(original)] if original else [slug]
    own = {word for shown_slug in shown for line in read_screen_lines(paths, shown_slug) for word in _list_words(line)}
    before = {word for line in read_screen_lines(paths, str(items[position - 1].get("slug"))) for word in _list_words(line)}
    if not own or not before:
        return record
    kept = []
    for line in str(record.get("screen") or "").splitlines():
        match = _LINE_QUOTE.search(line)
        words = _list_words(match.group(1)) if match else []
        if len(words) >= _NEIGHBOUR_LINE_MIN_WORDS:
            on_before = sum(_is_word_quoted(word, before) for word in words) / len(words)
            on_own = sum(_is_word_quoted(word, own) for word in words) / len(words)
            if on_before >= _NEIGHBOUR_SHARE and on_own < _OWN_SHARE:
                continue
        kept.append(line)

    return {**record, "screen": "\n".join(kept)}


# "same as frame N" in an outline, the frame its outline points to
# The least share of the frame's own reader lines that another frame's outline, filled in for a pointer the capture
# never marked, must quote to stand for this frame, and the least lines read for the share to say anything
POINTED_OUTLINE_MIN_SHARE = 0.5
_POINTED_OUTLINE_MIN_LINES = 3
POINTED_OUTLINE_PART = ("- screen: the text the program read on the frame, the outline written for it being another "
                        "frame's (its reader may misread a letter):")


def replace_unfitting_pointed_outline(paths: RunPaths,
                                      slug: str,
                                      written: dict,
                                      filled: dict) -> dict:
    """`filled` (the record with the pointer in `written`'s `## Screen` filled by the program) with its outline built
    from the frame's own reader lines instead, when the outline pointed to a frame that is neither the one it repeats
    nor one of its build, and the filled outline quotes under POINTED_OUTLINE_MIN_SHARE of those lines; `filled`
    otherwise. A batch's worker wrote "same as frame 43" for a frame showing another slide, taking a neighbour's
    message for its own, and the program filled in the other slide's title and footer (froscon talk 3143, DEFECTS 260)."""

    # Every way of pointing the check refuses ("identical to frame 36"), not only "same as": a worker wrote one for a
    # terminal whose frames between were dropped, and the batch was blocked (froscon talk 3211, frame 40, DEFECTS 279)
    allowed = _list_build_frames(paths, slug) | _list_repeated_frames(paths, slug)
    pointed = {int(match.group(1)) for match in _POINTS_TO_FRAME.finditer(_QUOTED.sub('""', str(written.get("screen") or "")))}
    if not pointed or pointed <= allowed:
        return filled
    lines = read_screen_lines(paths, slug)
    if len(lines) < _POINTED_OUTLINE_MIN_LINES:
        return filled
    quoted = _list_quoted_words(str(filled.get("screen") or ""))
    fitting = sum(_read_quoted_share(line, quoted) >= _LINE_QUOTED_SHARE for line in lines)
    if fitting / len(lines) >= POINTED_OUTLINE_MIN_SHARE:
        # the outline fits its frame: a pointer left in it is only a part's words, and goes
        kept = [line for line in str(filled.get("screen") or "").splitlines()
                if not any(int(match.group(1)) not in allowed
                           for match in _POINTS_TO_FRAME.finditer(_QUOTED.sub('""', line)))]
        return {**filled, "screen": "\n".join(kept)}
    # A double quote inside a read line would end its quote early
    read = "".join(f'\n  - text "{line.replace(chr(34), chr(39))}"' for line in lines)

    return {**filled, "screen": f"{POINTED_OUTLINE_PART}{read}"}


# A read line counts as the part a second look named not on the frame when this alike, the names written back with a
# letter changed or the quotes kept
_LEFT_OUT_LIKENESS = 0.9


def _normalize_part_text(text: str) -> str:
    return " ".join(str(text).strip().strip('"').strip("'").casefold().split())


def list_program_screen_lines(screen: str) -> list[str]:
    """The lines the program added to an outline from its reader, under SCREEN_COMPLETION_PART."""

    if SCREEN_COMPLETION_PART not in screen:
        return []

    return re.findall(r'^\s+- text "(.*)"\s*$', screen.split(SCREEN_COMPLETION_PART, 1)[1], re.MULTILINE)


# A line Apple's recognizer reads is added from this many words: shorter ones are labels and buttons
_RECOGNIZED_LINE_MIN_WORDS = 3
# ... on a frame Tesseract reads this many words of, and when its reading holds this share of the line's words:
# release test 1's commit logs had 34 and 41 words read and lines confirmed 0.2 to 0.7; its small code font 2 words
_RECOGNIZED_FRAME_MIN_WORDS = 10
_RECOGNIZED_LINE_CONFIRMED_SHARE = 0.2


def _list_recognized_lines(paths: RunPaths,
                           slug: str) -> list[str]:
    """The lines Apple's recognizer reads on the frame's stored image, its whole height; empty when it cannot run."""

    item = _find_frame_item(_read_frame_items(paths), slug)
    if not item or not item.get("path"):
        return []

    return outline_reread_service.list_read_lines(paths.output_root / str(item["path"]))


def add_unquoted_screen_lines(paths: RunPaths,
                              slug: str,
                              record: dict,
                              left_out: list[str] | tuple[str, ...] = ()) -> dict:
    """The record with the lines the text reader finds on the frame and its outline leaves out added to `## Screen`, one
    quoted line each under SCREEN_COMPLETION_PART, when the outline quotes at least SCREEN_COMPLETION_MIN_SHARE of them;
    unchanged otherwise, so an outline summing the screen up is still refused. A line `left_out` names (a part the
    second look found nowhere on the frame, a misreading such as "Sore Code F" on a 545-pixel editor, DEFECTS 319) is
    never added."""

    # Against this frame's own outline, never its neighbours': counted with them, a line a neighbour quoted was left
    # out, the neighbour was then refused and its record removed, and the check refused the frame for that line
    lines = read_screen_lines(paths, slug)
    # ... and of its build and the screen it repeats, as the check counts them: a build step outlines only what it
    # adds (13 of 32 lines short of the check's bar, its own outline quoted 15 of them and nothing was added)
    outlines = [str(record.get("screen") or "")]
    for number in sorted(_list_build_frames(paths, slug) | _list_repeated_frames(paths, slug)):
        try:
            outlines.append(str(record_file_service.read_frame_record(paths, f"{_SLUG_PREFIX}{number:03d}").get("screen") or ""))
        except (OSError, ValueError):
            continue
    quoted = _list_quoted_words("\n".join(outlines)) | _list_markdown_words(record)
    named = [_normalize_part_text(part) for part in left_out]
    missing = [line for line in lines if _read_quoted_share(line, quoted) < _LINE_QUOTED_SHARE
               and not any(difflib.SequenceMatcher(None, _normalize_part_text(line), part).ratio() >= _LEFT_OUT_LIKENESS
                           for part in named)]
    if lines and (len(lines) - len(missing)) / len(lines) < SCREEN_COMPLETION_MIN_SHARE:
        return record
    # ... and the lines Apple's recognizer reads that the outline leaves out, where it runs: it reads a terminal's or a
    # list's lines whole where Tesseract gives fragments, and a 30-line commit log outlined as its first and last line
    # got "fix; typo" and "feat: for" from Tesseract alone (DEFECTS 352). A Tesseract line Apple's added lines already
    # hold most words of is not added again
    # Kept only on a frame Tesseract reads too, and when its reading holds a part of the line's words, a letter off
    # allowed: the two readers seldom garble a word the same way, and Apple's recognizer gave a small code font
    # Tesseract read 2 words of back as "wold nespoll_goil(strat ratpoli", with the confidence of a clean line
    tesseract_words = {word for item in list_screen_text_items(paths, slug) for line in item.lines
                       for word in _list_words(line)}
    recognized = [line for line in (_list_recognized_lines(paths, slug)
                                    if len(tesseract_words) >= _RECOGNIZED_FRAME_MIN_WORDS else [])
                  if len(_list_words(line)) >= _RECOGNIZED_LINE_MIN_WORDS and _read_quoted_share(line, quoted) < _LINE_QUOTED_SHARE
                  and _read_quoted_share(line, tesseract_words) >= _RECOGNIZED_LINE_CONFIRMED_SHARE
                  and not any(difflib.SequenceMatcher(None, _normalize_part_text(line), part).ratio() >= _LEFT_OUT_LIKENESS
                              for part in named)]
    recognized_words = _list_quoted_words("\n".join(f'"{line}"' for line in recognized))
    missing = recognized + [line for line in missing if _read_quoted_share(line, recognized_words) < _LINE_QUOTED_SHARE]
    if not missing:
        return record
    # A double quote inside a read line would end its quote early; a table's border read at a line's end is no text
    added = "".join(f'\n  - text "{_TABLE_BORDER_AT_END.sub("", line).replace(chr(34), chr(39))}"' for line in missing)

    return {**record, "screen": f'{str(record.get("screen") or "").rstrip()}\n{SCREEN_COMPLETION_PART}{added}'}


# A name the speech misheard and the notes wrote as heard, where a screen of the recording spells it ("Trixity" where
# the slides read "Trixnity", "RICE" where a logo reads "RISE"): the screen's spelling wins, the rule phase 3 is told,
# and the notes kept the heard one on every frame (the Matrix talk). A word is respelled only when it is a name (a
# capital past its first letter, a digit, or a capital inside a sentence), was said over the frame, is on no screen
# read so far, and one screen word, never said, is a letter or two off it
_HEARD_NAME_FIELDS = ("facts", "take_aways", "open_questions", "speech_visual_alignment", "action_items")
# A model name with a hyphen ("BT-100") is one token, compared without its hyphen: split, the speech's "BT-100" stood
# against a slide's "VT100" as "BT" and "100" and was never respelled (DEFECTS 312)
_NAME_TOKEN = re.compile(r"[A-Za-z]{1,4}-\d{2,}[A-Za-z0-9]*|[A-Za-z][A-Za-z0-9]{3,}")
_QUOTED_TEXT = re.compile(r'"[^"]*"|`[^`]*`')
_NAME_AFTER_SENTENCE_END = ".!?:-•"
# Letters a heard name may be off its screen spelling: one, two from seven letters ("VotoZemax" for "Vodozemac");
# "Pages" two off "Pads" is no mishearing
_HEARD_NAME_SHORT_EDITS = 1
_HEARD_NAME_LONG_EDITS = 2
_HEARD_NAME_LONG_LETTERS = 7
# How many frames before this one lend their outlines' quotes: a name shown once is said for minutes after
_HEARD_NAME_EARLIER_FRAMES = 40


def _build_name_key(word: str) -> str:
    return word.lower().replace("-", "")


def _is_model_number_pair(word: str,
                          other: str) -> bool:
    """Whether `word` and `other` are model names with the same digits: one heard letter off at its start ("BT100" for
    "VT100") is still the same name, where a plain word's first letter must agree (DEFECTS 312)."""

    digits = "".join(character for character in word if character.isdigit())

    return bool(digits) and digits == "".join(character for character in other if character.isdigit())


def _has_same_digits(word: str,
                     other: str) -> bool:
    """Whether a heard name with digits has exactly `other`'s: speech mishears letters, and a screen word whose digits
    differ is the text reader's misread ("RFM6SCW" for a heard "RFM-69CW", DEFECTS 312)."""

    digits = [character for character in word if character.isdigit()]

    return not digits or digits == [character for character in other if character.isdigit()]


# A quoted word one letter off a word the outlines of _NEAR_SPELLING_LEAST_FRAMES or more of the _NEAR_SPELLING_LOOK_BACK
# frames before quote, quoted by none of them itself: a slide's small link misread by a letter on one frame while the
# others spell it right (DEFECTS 276). Over the public runs' 6,572 quoted words 13 are such, 5 misreads and 8 other words
# (remote and remove), so the worker checks each on the image and the program changes none
_NEAR_SPELLING_WORD = re.compile(r"[^\W\d_]{6,}")
_NEAR_SPELLING_LEAST_FRAMES = 3
_NEAR_SPELLING_LOOK_BACK = 40


# A quoted text aligned with a reading line: at least this many words, and this likeness of the two word sequences;
# a word set against its read word when the two are this alike and differ beyond letter case (DEFECTS 573)
_READING_QUOTE_LEAST_WORDS = 3
_READING_LINE_LIKENESS = 0.6
_READING_WORD_LIKENESS = 0.75
_READING_WORD_LEAST_LETTERS = 4
_QUOTED_TEXT = re.compile(r'"([^"]{8,})"|`([^`]{8,})`')


def _strip_word(word: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", word)


def list_reading_spellings(item: dict,
                           screen: str) -> list[tuple[str, str]]:
    """Each word the outline's own quotes spell a letter or two off the word phase 2's reading of the frame
    (`screenWords`) has at that place, the quote aligned word by word with the reading line it matches best, with that
    read word. A model writes a screen's own typo corrected ("script" for the screen's "scirpt", three times in Lecture
    7) and misreads a word ("argpase"); the reader misreads too ("urn" for "Turn"), so a pair is named to be checked on
    the image, never swapped: over 885 public quotes 16 pairs, 7 the reading right and 4 or more the reader wrong."""

    lines: dict[object, list[str]] = {}
    for entry in item.get("screenWords") or []:
        word = _strip_word(str(entry.get("text") or ""))
        if word:
            lines.setdefault(entry.get("line"), []).append(word)
    if not lines:
        return []
    pairs = []
    for match in _QUOTED_TEXT.finditer(frame_markdown_utils.strip_program_reading(screen)):
        quoted = [word for word in map(_strip_word, (match.group(1) or match.group(2)).split()) if word]
        if len(quoted) < _READING_QUOTE_LEAST_WORDS:
            continue
        read = max(lines.values(), key=lambda words: difflib.SequenceMatcher(None, quoted, words).ratio())
        matcher = difflib.SequenceMatcher(None, quoted, read)
        if matcher.ratio() < _READING_LINE_LIKENESS:
            continue
        for tag, start, end, read_start, read_end in matcher.get_opcodes():
            if tag != "replace" or end - start != 1 or read_end - read_start != 1:
                continue
            said, seen = quoted[start], read[read_start]
            if (len(said) >= _READING_WORD_LEAST_LETTERS and said.casefold() != seen.casefold()
                    and difflib.SequenceMatcher(None, said.casefold(), seen.casefold()).ratio() >= _READING_WORD_LIKENESS
                    and (said, seen) not in pairs):
                pairs.append((said, seen))

    return pairs


def list_near_spellings(paths: RunPaths,
                        slug: str,
                        screen: str) -> list[tuple[str, str]]:
    """Each word `screen` quotes that is one letter off a word the outlines of earlier frames quote often, with that
    word, and that no earlier outline quotes itself."""

    items = sorted(_read_frame_items(paths), key=lambda entry: int(entry.get("index", 0)))
    item = _find_frame_item(items, slug)
    if item is None:
        return []
    earlier = [entry for entry in items if int(entry.get("index", 0)) < int(item.get("index", 0))][-_NEAR_SPELLING_LOOK_BACK:]
    counts: collections.Counter = collections.Counter()
    for entry in earlier:
        outline = (entry.get("record") or {}).get("screen") or ""
        counts.update(_list_quoted_spelling_words(outline if isinstance(outline, str) else "\n".join(map(str, outline))))
    pairs = []
    for word in sorted(_list_quoted_spelling_words(screen)):
        near = [other for other, count in counts.items() if count >= _NEAR_SPELLING_LEAST_FRAMES
                and len(other) == len(word) and _count_letter_edits(word, other) == 1]
        if near and not counts[word]:
            pairs.append((word, near[0]))

    return pairs


# A word of eight letters or more: shorter words of a table and an outline differ by meaning far more often than by a
# misreading. Close by difflib's ratio, as the measure over the public runs' 448 blocks was taken
_TABLE_WORD = re.compile(r"[A-Za-zÄÖÜäöüß]{8,}")
_TABLE_WORD_CLOSENESS = 0.75


def list_table_outline_spellings(record: dict) -> list[tuple[str, str]]:
    """Each word of eight letters or more the frame's `## As markdown` writes that its own `## Screen` holds nowhere and
    that is spelled close to a word the outline does hold, with that word: the two parts of one frame's notes disagree,
    and only the image says which is right (DEFECTS 376: a table wrote "Lasersetzbeschriftungssoftware" where the
    outline and the screen hold "Laserbeschriftungssoftware"). Over the public runs' 448 blocks: 3 such pairs."""

    screen = record.get("screen") or ""
    screen = (screen if isinstance(screen, str) else "\n".join(map(str, screen))).casefold()
    outline_words = sorted({word.casefold() for word in _TABLE_WORD.findall(screen)})
    pairs = []
    for word in sorted(set(_TABLE_WORD.findall(str(record.get("as_markdown") or "")))):
        if word.casefold() in screen:
            continue
        close = difflib.get_close_matches(word.casefold(), outline_words, n=1, cutoff=_TABLE_WORD_CLOSENESS)
        if close:
            pairs.append((word, close[0]))

    return pairs


def _list_quoted_spelling_words(outline: str) -> set[str]:
    return {word.lower() for quote in re.findall(r'"([^"]+)"', outline) for word in _NEAR_SPELLING_WORD.findall(quote)}


def _count_letter_edits(word: str,
                        other: str) -> int:
    previous = list(range(len(other) + 1))
    for row, letter in enumerate(word, 1):
        current = [row]
        for column, other_letter in enumerate(other, 1):
            current.append(min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + (letter != other_letter)))
        previous = current

    return previous[-1]


def _is_name_token(text: str,
                   match: re.Match) -> bool:
    word = match.group(0)
    if any(letter.isupper() or letter.isdigit() for letter in word[1:]):
        return True
    # A hyphen joining it to a word before it is no list's dash: "Moller-Tombore" was read as a sentence opening on
    # "Tombore", which was never respelled as the slide's "Trumbore" (38c3-openpv frame 67, DEFECTS 625)
    if text[:match.start()].endswith("-") and text[:match.start() - 1][-1:].isalpha():
        return word[0].isupper()
    before = text[:match.start()].rstrip(' "*(`')

    return word[0].isupper() and bool(before) and before[-1] not in _NAME_AFTER_SENTENCE_END


def _is_name_like(word: str) -> bool:
    return any(letter.isupper() or letter.isdigit() for letter in word)


def _find_screen_spelling(word: str,
                          read: dict[str, str | None],
                          quoted: dict[str, str],
                          spoken: set[str]) -> str | None:
    """The one screen spelling of a heard `word`, or None: `read` holds the words the text reader finds on the screens
    so far, `quoted` those the outlines quote, both by their lower-case form; `spoken` the words said over the frame."""

    key = _build_name_key(word)
    limit = _HEARD_NAME_LONG_EDITS if len(word) >= _HEARD_NAME_LONG_LETTERS else _HEARD_NAME_SHORT_EDITS
    near_read = any(other != key and spelling is not None and _count_letter_edits(key, other) <= limit
                    for other, spelling in read.items())
    # An outline's quote vouches for the word unless the reader found a spelling near it: the outline quoted the name
    # as heard too
    if key in read or (key in quoted and not near_read) or key not in spoken:
        return None
    # A word read only on frames past the 40 before lends no spelling (None), and never takes away an outline's quote of
    # it: the slide's "VT100", read on later frames, stopped respelling the heard "BT-100" (DEFECTS 362)
    lent = {**quoted, **{other: spelling for other, spelling in read.items() if spelling is not None}}
    candidates = sorted({(_count_letter_edits(key, other), spelling) for other, spelling in lent.items()
                         if spelling is not None and other != key and (other[0] == key[0] or _is_model_number_pair(key, other)) and other not in spoken
                         and _has_same_digits(key, other)
                         # A spelling only an outline quotes is a name by more than its capital: "Lines" quoted on a
                         # slide was taken for the heard "Linters" (Lecture 9, DEFECTS 313)
                         and (other in read or _is_name_like(spelling[1:])) and _is_name_like(spelling)
                         and (not spelling.isupper() or word.isupper())
                         and not (other.startswith(key) or key.startswith(other))
                         and _count_letter_edits(key, other) <= limit})
    if not candidates or (len(candidates) > 1 and candidates[0][0] == candidates[1][0]):
        return None

    return candidates[0][1]


def _use_screen_spelling(line: str,
                         read: dict[str, str | None],
                         quoted: dict[str, str],
                         spoken: set[str],
                         names: set[str],
                         is_shown_as_heard: Callable[[str, str], bool] = lambda key, spelling: False) -> str:
    """`line` with each heard name outside its quotes written as the screen spells it; `names` holds the words the
    notes use as names inside a sentence, which count as names at a sentence's start too."""

    bare = _QUOTED_TEXT.sub(lambda match: " " * len(match.group(0)), line)
    pieces, end = [], 0
    for match in _NAME_TOKEN.finditer(bare):
        is_name = _is_name_token(bare, match) or _build_name_key(match.group(0)) in names
        spelling = _find_screen_spelling(match.group(0), read, quoted, spoken) if is_name else None
        if spelling and not is_shown_as_heard(_build_name_key(match.group(0)), spelling):
            pieces += [line[end:match.start()], spelling]
            end = match.end()

    return "".join(pieces) + line[end:]


# The width Apple's recognizer reads a frame at, for a name's spelling
_SECOND_READ_WIDTH = 1500


def _read_second_words(paths: RunPaths,
                       entry: dict) -> set[str]:
    """The name keys of the words Apple's text recognizer reads on the frame of `entry`, at its own size and at about
    1,500 pixels wide; empty when the frame is not on disk."""

    image = cv2.imread(str(paths.output_root / str(entry.get("path") or "")))
    if image is None:
        return set()
    scale = max(1, round(_SECOND_READ_WIDTH / image.shape[1]))
    # One size alone misreads: a 545-pixel frame's "DisAssembler" was read right at its size and twice it, and as
    # "Disfssembler" at three times, the size it was read at (DEFECTS 364); a word either reading finds counts
    images = [image] + ([cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)] if scale > 1 else [])

    return {_build_name_key(token) for each in images for word in apple_text_service.find_words_in_image(each)
            for token in _NAME_TOKEN.findall(word["text"])}


def use_screen_spelling_of_heard_names(paths: RunPaths,
                        slug: str,
                        record: dict) -> dict:
    """The record with each name the speech misheard written as a screen of the recording spells it, in its facts,
    take-aways, open questions, alignment and action items; unchanged for a frame the frames file does not hold."""

    items = sorted(_read_frame_items(paths), key=lambda entry: entry.get("index", 0))
    position = next((number for number, entry in enumerate(items) if entry.get("slug") == slug), None)
    if position is None:
        return record
    shown = items[max(0, position - _HEARD_NAME_EARLIER_FRAMES):position + 1]
    # A word read on any frame of the recording is no mishearing, though only the 40 frames before lend a spelling: a
    # talk's name read right on 4 early frames and misread within the 40 was written as the misreading (froscon talk
    # 3211, "Jockalope"), and spellings taken from the whole talk wrote "Compute" for a lecture's "Compare" (DEFECTS 313)
    read = {_build_name_key(word): None for entry in items for line in read_screen_lines(paths, str(entry.get("slug")))
            for word in _NAME_TOKEN.findall(line)}
    read |= {_build_name_key(word): word for entry in shown for line in read_screen_lines(paths, str(entry.get("slug")))
             for word in _NAME_TOKEN.findall(line)}
    outlines = [str(record.get("screen") or "")] + [str((entry.get("record") or {}).get("screen") or "") for entry in shown[:-1]]
    quoted = {_build_name_key(word): word for outline in outlines for line in outline.splitlines()
              for match in [_LINE_QUOTE.search(line)] if match for word in _NAME_TOKEN.findall(match.group(1))}
    spoken = {_build_name_key(word) for word in _NAME_TOKEN.findall(f'{items[position].get("transcriptBefore") or ""} '
                                                           f'{items[position].get("transcriptCurrent") or ""}')}
    lines = [line for field in _HEARD_NAME_FIELDS
             for line in (record.get(field) if isinstance(record.get(field), list) else [record.get(field)])
             if isinstance(line, str)]
    # "Trixity is the only SDK …" opens a sentence with the name the same notes write inside another ("… for Trixity")
    names = {_build_name_key(match.group(0)) for line in lines for bare in [_QUOTED_TEXT.sub(" ", line)]
             for match in _NAME_TOKEN.finditer(bare) if _is_name_token(bare, match)}
    second_reads: dict[str, set[str]] = {}

    def is_shown_as_heard(key: str,
                          spelling: str) -> bool:
        # The screen spelling may be the first reader's misread, copied into an outline too: a terminal's title
        # "DisAssembler" read "DisAssemblar" was written into the facts that had it right (froscon talk 3147, DEFECTS
        # 313). Apple's recognizer reads the frames holding that spelling, and one it reads the heard word on keeps it
        for entry in shown:
            frame_slug = str(entry.get("slug"))
            holds = spelling in str((entry.get("record") or {}).get("screen") or "") or (
                frame_slug == slug and spelling in str(record.get("screen") or "")) or any(
                spelling in line for line in read_screen_lines(paths, frame_slug))
            if not holds or not apple_text_service.is_available():
                continue
            if frame_slug not in second_reads:
                second_reads[frame_slug] = _read_second_words(paths, entry)
            if key in second_reads[frame_slug]:
                return True
        return False

    respelled = dict(record)
    for field in _HEARD_NAME_FIELDS:
        value = record.get(field)
        if isinstance(value, list):
            respelled[field] = [_use_screen_spelling(line, read, quoted, spoken, names, is_shown_as_heard)
                                if isinstance(line, str) else line for line in value]
        elif isinstance(value, str):
            respelled[field] = _use_screen_spelling(value, read, quoted, spoken, names, is_shown_as_heard)

    return respelled


# An editor's line number and its margin bar at a read line's start ("27 | raise ..."): never part of an outline, and
# a critique called the added line "not on the screen" for it (Lecture 9, 2026-10-17)
_EDITOR_GUTTER = re.compile(r"^\s*\d{1,5}\s*[|│¦]\s*")


def read_screen_lines(paths: RunPaths,
                      slug: str) -> list[str]:
    """The lines of text the text reader finds on the frame between its header and footer bands, each of at least two
    words of three letters or more, a top bar's left out and a line read again inside another counted once: the lines
    `## Screen` is checked against, and the lines each frame's message lists. Empty when the reader is not installed or
    the frame is not on disk."""

    return _drop_copied_lines([_EDITOR_GUTTER.sub("", line) for item in list_screen_text_items(paths, slug)
                               if item.kind != TOP_BAR_KIND for line in item.lines])


def validate_batch_summary_record(paths: RunPaths,
                                  summary_slug: str,
                                  frame_count: int = 0) -> ValidationReport:
    """R15 and R18: a batch's account of it and how the batch felt, as the program wrote them from the
    answer. Either is missing when the file or its field is absent or empty. A batch of fewer frames than the least
    sentences needs one sentence a frame (`frame_count`, 0 when not known)."""

    report = ValidationReport(subject=summary_slug)
    record = _read_answer_record(paths, summary_slug, record_file_service.read_batch_record, report,
                                 "BATCH_SUMMARY_UNPARSEABLE")
    if record is None:
        return report
    text = str(record.get("batch_summary") or "").strip()
    report.mark_ran("BATCH_SUMMARY_MISSING")
    if not text:
        report.add("BATCH_SUMMARY_MISSING", "the batch answer has no account of it.",
                   fix='write the batch\'s account under "# Batch", "## Story of the batch", in the third person')

        return report
    sentences = len([sentence for sentence in _SENTENCE_END.split(text) if sentence.strip()])
    words = len(text.split())
    report.mark_ran("BATCH_SUMMARY_SHAPE")
    least = min(SUMMARY_MIN_SENTENCES, frame_count) if frame_count else SUMMARY_MIN_SENTENCES
    if not least <= sentences <= SUMMARY_MAX_SENTENCES or words > SUMMARY_MAX_WORDS:
        report.add("BATCH_SUMMARY_SHAPE",
                   f"`## Story of the batch` has {sentences} sentences and {words} words; it needs {least} to "
                   f"{SUMMARY_MAX_SENTENCES} sentences and at most {SUMMARY_MAX_WORDS} words.",
                   fix="tell the aim, each step in order with what it changed, what was said or asked, and the outcome")
    # R18: the batch's mood. Missing is a rejection, because the field is required and nothing else carries it.
    atmosphere = str(record.get("atmosphere") or "").strip()
    report.mark_ran("BATCH_ATMOSPHERE_MISSING")
    if not atmosphere:
        report.add("BATCH_ATMOSPHERE_MISSING", "the batch answer has no `## Atmosphere`.",
                   fix='say under "# Batch", "## Atmosphere" how the batch feels: the speaker\'s tone, how the '
                       'others take part, any disagreement, or "plain, nobody speaks but the presenter"')
    _check_batch_title(report, paths, summary_slug, " ".join(str(record.get("batch_title") or "").split()))

    return report


def validate_batch_summary(paths: RunPaths,
                           call_slug: str,
                           summary_slug: str,
                           frame_count: int = 0) -> ValidationReport:
    """The call that asked for a batch's account alone: how it ended, then the account it left."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, call_slug),
                                                              call_slug)
    if not report.ok:
        return report

    return validate_batch_summary_record(paths, summary_slug, frame_count)


def validate_record(paths: RunPaths,
                    slug: str,
                    call_slug: str = "") -> ValidationReport:
    """One frame's record alone, for a frame of a group whose call already ended well. `call_slug` names the call that
    answered it, whose log says why a missing record is missing."""

    report = ValidationReport(subject=slug)
    _check_record(report, paths, slug, call_slug)

    return report


def is_facts_asked(paths: RunPaths,
                   slug: str,
                   record: dict) -> bool:
    """Whether `record` would be refused with no facts (FRAME_FACTS_MISSING): a frame answer leaving its facts heading
    out is asked for it again only then, since over the public runs 116 of 138 such frames meant no fact and passed
    (DEFECTS 563, temp/defect-563/measure_left_out_headings.py)."""

    report = ValidationReport(subject=slug)
    build_frames = _list_build_frames(paths, slug) | _list_repeated_frames(paths, slug)
    _check_facts(report, slug, {**record, "facts": []}, _count_spoken_words(paths, slug), build_frames,
                 _is_repeat(paths, slug), _list_earlier_quoted_words(paths, slug))

    return any(finding.code == "FRAME_FACTS_MISSING" for finding in report.findings)


# An outline of another screen: of the words of four letters or more it quotes (at least this many), under this share
# stand on the frame's reading (of at least this many such words). Lecture 7's frame 110, a terminal, was outlined as
# another frame's room view and quoted none of its words; over the 95 frames of the public runs with a stored reading
# the next lowest held 19% and 23%, both right outlines, a build step's one line and a terminal's grey text (DEFECTS 569,
# temp/defect-569/measure_quotes_on_frame.py)
_OTHER_SCREEN_LEAST_QUOTED = 8
_OTHER_SCREEN_LEAST_READ = 15
_OTHER_SCREEN_MOST_SHARE = 0.1
_LONG_WORD = re.compile(r"[A-Za-z]{4,}")


def is_outline_of_another_screen(paths: RunPaths,
                                 slug: str,
                                 record: dict) -> bool:
    """Whether `record`'s own outline quotes words its frame's reading almost never holds, the screen of another frame
    outlined in its place, or holds a line copied from a nearby frame's screen (`list_borrowed_outline_lines`). False
    when the reader reads too little to tell."""

    read = {word.casefold() for line in read_screen_lines(paths, slug) for word in _LONG_WORD.findall(line)}
    own = frame_markdown_utils.strip_program_reading(str(record.get("screen") or ""))
    quoted = [word.casefold() for quote in frame_markdown_utils.list_screen_quotes(own.replace("`", '"'))
              for word in _LONG_WORD.findall(quote)]
    if len(read) >= _OTHER_SCREEN_LEAST_READ and len(quoted) >= _OTHER_SCREEN_LEAST_QUOTED \
            and sum(word in read for word in quoted) / len(quoted) < _OTHER_SCREEN_MOST_SHARE:
        return True

    return bool(list_borrowed_outline_lines(paths, slug, record))


# A line of the outline is another frame's when it quotes this many long words, none of them on its own frame's
# reading, and a frame this near reads this share of them (DEFECTS 574)
_BORROWED_LINE_LEAST_WORDS = 4
_BORROWED_LINE_NEAR_FRAMES = 8
_BORROWED_LINE_NEAR_SHARE = 0.6


def _list_frame_read_words(item: dict) -> set[str]:
    """Every long word phase 2 read on the frame at the recording's size (`screenWords`), its bands and bars included:
    the reading the checks use leaves the top and bottom bands out, and a page title standing there counted as unread
    on three frames that show it."""

    return {word.casefold() for entry in item.get("screenWords") or [] for word in _LONG_WORD.findall(str(entry.get("text") or ""))}


def list_borrowed_outline_lines(paths: RunPaths,
                                slug: str,
                                record: dict) -> list[str]:
    """The lines of `record`'s own outline copied from a nearby frame's screen: each quotes 4 long words or more, none
    read anywhere on its frame, and a frame within 8 of it reads 60% of them, both by phase 2's reading at the
    recording's size; a frame with none is not judged. Lecture 7's frame 131, a page scrolled to
    its end, outlined the command and the page's top that only frame 138 shows, and passed every check, since its
    other lines are its own (DEFECTS 574); over Lecture 7's 119 frames with a stored reading this names frames 95, 110,
    131 and 133, each such a copy, and no line a nearby frame does not read."""

    items = _read_frame_items(paths)
    item = _find_frame_item(items, slug) or {}
    own_read = _list_frame_read_words(item)
    if len(own_read) < _OTHER_SCREEN_LEAST_READ or "index" not in item:
        return []
    near = [entry for entry in items if entry.get("slug") != slug
            and abs(int(entry.get("index", -1_000)) - int(item["index"])) <= _BORROWED_LINE_NEAR_FRAMES]
    borrowed = []
    own = frame_markdown_utils.strip_program_reading(str(record.get("screen") or ""))
    for line in own.split("\n"):
        words = [word.casefold() for quote in frame_markdown_utils.list_screen_quotes(line.replace("`", '"'))
                 for word in _LONG_WORD.findall(quote)]
        if len(words) < _BORROWED_LINE_LEAST_WORDS or any(word in own_read for word in words):
            continue
        for other in near:
            read = _list_frame_read_words(other)
            if len(read) >= _OTHER_SCREEN_LEAST_READ \
                    and sum(word in read for word in words) / len(words) >= _BORROWED_LINE_NEAR_SHARE:
                borrowed.append(line.strip())
                break

    return borrowed


def validate_frame_group(paths: RunPaths,
                         group_slug: str,
                         slugs: list[str]) -> ValidationReport:
    """Several frames answered in one call: how the call ended, then every frame's record, each named."""

    report = chain_response_validator.validate_chain_response(
        call_log_service.build_call_log_path(paths, group_slug), group_slug)
    if not report.ok:
        return report
    for slug in slugs:
        _check_record(report, paths, slug, group_slug)

    return report


def validate_item(paths: RunPaths,
                  slug: str) -> ValidationReport:
    """One frame's call: how it ended, then the record it left."""

    # --- how the call ended: always first, and a finding is an early return -------------------
    report = chain_response_validator.validate_chain_response(
        call_log_service.build_call_log_path(paths, slug), slug)
    if not report.ok:
        return report
    _check_record(report, paths, slug)

    return report


def validate_second_pass_record(paths: RunPaths,
                                pass_slug: str,
                                frames: list[int]) -> ValidationReport:
    """R19: a batch's second look, as the program wrote it from the answer. What it can check is that the answer is
    an answer: it either says there is nothing to change or names at least one addition, and every addition names a
    frame of THIS batch and says what it changed. Whether an addition is an improvement is decided by the guards in
    `utils/addition_utils.py`, record by record, and by reading."""

    report = ValidationReport(subject=pass_slug)
    record = _read_answer_record(paths, pass_slug, record_file_service.read_second_pass_record, report,
                                 "SECOND_PASS_UNPARSEABLE")
    if record is None:
        return report
    additions = [entry for entry in (record.get("additions") or []) if isinstance(entry, dict)]
    report.mark_ran("SECOND_PASS_EMPTY")
    if not additions and not record.get("is_nothing_to_change"):
        report.add("SECOND_PASS_EMPTY", f"{pass_slug}: no addition, and no \"# Nothing to change\"",
                   fix='write one "# Frame N" document per frame you improve, or "# Nothing to change" when there is '
                       "genuinely nothing to add anywhere")

        return report
    report.mark_ran("SECOND_PASS_FRAME_UNKNOWN")
    report.mark_ran("SECOND_PASS_CHANGE_UNSAID")
    for position, addition in enumerate(additions, start=1):
        frame = addition.get("frame")
        if not isinstance(frame, int) or frame not in frames:
            # Rejected: an addition aimed at a frame this batch does not hold cannot be applied at all.
            report.add("SECOND_PASS_FRAME_UNKNOWN",
                       f"{pass_slug}: addition {position} names frame {frame!r}, which is not in this batch",
                       fix="name one of the frames this message gave you: " + ", ".join(str(number) for number in frames))
        elif not str(addition.get("what_changed") or "").strip():
            # A note: the addition still applies, it just costs the reader of the log a guess.
            report.note("SECOND_PASS_CHANGE_UNSAID", f"{pass_slug}: addition {position} does not say what it changed",
                        fix='say under "## What changed", in one sentence, what the notes were missing')

    return report


def validate_batch_follow_up(paths: RunPaths,
                             call_slug: str,
                             question_count: int | None) -> ValidationReport:
    """The call that asked a batch for its open questions (`question_count` None), or for the answers to them (the
    number of questions it was given): how it ended, then what it left. An answer holds every question in exactly one
    of its two sections, and each answered one names its answer after the separator."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, call_slug),
                                                              call_slug)
    if not report.ok:
        return report
    record = _read_answer_record(paths, call_slug, record_file_service.read_batch_record, report,
                                 "BATCH_FOLLOW_UP_UNPARSEABLE")
    if record is None:
        return report
    report.mark_ran("BATCH_FOLLOW_UP_MISSING")
    if not record:
        report.add("BATCH_FOLLOW_UP_MISSING", f"{call_slug}: no `# Batch` document in the answer",
                   fix='write "# Batch" with its "## Open questions" section, and "(none)" when nothing is left open')

        return report
    if question_count is None:
        return report
    report.mark_ran("BATCH_QUESTIONS_LOST")
    report.mark_ran("BATCH_ANSWER_UNSAID")
    still_open, answered = record.get("open_questions") or [], record.get("answered_questions") or []
    set_aside = record.get("not_questions") or []
    # Only a question that came back with neither an answer nor a place among the open ones, or set aside as no
    # question, is lost. A line more than was asked is the worker splitting one question in two or adding one of its
    # own: the merge keeps what it holds, and eleven accepted frames are not blocked for it (2026-10-07).
    held = len(still_open) + len(answered) + len(set_aside)
    if held < question_count:
        report.add("BATCH_QUESTIONS_LOST",
                   f"{call_slug}: {question_count} questions were given and the answer holds {held}",
                   fix='put every question in exactly one section, "## Open questions", "## Answered questions" or '
                       '"## Not a question"')
    for entry in answered:
        # Padded with spaces: an entry stripped of the separator's own space at its end still holds the separator
        if frame_record.ANSWER_SEPARATOR not in f" {entry} ":
            report.add("BATCH_ANSWER_UNSAID", f"{call_slug}: {entry!r} has no answer after it",
                       fix=f'write each answered question as "<the question>{frame_record.ANSWER_SEPARATOR}<the answer>"')

    return report


def validate_critique_record(paths: RunPaths,
                             critique_slug: str,
                             frames: list[int],
                             screen_texts: list[str] = ()) -> ValidationReport:
    """A batch's critique, as the program wrote it from the answer: it either says there is nothing to criticise or
    names at least one rule; every rule has a name, both its lines and at least one frame of THIS batch; and every
    rule is stated for any meeting. Whether the anti-pattern is really there is decided by reading the frames it
    names - the monitor does that, and rejects a rule the frames do not show."""

    report = ValidationReport(subject=critique_slug)
    path = record_file_service.build_record_path(paths, critique_slug)
    report.mark_ran("CRITIQUE_UNREADABLE")
    record = {"is_nothing_to_criticise": False, "rules": []}
    if path.is_file():
        critique, why = critique_markdown_utils.parse_critique(path.read_text(encoding="utf-8"))
        if critique is None:
            report.add("CRITIQUE_UNREADABLE", f"{path.name} could not be read: {why}",
                       fix=f'answer with "## <name>" sections, one per anti-pattern, or '
                           f'"# {critique_record.NOTHING_TO_CRITICISE_TITLE}"')

            return report
        record = critique
    rules = [rule for rule in (record.get("rules") or []) if isinstance(rule, dict)]
    report.mark_ran("CRITIQUE_EMPTY")
    if not rules and not record.get("is_nothing_to_criticise"):
        report.add("CRITIQUE_EMPTY", f"{critique_slug}: no rule, and no \"# {critique_record.NOTHING_TO_CRITICISE_TITLE}\"",
                   fix=f'write one "## <name>" section per anti-pattern, or "# {critique_record.NOTHING_TO_CRITICISE_TITLE}" '
                       "when the notes of this batch hold none. An empty answer is most often one cut at the output "
                       "limit while going over notes that are right: this refusal says nothing was written, never that "
                       "a mistake is there to find. Look at each frame once and answer")

        return report
    for code in ("CRITIQUE_RULE_SHAPE", "CRITIQUE_FRAME_UNKNOWN", "CRITIQUE_RULE_NOT_GENERIC"):
        report.mark_ran(code)
    for position, rule in enumerate(rules, start=1):
        name = str(rule.get("name") or "").strip()
        missing = _list_missing_rule_lines(rule)
        if not name or missing:
            report.add("CRITIQUE_RULE_SHAPE",
                       f"{critique_slug}: rule {position} ({name or 'unnamed'}) is missing "
                       f"{', '.join(missing) or 'its name'}",
                       fix="every rule is a \"## <name>\" section with a line for each of: "
                           + ", ".join(critique_record.RULE_LINES.values())
                           + f', and "{critique_record.SEEN_ON_LABEL}: frames <numbers>"')
            continue
        named = critique_markdown_utils.list_rule_frames(rule)
        unknown = [number for number in named if number not in frames]
        if not named or unknown:
            report.add("CRITIQUE_FRAME_UNKNOWN",
                       f"{critique_slug}: rule {position} ({name}) names "
                       + (f"frame {', '.join(str(number) for number in unknown)}, which is not in this batch"
                          if unknown else "no frame"),
                       fix=f'"{critique_record.SEEN_ON_LABEL}: frames <numbers>", from the frames this message gave '
                           "you: " + ", ".join(str(number) for number in frames))
        _check_rule_is_generic(report, critique_slug, position, rule, screen_texts)

    return report


def validate_critique_collection(paths: RunPaths,
                                 call_slug: str,
                                 group_names: list[str]) -> ValidationReport:
    """Phase 6's call, then the rule set it left."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, call_slug),
                                                              call_slug)
    if not report.ok:
        return report

    return validate_critique_collection_record(paths, group_names)


def validate_critique_collection_record(paths: RunPaths,
                                        group_names: list[str]) -> ValidationReport:
    """Phase 6's rule set, as the program wrote it from the answer: the file is there, every rule it kept has both its
    lines, and a rule names no frame of any meeting. Keeping NO group is accepted - the instruction says dropping is a
    real answer - but a file with no rule at all and groups to word is the model answering nothing."""

    report = ValidationReport(subject=output_contract.CRITIQUE_COLLECTION_FILE)
    path = critique_collection_service.build_rule_set_path(paths)
    report.mark_ran("CRITIQUE_COLLECTION_MISSING")
    if not path.is_file():
        report.add("CRITIQUE_COLLECTION_MISSING", f"{path.name} was not written",
                   fix=f'answer with "# {critique_record.CRITIQUE_TITLE}" and one "## <name>" section per rule you keep')

        return report
    rule_set, why = critique_markdown_utils.parse_critique(path.read_text(encoding="utf-8"))
    report.mark_ran("CRITIQUE_COLLECTION_UNREADABLE")
    if rule_set is None:
        report.add("CRITIQUE_COLLECTION_UNREADABLE", f"{path.name} could not be read: {why}",
                   fix="one \"## <name>\" section per rule, each with its own lines")

        return report
    rules = rule_set.get("rules") or []
    report.mark_ran("CRITIQUE_COLLECTION_RULE_SHAPE")
    report.mark_ran("CRITIQUE_COLLECTION_RULE_NOT_GENERIC")
    for position, rule in enumerate(rules, start=1):
        missing = _list_missing_rule_lines(rule)
        if missing:
            report.add("CRITIQUE_COLLECTION_RULE_SHAPE",
                       f"{path.name}: rule {position} ({rule.get('name')}) is missing {', '.join(missing)}",
                       fix="every rule has a line for each of: " + ", ".join(critique_record.RULE_LINES.values()))
            continue
        _check_rule_is_generic(report, path.name, position, rule, [], "CRITIQUE_COLLECTION_RULE_NOT_GENERIC")
    report.mark_ran("CRITIQUE_COLLECTION_NO_RULE_KEPT")
    if group_names and not rules and not rule_set.get("is_nothing_to_criticise"):
        report.add("CRITIQUE_COLLECTION_NO_RULE_KEPT",
                   f"{path.name}: none of the {len(group_names)} anti-pattern(s) was worded, and the answer does not "
                   f'say "# {critique_record.NOTHING_TO_CRITICISE_TITLE}"',
                   fix="word each group you keep as a rule, or say that one heading when not one of them is worth "
                       "warning a later worker about")

    return report


def validate_critique(paths: RunPaths,
                      call_slug: str,
                      critique_slug: str,
                      frames: list[int],
                      screen_texts: list[str] = ()) -> ValidationReport:
    """The call that asked a batch to criticise its own notes: how it ended, then the rules it left. The critique's
    own call is read, logged under its slug: read under the batch's, a critique stopped mid-thought was refused as
    an empty answer (2026-10-13)."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, critique_slug),
                                                              critique_slug)
    if not report.ok:
        return report

    return validate_critique_record(paths, critique_slug, frames, screen_texts)


def validate_second_pass(paths: RunPaths,
                         call_slug: str,
                         pass_slug: str,
                         frames: list[int]) -> ValidationReport:
    """The call that asked for a batch's second look: how it ended, then the answer it left, its own call read."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, pass_slug),
                                                              pass_slug)
    if not report.ok:
        return report

    return validate_second_pass_record(paths, pass_slug, frames)
