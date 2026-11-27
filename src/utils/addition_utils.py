"""Applying one frame addition to one accepted record, with the guards that keep a second look from losing work.
REQUIREMENTS.md R19.

The second pass exists because a model told its notes looked thin finds what it dropped. The same model, re-reading
its own answer, can also talk a good record into a worse one: shorten a description it already got right, redraw a
diagram it was meant to extend, replace a list with a shorter one. So an addition may only ADD to a list, and may
replace a text under one rule each, stated here, once, where both the applier and its tests read them. The one way out
is a part the addition names under "Not on this frame": a part the first pass put on this frame that only another frame
shows. The screen's outline and the diagram are then judged without the lines that name it. A line of the diagram the
addition names under "Wrong in the diagram" (an arrow drawn backwards) may likewise be left out, and must not return.
A fact or a take-away changes only by an edit naming its id under "Edits" (replace or remove): the program applies each
as named and works out nothing from the wording (DEFECTS 357).

Pure functions over dicts: no I/O, no store, no context. The node decides what to do with the result.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from schemas import frame_record as schemas_frame_record
from utils import frame_markdown_utils
from utils import mermaid_text_utils

__all__ = ["APPENDED_FIELDS", "REPLACED_FIELDS", "apply_addition", "describe_refusals", "list_distinct_entries"]

# `added_<field>` in the addition -> the record's list it is appended to. Appended and de-duplicated, never replaced:
# a second look may add and must not be able to lose an accepted entry.
APPENDED_FIELDS = ("take_aways", "facts", "open_questions", "action_items", "questions_asked", "tags",
                   "builds_on", "contradicts_earlier", "urls", "screenshots")
# The texts an addition may replace outright, because a fuller answer is strictly better and nothing is lost.
REPLACED_FIELDS = ("screen_kind", "speech_visual_alignment")
# The texts an addition may replace only under a guard, named with the guard's reason. The screen's outline is
# replaced whole, never merged: a second look writes every part again, and a part moved under another is not a new one.
_GUARDED_FIELDS = ("third_person_description", "screen", "diagram", "as_markdown")
# The guarded texts a critique can name wrong by their heading, and the fix then write shorter: a fix took a sentence
# the critique found out of an account, 47 words against 56, and the length guard kept the sentence (lecture-1 frame
# 135, 2026-10-14). A diagram keeps its own judge
# The diagram too: a critique named frame 9's diagram wrong (a box skipped, two steps swapped), the repair drew it right
# without listing each old line as wrong, and the redraw was refused, so the wrong diagram reached a chapter
# (38c3-fault-injection, DEFECTS 649)
CRITIQUED_FIELDS = ("third_person_description", "screen", "diagram")
# The words by which a rule names each of those texts besides its heading, in its Where line for the frame or, with no
# such line, in its own name and description: "frame 42: Outlines old help lines ..." named the outline, and the fix's
# outline holding the new output was refused as shorter (froscon 3211 frames 42 to 44, DEFECTS 209)
_FIELD_WORDS = {"third_person_description": "what happens", "screen": "outline", "diagram": "diagram"}
# How many words shorter a replacement may be and still count as full: a reworded line, not a thinned outline
_SLACK_WORDS = 3
# How the console names a guarded field
_FIELD_NAMES = {"screen": "screen outline", "third_person_description": "narration", "as_markdown": "markdown block"}
# A top-level part of the outline that is the meeting tool's camera, by its kind (the words before the first colon).
# second_pass.md calls it never a miss, and a self-critique added one anyway in 3 of 10 batches of one run, each time
# counted as a frame that gained notes.
_MEETING_TOOL_PART = re.compile(r"^\s*-\s*[^:]*\b(camera|webcam|video (feed|box|tile|panel|overlay)|participant "
                                r"(video|tile|camera|box)|name (label|tag))\b[^:]*:", re.IGNORECASE)


# A take-away's "→ nothing on screen" ending is form, not content: a fix pass told by a wrong critique to add it wrote each
# closing "Take aways:" line again with it, and both were kept (lecture-1 frames 22-23, 2026-10-13)
_NOTHING_ON_SCREEN_TAIL = re.compile(r"\s*→\s*nothing on (?:the )?screen\s*$", re.IGNORECASE)


# An added entry this close to a held one corrects it: at least this many words, at most this many changed, and no
# changed word holding a digit, since two facts that differ in a value are two facts (`f(5.2)` and `f(5.9)`)
_CORRECTION_MIN_WORDS = 8
# A take-away's opening quote and its arrow: `"<words said>" → <part of the screen>`, backticks around it allowed
# Its quote ends at the arrow, or at a colon when a repair writes the take-away so: "this is <words>": ... stood beside
# the take-away it corrected, the same words with an arrow (release test 1, frame 132; DEFECTS 347)
_TAKE_AWAY_QUOTE = re.compile(r'^\s*(?:-\s*)?`?\s*["“]([^"”]+)["”]\s*(?:→|:)')


def _normalise(entry: Any) -> str:
    """One entry's identity for de-duplication: its text, or a dict's own values joined, folded and squeezed, with a
    "→ nothing on screen" ending taken off."""

    if isinstance(entry, dict):
        return "|".join(_normalise(part) for part in entry.values())

    return " ".join(_NOTHING_ON_SCREEN_TAIL.sub("", str(entry)).split()).casefold()


def list_distinct_entries(entries: list[Any]) -> list[Any]:
    """`entries` with each one that repeats an earlier one left out, by the identity `_normalise` gives."""

    return _append([], entries)


def _list_words(entry: Any) -> list[str]:
    return re.findall(r"\w+", _normalise(entry))


def _is_said_within(entry: Any,
                    held: list[Any]) -> bool:
    """Whether `entry`, of `_CORRECTION_MIN_WORDS` words or more, is a held entry with words taken out and none added:
    it says nothing new. A fix pass wrote a held fact again without its last three words, and both stood
    (38c3-fault-injection frame 21, 2026-10-15)."""

    words = _list_words(entry)
    if not isinstance(entry, str) or len(words) < _CORRECTION_MIN_WORDS:
        return False
    for other in held:
        remaining = iter(_list_words(other))
        if isinstance(other, str) and all(word in remaining for word in words):
            return True

    return False


def _append(accepted: list[Any],
            added: list[Any]) -> list[Any]:
    seen = {_normalise(entry) for entry in accepted}
    kept = list(accepted)
    for entry in added:
        key = _normalise(entry)
        if key and key not in seen and not _is_said_within(entry, kept):
            seen.add(key)
            kept.append(entry)

    return kept


# A held entry a critique rule's Where line quotes on this frame, and an added entry opening with its first
# `_REWRITE_OPENING_WORDS` words, is that entry written again: a fix pass rewrote a fact the critique quoted ("a pixel
# art illustration of a golden ..." with another object, the keys of a list read again), named nothing under
# "## Wrong facts", and both stood (froscon 3147 frames 96 and 100, DEFECTS 342). Quoted means a run of
# `_QUOTED_RUN_WORDS` of its words stands in the line
_REWRITE_OPENING_WORDS = 6
_QUOTED_RUN_WORDS = 5


def _read_first_question(entry: Any) -> str:
    """A question asked's first question, folded, up to its first question mark; "" when it holds none."""

    text = str(entry.get("question") if isinstance(entry, dict) else entry)
    head, mark, _ = text.partition("?")

    return " ".join(head.split()).casefold() if mark else ""


