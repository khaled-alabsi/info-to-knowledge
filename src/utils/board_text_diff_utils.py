"""What changed in the text of a board between two frames, as plain code over the words the text reader found on each
(`frames.boardHandler: "text-diff-integration"`). Both frames are read only inside the box phase 2
found changed, so the reader sees the same region twice. Words are matched by place and text, a letter misread allowed;
a line holding a word with no match is a line that changed, and a shift most matched words share is a scroll.

Matched line by line, the reader's grouping undid it: it joined two columns into one line on one frame and not on the
other, and misread a letter differently each time, so a board step listed 37 to 185 changes, nearly all noise.

The words come from `text_detection_service.find_words_in_image`: `{"text", "box", "line"}`, `box` in pixels.
"""

from __future__ import annotations

import difflib
from collections import Counter

__all__ = ["list_lines", "compare_board_words", "render_board_text_diff"]

SAME_PLACE_PX = 8                           # a word whose centre moved by at most this many pixels stands in its place
# A word read the same in its column this far above or below stands where it stood: part of a panel moves while the
# rest stands (a drop-down opened in a field pushed the fields under it 12 pixels down, and each read as changed)
LOCAL_SHIFT_PX = 40
SCROLL_SHARE = 0.6                          # a shift this share of the matched words share is a scroll
SAME_WORD_RATIO = 0.75                      # two words this alike are one word read twice (a letter misread)
MIN_WORD_CHARS = 3                          # a changed word shorter than this (a mark, an icon read as a letter) is no change
LINE_MOVED_SHARE = 0.03                     # a line changed only when this share of the pixels under it changed
MAX_CHANGES = 20                            # a step listing more names the count of the rest


def _normalise(text: str) -> str:
    return "".join(character for character in str(text).casefold() if character.isalnum())


def _find_centre(word: dict) -> tuple[float, float]:
    return (word["box"][0] + word["box"][2]) / 2, (word["box"][1] + word["box"][3]) / 2


def list_lines(words: list[dict]) -> list[dict]:
    """The reader's lines, each `{"text", "box", "words"}` with its words in reading order and the box round them,
    top to bottom."""

    grouped: dict[int, list[dict]] = {}
    for word in words:
        grouped.setdefault(word["line"], []).append(word)
    lines = []
    for line_words in grouped.values():
        ordered = sorted(line_words, key=lambda word: word["box"][0])
        lines.append({"text": " ".join(word["text"] for word in ordered), "words": ordered,
                      "box": [min(word["box"][0] for word in ordered), min(word["box"][1] for word in ordered),
                              max(word["box"][2] for word in ordered), max(word["box"][3] for word in ordered)]})

    return sorted(lines, key=lambda line: (line["box"][1], line["box"][0]))


def _is_same_word(first: str,
                  second: str) -> bool:
    first, second = _normalise(first), _normalise(second)

    return first == second or (min(len(first), len(second)) >= MIN_WORD_CHARS
                               and difflib.SequenceMatcher(a=first, b=second).ratio() >= SAME_WORD_RATIO)


