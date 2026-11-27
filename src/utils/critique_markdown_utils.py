"""A batch's critique as markdown: the model's answer read into rules, and rules written back as the same document.
Pure functions.

An answer is one document, a `## ` section per rule, each section three labelled lines:

    # Critique
    ## An arrow is copied from the frame before without checking its head
    What it is: the diagram of the frame before is handed over in full, and a frame that only adds a box keeps an
    arrow whose direction this frame contradicts.
    How to avoid it: read every arrow's head on the frame itself before you keep it.
    Seen on: frames 9, 11

    # Nothing to criticise           the batch's notes hold no anti-pattern this call can name

The rule's own two lines are written for ANY meeting; `Seen on` is the one place a frame number belongs. A line the
document lacks is refused by name rather than guessed at, as everywhere in this folder: a rule missing how to avoid
it teaches nobody anything, and a silent default would hide that.
"""

from __future__ import annotations

import re
from typing import Any

from models.critique_parse_result import CritiqueParseResult
from schemas import critique_record

from utils import frame_markdown_utils

__all__ = ["parse_critique", "render_critique_document", "list_rule_frames", "find_screen_quotes", "is_form_rule",
           "hide_screen_quotes", "hide_screen_keys", "SCREEN_TEXT_MASK", "find_frame_in_rule", "hide_frame_numbers"]