def _replace_answered(held: list[Any],
                      added: list[Any]) -> tuple[list[Any], list[Any]]:
    """`held` with each question asked that an added one opens with the same question replaced by it: a fix wrote a
    question again with how it was answered, and the old "unanswered" one stood beside it (lecture-1 frame 185,
    2026-10-14)."""

    kept = list(held)
    rest = []
    for entry in added:
        question = _read_first_question(entry)
        index = next((index for index, old in enumerate(kept) if question and _read_first_question(old) == question
                      and _normalise(old) != _normalise(entry)), None)
        if index is None:
            rest.append(entry)
        else:
            kept[index] = entry

    return kept, rest


def _is_text_full_enough(accepted: str,
                         proposed: str) -> bool:
    """A replacement narration or screen outline must not be shorter in words than the one it replaces, give or take a
    rewording: the second pass is there to make them fuller, and a shorter one is the drift this module exists to stop.
    The slack (3 words, or a tenth) lets a corrected line through: three corrections one or two words shorter were
    refused in one batch (2026-10-01), one of them a screen the notes called "loading" that showed a dialog."""

    accepted_words = len(accepted.split())

    return len(proposed.split()) >= accepted_words - max(_SLACK_WORDS, accepted_words // 10)


def _is_diagram_extended(accepted: str,
                         proposed: str) -> bool:
    """A diagram is EXTENDED, never redrawn (CONTEXT.md): every non-empty line of the accepted one, ignoring
    indentation, must still be in the proposed one."""

    if not mermaid_text_utils.is_mermaid(mermaid_text_utils.strip_fence(proposed)):
        return False
    kept = {line.strip() for line in mermaid_text_utils.strip_fence(accepted).splitlines() if line.strip()}
    proposed_lines = {line.strip() for line in mermaid_text_utils.strip_fence(proposed).splitlines() if line.strip()}

    return kept <= proposed_lines


# A flowchart's words that are no box: its kind, its directions and its statements
_FLOWCHART_WORDS = frozenset("flowchart graph subgraph end direction TB TD BT LR RL classDef class style click "
                             "linkStyle".split())
_FLOWCHART_ID = re.compile(r"\b[A-Za-z_]\w*\b")
# A box's label, an edge's label and any quoted text: what is left of a line is its box ids and its arrows
_FLOWCHART_LABEL = re.compile(r'"[^"]*"|\|[^|]*\||\[[^\]]*\]|\([^)]*\)|\{[^}]*\}')
# A box whose label is read again often gets an id from its new label: measured on four such corrections, each renamed
# one id of eighteen, with no box and no arrow fewer. A redrawn diagram renames most of them
_RENAMED_BOX_SHARE = 0.25


def _list_box_ids(diagram: str) -> set[str]:
    """The ids of a flowchart's boxes and groups."""

    lines = mermaid_text_utils.strip_fence(diagram).splitlines()[1:]

    return {word for line in lines for word in _FLOWCHART_ID.findall(_FLOWCHART_LABEL.sub(" ", line))
            if word not in _FLOWCHART_WORDS}


def _is_diagram_corrected(accepted: str,
                          proposed: str) -> bool:
    """Is the proposed flowchart the accepted one corrected, never thinned: as many boxes, at most a quarter of their
    ids renamed, and at least as many arrows? A box label read again or an arrow turned round changes lines the worker did not name under
    `## Wrong in the diagram`: three second looks in one run corrected labels and arrows in place, and each was refused
    as a redrawn diagram, so the misreading stayed."""

    source = mermaid_text_utils.strip_fence(proposed)
    if not source.split() or source.split()[0] not in ("flowchart", "graph"):
        return False

    accepted_ids, proposed_ids = _list_box_ids(accepted), _list_box_ids(proposed)

    return (len(proposed_ids) >= len(accepted_ids)
            and len(accepted_ids - proposed_ids) <= _RENAMED_BOX_SHARE * len(accepted_ids)
            and mermaid_text_utils.count_arrows(proposed) >= mermaid_text_utils.count_arrows(accepted))


# A text in single, double or curly quotes inside a named line
# A text an outline quotes as code, in backticks: a part quoted so is named by it like a part in double quotes
_CODE_SPAN = re.compile(r"`([^`]+)`")
_INNER_QUOTE = re.compile(r"""['"\u201c\u201e]([^'"\u201c\u201d\u201e]+)['"\u201d\u201c]""")


def _drop_named_sentences(text: str,
                          sentences: list[str]) -> str:
    """The narration without each sentence the addition quotes whole as said over another frame; a part of a sentence
    takes out nothing."""

    for sentence in sentences:
        sentence = " ".join(sentence.split())
        if sentence and sentence in " ".join(text.split()):
            text = " ".join(" ".join(text.split()).replace(sentence, " ").split())

    return text


_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset("the a an of to in on is are be it its and or for by with as at this that from can".split())


def _list_content_words(text: str) -> set[str]:
    """The words that carry `text`, folded, a plural's "s" off, the small ones left out."""

    return {word[:-1] if len(word) > 3 and word.endswith("s") else word
            for word in _WORD.findall(text.casefold()) if word not in _STOP_WORDS}


# A text a fact quotes, in backticks or double quotes
_QUOTED_TEXT = re.compile(r"`([^`]+)`|[\"“]([^\"”]+)[\"”]")


def _list_line_quotes(text: str) -> list[str]:
    """Every text a line or a named part quotes, in quotes or in backticks."""

    return _INNER_QUOTE.findall(text) + _CODE_SPAN.findall(text)


def _list_named_parts(accepted_screen: str,
                      wrong_parts: list[str]) -> list[str]:
    """The parts the addition says are not on this frame that the accepted outline quotes: a part it does not quote
    cannot have been put there, and a loose word would excuse every line."""

    quoted = {quote.casefold() for quote in frame_markdown_utils.list_screen_quotes(accepted_screen)
              + _CODE_SPAN.findall(accepted_screen)}
    # A part named as a whole described line or a diagram line (`box 'Archive': right`, `C["Archive"] --> A`) names it
    # by the texts it quotes inside it
    named = [inner for part in wrong_parts
             for inner in ([part] if part.casefold() in quoted else _list_line_quotes(part))]

    return [part for part in named if part.casefold() in quoted]


def _find_kind_line(accepted_screen: str,
                    part: str) -> int | None:
    """The index of the one outline line whose kind (the words before its colon) is `part`, when that line quotes no
    text and holds no line under it; None when no line or several lines are so: a part with no text of its own (an
    arrow, a line, a shape) has only its kind to be named by, and a kind holding other parts would excuse them too."""

    lines = accepted_screen.splitlines()
    found = []
    for index, line in enumerate(lines):
        kind = line.strip().removeprefix("- ").split(":", 1)[0].strip()
        if kind.casefold() != part.strip().casefold() or _INNER_QUOTE.search(line):
            continue
        indent = len(line) - len(line.lstrip())
        following = next((later for later in lines[index + 1:] if later.strip()), "")
        if following and len(following) - len(following.lstrip()) > indent:
            continue
        found.append(index)

    return found[0] if len(found) == 1 else None


def _drop_kind_lines(text: str,
                     kinds: list[str]) -> str:
    """The outline without the one line each named kind stands for, as `_find_kind_line` finds it."""

    drop = {index for kind in kinds if (index := _find_kind_line(text, kind)) is not None}

    return "\n".join(line for index, line in enumerate(text.splitlines()) if index not in drop)


def _drop_lines_naming(text: str,
                       parts: list[str]) -> str:
    """The text without each line that names one of the parts, and without the lines indented under such a line."""

    kept: list[str] = []
    dropped_indent: int | None = None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if dropped_indent is not None and line.strip() and indent > dropped_indent:
            continue
        dropped_indent = None
        # A part is named by a quoted text of the line, as `_list_named_parts` found it: "Log" never excuses "Login"
        quoted = {quote.casefold() for quote in _list_line_quotes(line)}
        if any(part.casefold() in quoted for part in parts):
            dropped_indent = indent
            continue
        kept.append(line)

    return "\n".join(kept)


def _drop_meeting_tool_parts(screen: str) -> str:
    """The outline without each part, at any depth, that is the meeting tool's camera, and without the lines under it:
    a tile nested beside a slide, its person's name label quoted, was kept (lecture-1 frame 109, 2026-10-13)."""

    kept: list[str] = []
    dropped_indent: int | None = None
    for line in screen.splitlines():
        indent = len(line) - len(line.lstrip())
        if dropped_indent is not None and line.strip() and indent > dropped_indent:
            continue
        dropped_indent = indent if line.strip() and _MEETING_TOOL_PART.match(line) else None
        if dropped_indent is None:
            kept.append(line)

    return "\n".join(kept).strip()


def _drop_wrong_diagram_lines(diagram: str,
                              wrong_lines: list[str]) -> str:
    """The accepted diagram without each line the addition names as wrong, matched whole, ignoring indentation: a line
    it does not hold names nothing, so a loose fragment can excuse no line."""

    wrong = {line.strip() for line in wrong_lines if line.strip()}

    return "\n".join(line for line in diagram.splitlines() if line.strip() not in wrong)


def _judge_diagram(accepted: str,
                   proposed: str,
                   wrong_diagram_lines: list[str]) -> str:
    """Empty when the new diagram extends the accepted one; otherwise why not. A line named as wrong may go, and must
    not come back (2026-10-06: an arrow drawn backwards could not be corrected by a second look that may only extend)."""

    # A line named wrong that is empty names nothing: it never refuses a diagram with a blank line in it
    wrong_lines = [line for line in wrong_diagram_lines if line.strip()]
    accepted = _drop_wrong_diagram_lines(accepted, wrong_lines)
    proposed_rows = {row.strip() for row in mermaid_text_utils.strip_fence(proposed).splitlines()}
    kept_wrong = [line for line in wrong_lines if line.strip() in proposed_rows]
    if kept_wrong:
        return f"the diagram still holds a line named wrong: {kept_wrong[0]!r}"

    if _is_diagram_extended(accepted, proposed) or _is_diagram_corrected(accepted, proposed):
        return ""

    return "the diagram would be redrawn, not extended"


def _judge_text(field: str,
                accepted: str,
                proposed: str) -> str:
    """Empty when the new text is as full as the accepted one; otherwise why not."""

    if _is_text_full_enough(accepted, proposed):
        return ""

    return (f"its {_FIELD_NAMES.get(field, field.replace('_', ' '))} is shorter "
            f"({len(proposed.split())} words, was {len(accepted.split())})")


def _judge_guarded(field: str,
                   accepted: str,
                   proposed: str,
                   wrong_parts: list[str],
                   wrong_diagram_lines: list[str],
                   wrong_kinds: list[str],
                   wrong_narration: list[str]) -> str:
    """Empty when the replacement may be taken; otherwise why it is refused, in the words the console prints. The
    accepted text is judged without the lines naming a part the addition says is not on this frame, and an outline
    without the meeting tool's camera parts, which the proposed one is stripped of too."""

    accepted = _drop_lines_naming(accepted, wrong_parts) if wrong_parts else accepted
    if field == "third_person_description" and wrong_narration:
        accepted = _drop_named_sentences(accepted, wrong_narration)
    if field == "screen" and wrong_kinds:
        accepted = _drop_kind_lines(accepted, wrong_kinds)
    if field == "diagram":
        return _judge_diagram(accepted, proposed, wrong_diagram_lines)
    # ... and without the program's part of it, which the worker was not shown (`render_notes_to_review`)
    if field == "screen":
        accepted = frame_markdown_utils.strip_program_reading(_drop_meeting_tool_parts(accepted))

    return _judge_text(field, accepted, proposed)


def list_critiqued_fields(rules: list[dict[str, Any]],
                          frame: int,
                          screen: str = "") -> frozenset[str]:
    """The texts (`CRITIQUED_FIELDS`) a critique rule's "Where" line names on `frame` by their heading, such as
    `frame 12: "What happens" section claiming ...`, and the outline when that line quotes a text of the frame's
    `screen`."""

    # "Frame 58, Screen outline" names frame 58 as much as "frame 58: ..." does: the fix's corrected outline was
    # refused as shorter (lecture-1 frame 58, 2026-10-14)
    on_frame = re.compile(rf"\s*frame {frame}\b", re.IGNORECASE)
    lines = [str(where) for rule in rules for where in (rule.get("where") or []) if on_frame.match(str(where))]
    named = {field for field in CRITIQUED_FIELDS for line in lines
             if schemas_frame_record.FRAME_SECTIONS[field][0].casefold() in line.casefold()
             or _FIELD_WORDS[field] in line.casefold()}
    # A rule seen on the frame with no Where line for it names the text by its own words: "a position in the screen
    # outline" (lecture-1 frame 151, 2026-10-14)
    unplaced = [" ".join([str(rule.get("name") or ""), str(rule.get("what_it_is") or "")]).casefold() for rule in rules
                if frame in (rule.get("frames") or []) and not any(on_frame.match(str(where))
                                                                    for where in (rule.get("where") or []))]
    named |= {field for field in CRITIQUED_FIELDS for text in unplaced if _FIELD_WORDS[field] in text}
    # "frame 40: the `- entry "Get FEN status": visible below` line (image shows `rfazin`)" names the outline by its own
    # line: the fix's outline, corrected and shorter, was refused, and the misread lines stayed (froscon 3211, DEFECTS 286)
    if screen and any(len(quote) >= _LEAST_QUOTED_LETTERS and quote in screen
                      for line in lines for quote in _list_line_quotes(line)):
        named.add("screen")

    return frozenset(named)


# The shortest text of a Where line that names an outline line by quoting it
_LEAST_QUOTED_LETTERS = 4


# The notes a correction is carried into. A quote of the speech is what was said and stays: every quote of the
# alignment note, and the quote a take-away opens with (`"<words>" → <part of the screen>: ...`), whose screen part
# after it quotes the screen
_CORRECTED_FIELDS = ("facts", "take_aways", "open_questions", "action_items", "speech_visual_alignment",
                     "third_person_description", "as_markdown")
_SPEECH_QUOTING_FIELDS = ("take_aways", "speech_visual_alignment")
_SPEECH_OPENING_FIELDS = ("take_aways",)
_CORRECTION_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_]*")
_SPEECH_QUOTE = re.compile(r'"[^"]*"|`[^`]*`')
# A correction is a few words the repair wrote another way: a longer change is a rewrite, not a misread
_MOST_CORRECTED_TOKENS = 3
_LEAST_CORRECTED_LETTERS = 3