def _find_scroll(before: list[dict],
                 after: list[dict]) -> int:
    """The vertical shift most words read the same on both frames share, when it is a move; 0 otherwise. Only a word
    standing once on each frame counts: a word on every row ("Ticket" in a list of tickets) matches its own row's height
    and hides the scroll."""

    counts_before = Counter(_normalise(word["text"]) for word in before)
    counts_after = Counter(_normalise(word["text"]) for word in after)
    places = {_normalise(word["text"]): _find_centre(word)[1] for word in before}
    shifts = [round(_find_centre(word)[1] - places[key]) for word in after
              if len(key := _normalise(word["text"])) >= MIN_WORD_CHARS and counts_before[key] == 1 and counts_after[key] == 1]
    if not shifts:
        return 0
    bucket, count = Counter(shift // SAME_PLACE_PX for shift in shifts).most_common(1)[0]
    shared = [shift for shift in shifts if shift // SAME_PLACE_PX == bucket]
    shift = round(sum(shared) / len(shared))

    return shift if abs(shift) > SAME_PLACE_PX and count >= SCROLL_SHARE * len(shifts) else 0


def _list_unmatched(words: list[dict],
                    others: list[dict],
                    shift: int) -> list[dict]:
    """The words of `words` with no word of `others` in their place (`others` moved by `shift`) reading the same."""

    unmatched = []
    for word in words:
        x, y = _find_centre(word)
        # In its place or moved by the scroll: a part of a panel moves while the rest stands (a drop-down opened in a
        # field pushes the fields under it down)
        if not any(abs(x - _find_centre(other)[0]) <= 4 * SAME_PLACE_PX
                   and min(abs(y - _find_centre(other)[1] - shift), abs(y - _find_centre(other)[1])) <= LOCAL_SHIFT_PX
                   and _is_same_word(word["text"], other["text"]) for other in others):
            unmatched.append(word)

    return unmatched


def _is_real_word(word: dict) -> bool:
    return len(_normalise(word["text"])) >= MIN_WORD_CHARS


def _read_line_there(line: dict,
                     words: list[dict],
                     shift: int) -> str:
    """The words of the other frame on the same row as `line` and across its columns, `shift` taken off: what stood
    there before (or after)."""

    x0, y0, x1, y1 = line["box"]
    there = [word for word in words if y0 - shift - SAME_PLACE_PX <= _find_centre(word)[1] <= y1 - shift + SAME_PLACE_PX
             and x0 - 4 * SAME_PLACE_PX <= _find_centre(word)[0] <= x1 + 4 * SAME_PLACE_PX]

    return " ".join(word["text"] for word in sorted(there, key=lambda word: word["box"][0]))


def _has_moved(line: dict,
               moved) -> bool:
    """Whether the pixels under a line changed: a line whose pixels stand still was read differently on the two
    frames, not changed. True when no pixel map is given."""

    if moved is None:
        return True
    x0, y0, x1, y1 = (max(0, value) for value in line["box"])
    under = moved[y0:y1 + 1, x0:x1 + 1]

    return under.size > 0 and under.mean() >= LINE_MOVED_SHARE


def compare_board_words(before: list[dict],
                     after: list[dict],
                     moved=None) -> dict:
    """What changed from the words of `before` to those of `after`, read in one region of both frames:
    `{"scrolled": <pixels, + down the screen>, "changes": [{"kind", "text", "old"}]}`. `kind` is "added" (a line
    where nothing stood), "changed" (`old` what stood there) or "removed"; a line whose only unmatched words are marks
    shorter than MIN_WORD_CHARS is no change, and so is a line whose pixels did not change (`moved`, a boolean map of the
    pixels that changed, in the words' pixels; None counts every line)."""

    shift = _find_scroll(before, after)
    new = {id(word) for word in _list_unmatched(after, before, shift) if _is_real_word(word)}
    gone = {id(word) for word in _list_unmatched(before, after, -shift) if _is_real_word(word)}
    changes = []
    for line in list_lines(after):
        if any(id(word) in new for word in line["words"]) and _has_moved(line, moved):
            old = _read_line_there(line, before, shift)
            changes.append({"kind": "changed" if old else "added", "text": line["text"], "old": old, "y": line["box"][1]})
    for line in list_lines(before):
        if any(id(word) in gone for word in line["words"]) and not _read_line_there(line, after, -shift) and _has_moved(line, moved):
            changes.append({"kind": "removed", "text": line["text"], "old": "", "y": line["box"][1] + shift})

    return {"scrolled": shift, "changes": [{key: change[key] for key in ("kind", "text", "old")}
                                           for change in sorted(changes, key=lambda change: change["y"])]}


def render_board_text_diff(diff: dict,
                           against_frame: int) -> str:
    """The changes as lines for a frame's message, in the order they stand on the board; empty when there are none."""

    lines = []
    if diff.get("scrolled"):
        lines.append(f'- the view scrolled {"down" if diff["scrolled"] < 0 else "up"} by {abs(diff["scrolled"])} pixels')
    for change in diff.get("changes", [])[:MAX_CHANGES]:
        if change["kind"] == "changed":
            lines.append(f'- changed: "{change["old"]}" → "{change["text"]}"')
        else:
            lines.append(f'- {change["kind"]}: "{change["text"]}"')
    rest = len(diff.get("changes", [])) - MAX_CHANGES
    if rest > 0:
        lines.append(f"- and {rest} more lines changed")
    if not lines:
        return ""

    return (f"The program read the text in the part of this frame that changed since frame {against_frame}, on both "
            "frames, and found these changes (its reader may misread a letter and misses small or highlighted text: "
            "the image is the source):\n" + "\n".join(lines) + "\n")