_DOCUMENT = re.compile(r"^# (?P<title>.+?)\s*$")
_RULE = re.compile(r"^#{2,3} (?P<name>.+?)\s*$")
_LABELLED = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?(?P<label>[A-Za-z][A-Za-z' ]*?)(?:\*\*)?\s*:\s*(?P<value>.*)$")
# "frames 9, 11", "frame 9", "9 and 11", "9, 11": every number of the line, whatever words sit between them.
_NUMBER = re.compile(r"\d+")
# A rule about form, never knowledge (critique.md: "a colour, a font, a position ... is form: never a rule"): models
# wrote such rules all the same, and the repair then spent a call on them. Named by its rule's name; "position" and
# "layout" stay out, since an arrow drawn by the boxes' positions is a real mistake.
_FORM_RULE = re.compile(r"\b(?:colou?rs?|fonts?|bold|italics?|styling|spacing|margins?|borders?|copyright|footers?|"
                        r"character counts?|smart|curly|quotation marks|punctuation|cursors?|"
                        r"cent(?:red|ered)|(?:left|right)-aligned|aligned|alignment|line numbers?)\b", re.IGNORECASE)
_LABELS = {label.casefold(): field for field, label in critique_record.RULE_LINES.items()}
# A quoted span, straight or curly, and its text
_QUOTED_SPAN = re.compile(r'"([^"]+)"|\u201c([^\u201d]+)\u201d')
# What a quoted span must be longer than to count as the screen's own words: "ok" or "id" appears on every screen and
# in every sentence about one, while a span of this many characters is text copied off the frame.
_QUOTED_SCREEN_CHARS = 12
# A rule's own two lines hold for any meeting, so a frame number in them is the mistake: the rule was written about
# this batch instead of about the anti-pattern. `Seen on` is where a frame number belongs. A list of numbers after one
# "frames" is one match, so it is hidden whole
_FRAME_IN_RULE = re.compile(r"\b(frames?)\s+\d+(?:\s*(?:,|and|to|-|\u2013|\u2192)\s*\d+)*", re.IGNORECASE)
# What a rule's line says in place of this batch's frame numbers
FRAME_NUMBER_MASK = "<n>"
# What a rule says in place of the screen's own words: the kind of text, never this meeting's text
SCREEN_TEXT_MASK = '"<text on the screen>"'


def _split_checked_section(lines: list[str]) -> tuple[list[str], list[str]]:
    """The answer's lines without its `## Checked` section, and that section's lines: one answer per listed mistake,
    never a rule."""

    kept: list[str] = []
    checked: list[str] = []
    inside = False
    for line in lines:
        rule = _RULE.match(line)
        if rule or _DOCUMENT.match(line):
            inside = bool(rule) and " ".join(rule.group("name").split()).casefold() \
                == critique_record.CHECKED_TITLE.casefold()
            if inside:
                continue
        if inside:
            if line.strip():
                checked.append(re.sub(r"^\s*[-*+]\s+", "", line).strip())
            continue
        kept.append(line)

    return kept, checked


def is_nothing_checked(checked: list[str]) -> bool:
    """Whether every answer of a `## Checked` section is "none": the critique found no mistake."""

    return bool(checked) and all(line.rsplit(":", 1)[-1].strip().strip(".").casefold() == critique_record.NONE_FOUND
                                 for line in checked)


def _split_rule_sections(lines: list[str]) -> tuple[bool, list[tuple[str, list[str]]]]:
    """Whether the answer says there is nothing to criticise, and one (name, body) pair per rule section."""

    is_nothing = False
    rules: list[tuple[str, list[str]]] = []
    for line in lines:
        document = _DOCUMENT.match(line)
        if document:
            title = " ".join(document.group("title").split()).casefold()
            is_nothing = is_nothing or title.startswith(critique_record.NOTHING_TO_CRITICISE_TITLE.casefold())
            continue
        rule = _RULE.match(line)
        # "## Nothing to criticise" after a critique's rules is that heading at the wrong level, with empty lines under
        # it, never a rule: it was stored as one, and the gate asked for it to be judged (froscon talk 3147, DEFECTS 315)
        if rule and " ".join(rule.group("name").split()).casefold().startswith(
                critique_record.NOTHING_TO_CRITICISE_TITLE.casefold()):
            rules.append(("", []))
            continue
        if rule:
            rules.append((" ".join(rule.group("name").split()), []))
        elif rules:
            rules[-1][1].append(line)

    return is_nothing, [(name, body) for name, body in rules if name]


# A text a Where line quotes, in backticks, double quotes or brackets with quotes: long enough to find one line by
_WHERE_QUOTE = re.compile(r'`([^`]{12,})`|"([^"]{12,})"|\(\s*"?([^()"]{12,})"?\s*\)')
_WHERE_FRAME = re.compile(r"\s*frame (\d+)\b", re.IGNORECASE)
# The lists of a frame's notes the fix may edit a line of under "Edits"
_NAMED_LINE_FIELDS = ("facts", "take_aways")


def _strip_marks(text: str) -> str:
    return " ".join(re.sub(r"[`\"“”‘’'…]|\.\.\.", " ", str(text)).split()).casefold()


_ELLIPSIS = re.compile(r"\.{3}|…")


def list_named_lines(where: str,
                     records: list[dict[str, Any]]) -> list[str]:
    """The whole lines of the frame's facts and take-aways a Where line quotes part of: the fix agreed a line was wrong,
    wrote its correction and named nothing, so the wrong line stayed (lecture-1 frames 81, 111, 137, 140, 2026-10-14).
    Handed whole, with the id it is edited by."""

    frame = _WHERE_FRAME.match(str(where))
    record = next((entry for entry in records if frame and str(entry.get("frame")) == frame.group(1)), None)
    if record is None:
        return []
    quotes = [next(part for part in match.groups() if part) for match in _WHERE_QUOTE.finditer(str(where))]
    lines = [str(line) for field in _NAMED_LINE_FIELDS for line in record.get(field) or []]

    return list(dict.fromkeys(line for line in lines for quote in quotes if quote and _holds_quote(_strip_marks(line), quote)))


def _holds_quote(line: str,
                 quote: str) -> bool:
    """Whether `line` holds `quote`, a quote shortened with "..." or "…" holding each piece in order: a Where line quoted
    "The vendor team... found some issues by design", no line held it whole, and the wrong line stayed beside its
    correction (38c3-fault-injection frame 4, 2026-10-15)."""

    pieces = [_strip_marks(piece) for piece in _ELLIPSIS.split(quote) if _strip_marks(piece)]
    position = 0
    for piece in pieces:
        position = line.find(piece, position)
        if position < 0:
            return False
        position += len(piece)

    return bool(pieces)


def list_seen_frames(rule_line: str) -> list[int]:
    """The frames a `Seen on` line names: the numbers after "frame" or "frames" when it names any, every number when it
    is a bare list ("9, 11"). A worker that said where on the frame it saw the mistake ("frame 35 (bullet 3)") had its
    bullet numbers read as frames, and the critique was refused as naming frames outside its batch (2026-10-12)."""

    named = [int(number) for match in _FRAME_IN_RULE.finditer(rule_line) for number in _NUMBER.findall(match.group(0))]
    return named or [int(number) for number in _NUMBER.findall(rule_line)]


def _read_rule(name: str,
               body: list[str]) -> dict[str, Any] | None:
    """One rule section: its labelled lines by their field, and the frames of `Seen on`. None when it is all blank."""

    rule: dict[str, Any] = {"name": name, **{field: "" for field in critique_record.RULE_LINES}, "frames": [],
                            "where": []}
    current = ""
    for line in body:
        labelled = _LABELLED.match(line)
        label = " ".join(labelled.group("label").split()).casefold() if labelled else ""
        if labelled and label == critique_record.SEEN_ON_LABEL.casefold():
            rule["frames"] = list_seen_frames(labelled.group("value"))
            current = ""
        elif labelled and label == critique_record.WHERE_LABEL.casefold():
            rule["where"].append(labelled.group("value").strip())
            current = "where"
        elif current == "where" and line.strip():
            rule["where"][-1] = f"{rule['where'][-1]} {line.strip()}"
        elif labelled and label in _LABELS:
            current = _LABELS[label]
            rule[current] = labelled.group("value").strip()
        elif current and line.strip():
            # A line wrapped by the model belongs to the line above it, which is how every long rule arrives.
            rule[current] = f"{rule[current]} {line.strip()}".strip()
    if not name.strip() and not any(str(rule[field]).strip() for field in critique_record.RULE_LINES):
        return None
    for field in critique_record.RULE_LINES:
        rule[field] = drop_reasoning_remarks(str(rule[field]))
    rule["where"] = [drop_reasoning_remarks(line) for line in rule["where"]]

    return rule


# The words of a writer reasoning to itself, which no rule for any meeting holds: "Wait", "let's check", "I will"
_REASONING_WORD = re.compile(r"\b(?:wait|hmm|let'?s (?:check|look|see|re-?check)|let me)\b|\bI(?: will|'ll| think)\b",
                             re.IGNORECASE)


def drop_reasoning_remarks(text: str) -> str:
    """`text` without each bracketed remark holding the writer's own reasoning, "(note: ... Wait, let's check ... I
    will restrict the rule to frame 106.)", a bracket left open taken to the line's end. A critique wrote its doubts
    about one frame into a rule's Where line, and the rule went to phase 6 with them (Lecture 7, DEFECTS 570); the
    critique changes no frame, so its words are put right where written, never refused."""

    kept, start = [], 0
    while (opening := text.find("(", start)) != -1:
        depth, end = 0, len(text)
        for index in range(opening, len(text)):
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            if depth == 0:
                end = index + 1
                break
        if _REASONING_WORD.search(text[opening:end]):
            kept.append(text[start:opening].rstrip())
        else:
            kept.append(text[start:end])
        start = end
    kept.append(text[start:])

    return " ".join("".join(kept).split())


def parse_critique(text: str) -> CritiqueParseResult:
    """The answer read into `{"is_nothing_to_criticise": bool, "rules": [...], "checked": [...]}`, or None and why it
    could not be read; every rule carries `name`, each line of `RULE_LINES` and `frames`, and `checked` holds the
    answers of the `## Checked` section, one per listed mistake."""

    # A line the rule does not hold is left EMPTY here and named by the check, so the operator sees which rule was
    # short rather than a line the program invented for it.

    body = frame_markdown_utils.THINKING_BLOCK.sub("", text or "")
    lines, checked = _split_checked_section(body.replace("\r\n", "\n").split("\n"))
    is_nothing, rule_sections = _split_rule_sections(lines)
    read = [rule for rule in (_read_rule(name, block) for name, block in rule_sections) if rule]
    rules = [rule for rule in read if not is_form_rule(rule)]
    # A critique whose every rule was about form criticised nothing a reader needs, and one whose every listed
    # mistake was checked and found on no frame criticised nothing either
    is_nothing = is_nothing or bool(read and not rules) or (not read and is_nothing_checked(checked))
    if not rules and not is_nothing:
        return CritiqueParseResult(critique=None, why=(
            "no rule and no \"# " + critique_record.NOTHING_TO_CRITICISE_TITLE + "\": a critique is one `## <name>` "
            "section per anti-pattern, or that one heading"))

    critique = {"is_nothing_to_criticise": bool(is_nothing and not rules), "rules": rules}

    return CritiqueParseResult(critique={**critique, "checked": checked} if checked else critique, why="")


def is_form_rule(rule: dict[str, Any]) -> bool:
    """Whether a rule's name is about form (a colour, a font, spacing, a margin, a footer) rather than knowledge."""

    return bool(_FORM_RULE.search(str(rule.get("name") or "")))


def list_rule_frames(rule: dict[str, Any]) -> list[int]:
    """The frame numbers one rule names, in the order it names them, with none repeated."""

    seen: list[int] = []
    for number in rule.get("frames") or []:
        if int(number) not in seen:
            seen.append(int(number))

    return seen


def render_critique_document(answer: dict[str, Any]) -> str:
    """The critique as it was answered: `# Nothing to criticise`, or one section per rule under `# Critique`."""

    rules = answer.get("rules") or []
    if not rules:
        return f"# {critique_record.NOTHING_TO_CRITICISE_TITLE}\n"
    blocks = [f"# {critique_record.CRITIQUE_TITLE}\n"]
    for rule in rules:
        lines = [f"## {str(rule.get('name') or '').strip()}"]
        lines += [f"{label}: {str(rule.get(field) or '').strip()}"
                  for field, label in critique_record.RULE_LINES.items()]
        frames = list_rule_frames(rule)
        listed = ", ".join(str(number) for number in frames)
        seen_on = f"frame{'s' if len(frames) != 1 else ''} {listed}" if frames else ""
        lines.append(f"{critique_record.SEEN_ON_LABEL}: {seen_on}")
        lines += [f"{critique_record.WHERE_LABEL}: {where}" for where in rule.get("where") or []]
        blocks.append("\n".join(lines) + "\n")

    return "\n".join(blocks)


def _read_screen_quotes(screen_texts: list[str]) -> str:
    """Only what an outline quotes is the screen's text; its own words around the quotes ("the grid scrolls right")
    describe the screen, and a rule may name a kind of description that way."""

    return " | ".join(match.group(1) or match.group(2) for text in screen_texts
                      for match in _QUOTED_SPAN.finditer(text)).casefold()


def find_screen_quotes(text: str,
                       screen_texts: list[str]) -> list[str]:
    """The spans `text` quotes that are the screen's own words, as the outlines in `screen_texts` quote them."""

    screen = _read_screen_quotes(screen_texts)
    spans = [match.group(1) or match.group(2) for match in _QUOTED_SPAN.finditer(text)]

    return [span for span in spans if len(span.strip()) >= _QUOTED_SCREEN_CHARS and span.strip().casefold() in screen]


def _hide_quotes(text: str,
                 screen: str) -> str:
    """Write each quote of `text` that is the screen's own words as `SCREEN_TEXT_MASK`, the rest as it was."""

    pieces, end = [], 0
    for match in _QUOTED_SPAN.finditer(text):
        span = (match.group(1) or match.group(2)).strip()
        is_screen = len(span) >= _QUOTED_SCREEN_CHARS and span.casefold() in screen
        pieces += [text[end:match.start()], SCREEN_TEXT_MASK if is_screen else match.group(0)]
        end = match.end()

    return "".join(pieces) + text[end:]


def hide_screen_quotes(rule: dict[str, Any],
                       screen_texts: list[str]) -> dict[str, Any]:
    """Return `rule` with each quote of the screen's own words in its name and its lines written `SCREEN_TEXT_MASK`:
    the rule file is carried into the worker's instructions, where one meeting's words do not belong, and the critique
    changes no frame, so its words are put right here rather than refused."""

    screen = _read_screen_quotes(screen_texts)
    if not screen:
        return rule

    return {**rule, **{field: _hide_quotes(str(rule.get(field) or ""), screen)
                       for field in ("name", *critique_record.RULE_LINES)}}


# A name of the screen's own, unquoted: letters and digits in one word of four characters or more (a ticket's key,
# a build's tag), never a plain number
_SCREEN_KEY = re.compile(r"\b(?=[\w-]*[A-Za-z])(?=[\w-]*\d)[A-Za-z0-9][\w-]{3,}\b")


def _hide_keys(text: str,
               keys: set[str]) -> str:
    return _SCREEN_KEY.sub(lambda match: SCREEN_TEXT_MASK if match.group(0) in keys else match.group(0), text)


def hide_screen_keys(rule: dict[str, Any],
                     screen_texts: list[str]) -> dict[str, Any]:
    """Return `rule` with each word of letters and digits its name and lines share with the screens written
    `SCREEN_TEXT_MASK`: a rule gave two of a board's ticket keys, unquoted, as its example, and the quote mask let them
    through into the file carried into the worker's instructions (board recording, 2026-10-22)."""

    screen = " ".join(screen_texts)
    keys = {match.group(0) for match in _SCREEN_KEY.finditer(screen)}
    if not keys:
        return rule

    return {**rule, **{field: _hide_keys(str(rule.get(field) or ""), keys) for field in ("name", *critique_record.RULE_LINES)}}


def find_frame_in_rule(text: str) -> str:
    """The first frame number `text` names, as written ("frame 12", "frames 3, 4"), or "" when it names none."""

    match = _FRAME_IN_RULE.search(text)

    return match.group(0) if match else ""


def hide_frame_numbers(rule: dict[str, Any]) -> dict[str, Any]:
    """Return `rule` with each frame number in its name and its lines written `frame <n>`: its frames belong on its
    `Seen on` line, which keeps them, and the critique changes no frame, so the line is put right here rather than
    refused."""

    return {**rule, **{field: _FRAME_IN_RULE.sub(rf"\1 {FRAME_NUMBER_MASK}", str(rule.get(field) or ""))
                       for field in ("name", *critique_record.RULE_LINES)}}