def _list_corrections(replaced: list[tuple[str, str]],
                      screen: str) -> dict[str, str]:
    """Each run of words a replace wrote another way, old text to new, read from the old and the new line by their
    words ("softwarepublication" to "software publication", "VTS2" to "VT52"). A run is a correction only when it is at
    most three words each way, its old text at least four letters long and nowhere in the corrected outline `screen`,
    and its new text inside a quote of that outline, so a misread of the frame's text corrected to what it shows; a
    part moved ("upper right" to "upper left") is no misread. An old text corrected two ways is left out."""

    quoted = "\n".join(_list_outline_quotes(screen))
    corrections: dict[str, set[str]] = {}
    for old_line, new_line in replaced:
        old_tokens = list(_CORRECTION_TOKEN.finditer(str(old_line)))
        new_tokens = list(_CORRECTION_TOKEN.finditer(str(new_line)))
        matcher = difflib.SequenceMatcher(a=[token.group(0) for token in old_tokens],
                                          b=[token.group(0) for token in new_tokens], autojunk=False)
        for kind, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if kind != "replace" or old_end - old_start > _MOST_CORRECTED_TOKENS \
                    or new_end - new_start > _MOST_CORRECTED_TOKENS:
                continue
            old_text = str(old_line)[old_tokens[old_start].start():old_tokens[old_end - 1].end()]
            new_text = str(new_line)[new_tokens[new_start].start():new_tokens[new_end - 1].end()]
            if len(old_text) >= _LEAST_CORRECTED_LETTERS and old_text not in screen and new_text in quoted:
                corrections.setdefault(old_text, set()).add(new_text)

    return {old: next(iter(news)) for old, news in corrections.items() if len(news) == 1}


def _write_corrections(text: str,
                       corrections: dict[str, str],
                       is_speech_kept: bool,
                       is_opening_quote_only: bool) -> str:
    """`text` with each corrected old text written as corrected, as a whole word; outside its quotes when
    `is_speech_kept`, or outside its opening quote alone when `is_opening_quote_only`, since a quote of the speech is
    what was said."""

    if not corrections:
        return text
    pattern = re.compile("|".join(rf"(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])"
                                  for old in sorted(corrections, key=len, reverse=True)))
    quotes = list(_SPEECH_QUOTE.finditer(text)) if is_speech_kept else []
    if is_opening_quote_only:
        quotes = [quote for quote in quotes[:1] if not text[:quote.start()].strip()]
    pieces, end = [], 0
    for quote in [*quotes, None]:
        stop = quote.start() if quote else len(text)
        pieces.append(pattern.sub(lambda match: corrections[match.group(0)], text[end:stop]))
        if quote:
            pieces.append(quote.group(0))
            end = quote.end()

    return "".join(pieces)


# The id the repair is shown in front of a line, "[F158.2] ", which a line it adds copies
_LINE_ID_IN_FRONT = re.compile(r"^\s*\[[FTQ]\d+\.\d+\]\s*")
# A written line of an outline, and the id the repair was shown after a line's dash, which an outline written whole
# again may keep (DEFECTS 420)
_OUTLINE_PART = re.compile(r"^ *[-*+] \S")
_SCREEN_ID_AFTER_DASH = re.compile(r"^(?P<head> *[-*+] )\[O\d+\.\d+\]\s*", re.MULTILINE)
# The spaces a nested part of an outline is indented by, past the part it sits in
_OUTLINE_INDENT = 2


def _apply_screen_edits(record: dict[str, Any],
                        frame: int,
                        edits: list[dict[str, str]],
                        changes: list[str],
                        replaced: list[tuple[str, str]]) -> list[str]:
    """`record`'s outline with each edit applied to the line its id names (`build_screen_line_id`, counted as the
    repair was shown the outline, the program's part left out): a replace writes the new text after the line's indent
    and dash, and adds the line's old and new text to `replaced`; a remove takes the line and every line inside it out;
    an add under writes a new part after the line and every line inside it, one level deeper (DEFECTS 572). Returns a
    refusal for each edit naming no line."""

    lines = str(record.get("screen") or "").split("\n")
    # The program's part comes last, so the repair's numbers count the outline's first written lines
    shown = sum(bool(_OUTLINE_PART.match(line))
                for line in frame_markdown_utils.strip_program_reading(str(record.get("screen") or "")).split("\n"))
    written = [index for index, line in enumerate(lines) if _OUTLINE_PART.match(line)]
    places = {frame_markdown_utils.build_screen_line_id(frame, place): index
              for place, index in enumerate(written[:shown], start=1)}
    removed: set[int] = set()
    # The parts each add under writes, by the index of the line they go before
    added: dict[int, list[str]] = {}
    refusals: list[str] = []
    for edit in edits:
        index = places.get(str(edit.get("id") or ""))
        if index is None:
            refusals.append(f'edit {edit.get("action")} {edit.get("id")}: it names no line of frame {frame}\'s outline')
            continue
        indent = len(lines[index]) - len(lines[index].lstrip())
        inside = next((later for later in range(index + 1, len(lines))
                       if lines[later].strip() and len(lines[later]) - len(lines[later].lstrip()) <= indent), len(lines))
        if edit.get("action") == "remove":
            removed.update(range(index, inside))
            continue
        text = re.sub(r"^[-*+]\s+(?:\[O\d+\.\d+\]\s*)?", "", str(edit.get("text") or "").strip())
        if text and edit.get("action") == frame_markdown_utils.ADD_UNDER_EDIT:
            added.setdefault(inside, []).append(f'{" " * (indent + _OUTLINE_INDENT)}- {text}')
            continue
        if text:
            new_line = f'{" " * indent}- {text}'
            if new_line != lines[index]:
                replaced.append((lines[index], new_line))
            lines[index] = new_line
    edited: list[str] = []
    for index, line in enumerate(lines):
        edited += added.get(index, [])
        if index not in removed:
            edited.append(line)
    kept = "\n".join(edited + added.get(len(lines), []))
    if kept != str(record.get("screen") or ""):
        record["screen"] = kept
        changes.append("screen edited")

    return refusals


# A line written with the id of a line the notes hold: "[T19.2] <the line as it should read>"
_HELD_ID_IN_FRONT = re.compile(r"^\s*\[(?P<prefix>[FT])(?P<frame>\d+)\.(?P<place>\d+)\]\s*(?P<text>.*)$", re.DOTALL)


def _convert_held_ids_into_replaces(record: dict[str, Any],
                                    addition: dict[str, Any]) -> dict[str, Any]:
    """The addition with each fact or take-away line that is written with the id of a line this frame's notes hold
    made a replace of that line. The id is the line's name: a repair that wrote its take-aways again in full with the
    ids in front, three lines corrected, got the corrected lines appended beside the old ones on 3 frames of one batch
    (DEFECTS 453). An id past the notes' last line is a new line and stays an added one; an edit already naming the id
    wins."""

    frame = int(addition.get("frame") or 0)
    edits = list(addition.get("edits") or [])
    named = {str(edit.get("id") or "") for edit in edits}
    moved = {}
    for field, source in (("added_facts", "facts"), ("added_take_aways", "take_aways")):
        prefix, held_count = frame_markdown_utils.LINE_ID_PREFIXES[source], len(record.get(source) or [])
        kept = []
        for entry in addition.get(field) or []:
            match = _HELD_ID_IN_FRONT.match(str(entry))
            if match and match["prefix"] == prefix and int(match["frame"]) == frame \
                    and 1 <= int(match["place"]) <= held_count and match["text"].strip():
                line_id = f"{prefix}{frame}.{int(match['place'])}"
                if line_id not in named:
                    edits.append({"action": "replace", "id": line_id, "text": match["text"].strip()})
                    named.add(line_id)
                continue
            kept.append(entry)
        if addition.get(field):
            moved[field] = kept

    return {**addition, **moved, "edits": edits}


def _apply_edits(record: dict[str, Any],
                 frame: int,
                 edits: list[dict[str, str]],
                 changes: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """`record` with each edit applied to the line its id names (`frame_markdown_utils.build_line_id`): a replace writes
    the new text there, a remove takes the line out. Returns each replaced line's old and new text, and a refusal for
    each edit naming no line of this frame."""

    places = {frame_markdown_utils.build_line_id(field, frame, place): (field, place - 1)
              for field in frame_markdown_utils.LINE_ID_PREFIXES
              for place in range(1, len(record.get(field) or []) + 1)}
    held = {field: list(record.get(field) or []) for field in frame_markdown_utils.LINE_ID_PREFIXES}
    removed: dict[str, set[int]] = {field: set() for field in held}
    replaced: list[tuple[str, str]] = []
    refusals: list[str] = []
    screen_edits = [edit for edit in edits
                    if str(edit.get("id") or "").startswith(frame_markdown_utils.SCREEN_LINE_PREFIX)]
    refusals += _apply_screen_edits(record, frame, screen_edits, changes, replaced)
    # Two replaces writing one text name its line ambiguously, and neither is applied: a repair answered from a summary
    # wrote "replace F61.2" and "[F61.3]" with one new fact, and the two lines became it, F61.2's own fact lost
    # (Lecture 7, DEFECTS 561)
    texts = [" ".join(str(edit.get("text") or "").split()) for edit in edits if edit.get("action") == "replace"]
    twice = {text for text in texts if text and texts.count(text) > 1}
    for edit in edits:
        if edit in screen_edits:
            continue
        if edit.get("action") == "replace" and " ".join(str(edit.get("text") or "").split()) in twice:
            refusals.append(f'edit replace {edit.get("id")}: another edit of frame {frame} writes the same text, so '
                            "neither is applied")
            continue
        if edit.get("action") == frame_markdown_utils.IGNORED_EDIT:
            text = str(edit.get("text"))
            what = ("a section of frame {frame}'s repair names no section and was ignored with its lines"
                    if text.startswith("## ") else "an edit line of frame {frame} is in no edit's shape and was ignored")
            refusals.append(f'{what.format(frame=frame)}: "{text[:100]}"')
            continue
        if edit.get("action") == frame_markdown_utils.ADD_UNDER_EDIT:
            refusals.append(f'edit add under {edit.get("id")}: a part is added under an outline line only; a new line '
                            "of the other sections is written under its section")
            continue
        place = places.get(str(edit.get("id") or ""))
        if place is None:
            refusals.append(f'edit {edit.get("action")} {edit.get("id")}: it names no line of frame {frame}\'s notes')
            continue
        field, index = place
        if edit.get("action") == "remove":
            removed[field].add(index)
        elif str(edit.get("text") or "").strip() and edit["text"] != held[field][index]:
            replaced.append((str(held[field][index]), edit["text"]))
            held[field][index] = edit["text"]
    for field, lines in held.items():
        kept = [line for index, line in enumerate(lines) if index not in removed[field]]
        if kept != list(record.get(field) or []):
            record[field] = kept
            changes.append(f"{field} edited")

    return replaced, refusals


def apply_addition(accepted: dict[str, Any],
                   addition: dict[str, Any],
                   critiqued_fields: frozenset[str] = frozenset()) -> tuple[dict[str, Any], list[str], list[str]]:
    """Apply one addition: `(record, changes, refusals)`. `changes` names each field that moved, `refusals` each
    replacement a guard turned down with its reason. A text the batch's critique named wrong on this frame
    (`critiqued_fields`, `list_critiqued_fields`) may come back shorter: the critique and the fix both say it held
    something wrong, and the record is judged again after. The accepted record is never mutated."""

    record = dict(accepted)
    changes: list[str] = []
    refusals: list[str] = []

    # STEP 1: the repair's edits, each applied to the line it names by id, nothing worked out from its wording: a repair
    # that could only add left the program to guess which held line each new one replaced, about 25 times patched
    # (DEFECTS 357). A remark on the notes or a page number added as a fact is no knowledge, and a fact only reporting
    # the speaker saying its clause is the clause (DEFECTS 330, 346, 267)
    # A line written with the id of a held line is that line, replaced
    addition = _convert_held_ids_into_replaces(record, addition)
    # An added line carries no id: a repair shown the ids wrote its new fact as "[F1.5] <fact>" (release test 1 frame 1)
    addition = {**addition, **{field: [_LINE_ID_IN_FRONT.sub("", str(entry)) for entry in addition.get(field) or []
                                      if not frame_markdown_utils.is_remark_about_notes(str(entry))]
                               for field in ("added_facts", "added_take_aways", "added_open_questions")
                               if addition.get(field)}}
    if addition.get("added_facts"):
        addition = {**addition, "added_facts": [frame_markdown_utils.unwrap_speaker_fact(str(entry))
                                                for entry in addition["added_facts"]
                                                if not frame_markdown_utils.is_page_number_fact(str(entry))]}
        # A fact the repair adds about the person talking ("The speaker usually cannot attend conferences"): its
        # instruction says the subject is never the speaker, phase 4 refuses such a sentence, and three were added to
        # one batch no critique rule asked for (38c3-fault-injection, DEFECTS 644)
        addition = {**addition, "added_facts": [entry for entry in addition["added_facts"]
                                                if not frame_markdown_utils.is_speaker_subject_fact(str(entry))]}
    replaced_texts, refused = _apply_edits(record, int(addition.get("frame") or 0), list(addition.get("edits") or []),
                                           changes)
    refusals += refused
    screen = str(record.get("screen") or "")
    shown_parts = _list_named_parts(screen, list(addition.get("wrong_parts") or []))

    # STEP 2: every list grows, never shrinks, and never gains the same entry twice, nor a fact named wrong
    for field in APPENDED_FIELDS:
        added = [entry for entry in (addition.get(f"added_{field}") or []) if entry]
        held = list(record.get(field) or [])
        if field == "questions_asked":
            corrected, added = _replace_answered(held, added)
            replaced_count = sum(old != new for old, new in zip(held, corrected))
            if replaced_count:
                record[field] = held = corrected
                changes.append(f"{field} ~{replaced_count}")
        if not added:
            continue
        grown = _append(held, added)
        if len(grown) > len(held):
            record[field] = grown
            changes.append(f"{field} +{len(grown) - len(held)}")

    # STEP 2b: the words a replace corrected, in a fact or an outline line, are corrected in every other field of the
    # frame's notes. The repair names only lines with ids, and the markdown block and the narration have none: a
    # third fact kept "VTS2" for the slide's "VT52" (DEFECTS 314), and a run-together misread fixed in the outline
    # stayed in the markdown block and the narration (DEFECTS 483)
    corrections = _list_corrections(replaced_texts, str(record.get("screen") or ""))
    for field in _CORRECTED_FIELDS:
        value = record.get(field)
        lines = value if isinstance(value, list) else [value]
        corrected = [_write_corrections(line, corrections, is_speech_kept=field in _SPEECH_QUOTING_FIELDS,
                                        is_opening_quote_only=field in _SPEECH_OPENING_FIELDS)
                     if isinstance(line, str) else line for line in lines]
        if corrected != lines:
            record[field] = corrected if isinstance(value, list) else corrected[0]
            changes.append(f"{field} corrected")

    # STEP 3: the two texts that may simply be better
    for field in REPLACED_FIELDS:
        proposed = str(addition.get(field) or "").strip()
        if proposed and proposed != str(record.get(field) or "").strip():
            record[field] = proposed
            changes.append(field)

    # STEP 4: the three texts a guard decides, so a second look cannot thin what the first one got right. A camera
    # part the outline gains is dropped first, so an addition of nothing else changes nothing
    wrong_parts = _list_named_parts(str(record.get("screen") or ""), list(addition.get("wrong_parts") or []))
    # A named part with no text is named by its kind, when one bare line alone has it (an arrow drawn that is not there)
    wrong_kinds = [part for part in (addition.get("wrong_parts") or [])
                   if _find_kind_line(str(record.get("screen") or ""), str(part)) is not None]
    for field in _GUARDED_FIELDS:
        proposed = str(addition.get(field) or "").strip()
        accepted_text = str(record.get(field) or "").strip()
        if field == "screen":
            # Folded to two spaces a level as a first answer's outline is: a fix indented four spaces under its first
            # part, the record check refused it, and the whole addition with its facts was dropped (lecture-1 frames
            # 35 and 36, 2026-10-13)
            proposed = frame_markdown_utils.normalise_screen_indentation(
                _SCREEN_ID_AFTER_DASH.sub(r"\g<head>", proposed)) if proposed else proposed
            # Both outlines without the camera parts: one that only loses its camera part is no change
            proposed, accepted_text = _drop_meeting_tool_parts(proposed), _drop_meeting_tool_parts(accepted_text)
        if not proposed or proposed == accepted_text:
            continue
        reason = "" if field in critiqued_fields else _judge_guarded(
            field, str(record.get(field) or ""), proposed, wrong_parts, list(addition.get("wrong_diagram_lines") or []),
            wrong_kinds, list(addition.get("wrong_narration") or []))
        if reason:
            refusals.append(f"{field}: {reason}")
            continue
        record[field] = proposed
        changes.append(field)
    # A line the new outline corrects is corrected in the markdown copy too, when the addition wrote no markdown: a
    # fix added a slide's "[1|2]" to its outline line and the markdown kept the old line (froscon 3211 frame 43,
    # DEFECTS 287), though second_pass.md asks for both
    if "screen" in changes and "as_markdown" not in changes and record.get("as_markdown"):
        markdown = _apply_outline_corrections(str(accepted.get("screen") or ""), str(record["screen"]),
                                              str(record["as_markdown"]))
        if markdown != record["as_markdown"]:
            record["as_markdown"] = markdown
            changes.append("as_markdown")
    # ... and a part named not on this frame leaves the markdown copy as it leaves the outline: frame 36's markdown kept
    # seven lines of a later frame's output the fix named, and the document cited frame 36 for them (froscon 3211,
    # DEFECTS 294)
    if shown_parts and "as_markdown" not in changes and record.get("as_markdown"):
        named = {part.casefold() for part in shown_parts}
        lines = str(record["as_markdown"]).split("\n")
        kept_lines = [line for line in lines if line.strip().casefold() not in named]
        if len(kept_lines) < len(lines):
            record["as_markdown"] = "\n".join(kept_lines)
            changes.append("as_markdown")

    return record, changes, refusals


# How alike an old and a new outline quote must be to be one line corrected
_CORRECTED_QUOTE_LIKENESS = 0.75


def _list_outline_quotes(screen: str) -> list[str]:
    return [match.group(1).strip() for line in screen.splitlines() for match in [_OUTLINE_QUOTE.search(line)] if match]


def _apply_outline_corrections(accepted_screen: str,
                               proposed_screen: str,
                               markdown: str) -> str:
    """The markdown with each line that is exactly an old outline quote the new outline corrected (the one new quote
    most alike to it, at least _CORRECTED_QUOTE_LIKENESS, standing nowhere in the old outline) written as corrected."""

    old, new = _list_outline_quotes(accepted_screen), _list_outline_quotes(proposed_screen)
    gone = [quote for quote in old if quote not in new]
    added = [quote for quote in new if quote not in old]
    lines = markdown.split("\n")
    for quote in gone:
        scored = sorted(((difflib.SequenceMatcher(None, quote, other).ratio(), other) for other in added), reverse=True)
        if not scored or scored[0][0] < _CORRECTED_QUOTE_LIKENESS:
            continue
        lines = [scored[0][1] if line.strip() == quote else line for line in lines]

    return "\n".join(lines)


# An outline line's quoted text, from its first quote to its last
_OUTLINE_QUOTE = re.compile(r'["`\u201c\u201e](.*)["`\u201d\u201c]')


def describe_refusals(frame: int,
                      refusals: list[str]) -> str:
    """One console line for what an addition wanted and did not get, or an empty string when it got everything."""

    return f"frame {frame}: addition refused, {'; '.join(refusals)}" if refusals else ""
