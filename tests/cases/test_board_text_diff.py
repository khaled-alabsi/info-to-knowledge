"""The board's text diff (`frames.boardHandler: "text-diff-integration"`): what changed between
the words the text reader finds in the changed part of a board step and of the board frame before it, and how phase 3
is told.

The words are written here as the reader gives them: `{"text", "box", "line"}`. The board is invented.
"""

from __future__ import annotations

from support import harness

from utils import board_text_diff_utils


def _words(rows: list[tuple[str, int, int]]) -> list[dict]:
    """The reader's words for rows of `(text, x, y)`, each word 60 pixels wide and a line of its own per row."""

    return [{"text": word, "box": [x + 70 * place, y, x + 70 * place + 60, y + 16], "line": number}
            for number, (text, x, y) in enumerate(rows) for place, word in enumerate(text.split())]


BOARD = [("To Do", 40, 100), ("Login fails on Safari", 40, 140), ("Export as CSV", 40, 180),
         ("In Progress", 500, 100), ("Retry budget per tenant", 500, 140), ("Status Open", 900, 100)]


def test_a_row_added_where_nothing_stood_is_added():
    diff = board_text_diff_utils.compare_board_words(_words(BOARD), _words(BOARD + [("Gateway timeout on refunds", 40, 220)]))
    harness.check("one added row, no scroll", diff == {"scrolled": 0, "changes": [
        {"kind": "added", "text": "Gateway timeout on refunds", "old": ""}]}, str(diff))


def test_a_value_changed_in_place_is_one_change_with_its_old_text():
    after = [row if row[0] != "Status Open" else ("Status Done", 900, 100) for row in BOARD]
    diff = board_text_diff_utils.compare_board_words(_words(BOARD), _words(after))
    harness.check("one change, old and new", diff["changes"] == [
        {"kind": "changed", "text": "Status Done", "old": "Status Open"}], str(diff))


def test_a_letter_read_differently_on_the_two_frames_is_no_change():
    after = [row if row[0] != "Retry budget per tenant" else ("Retry budget per tenamt", 500, 140) for row in BOARD]
    diff = board_text_diff_utils.compare_board_words(_words(BOARD), _words(after))
    harness.check("a misread letter changes nothing", diff["changes"] == [], str(diff))


def test_a_scrolled_list_is_a_scroll_with_the_rows_that_came_into_view():
    rows = [(f"Ticket number {name}", 40, 100 + 40 * place) for place, name in enumerate(["alpha", "bravo", "charlie", "delta", "echo"])]
    after = [(text, x, y - 80) for text, x, y in rows[2:]] + [("Ticket number foxtrot", 40, 220), ("Ticket number golf", 40, 260)]
    diff = board_text_diff_utils.compare_board_words(_words(rows), _words(after))
    harness.check("the shift is found as a scroll", diff["scrolled"] == -80, str(diff["scrolled"]))
    harness.check("the rows that came into view are listed as added",
                  [change["text"] for change in diff["changes"] if change["kind"] == "added"]
                  == ["Ticket number foxtrot", "Ticket number golf"], str(diff["changes"]))


def test_the_same_board_read_again_has_no_change():
    diff = board_text_diff_utils.compare_board_words(_words(BOARD), _words(BOARD))
    harness.check("nothing changed, nothing listed", diff == {"scrolled": 0, "changes": []}, str(diff))
    harness.check("and the message says nothing", board_text_diff_utils.render_board_text_diff(diff, 44) == "")


def test_the_message_lists_each_change():
    after = [row if row[0] != "Status Open" else ("Status Done", 900, 100) for row in BOARD] + [
        ("Gateway timeout on refunds", 40, 220)]
    text = board_text_diff_utils.render_board_text_diff(board_text_diff_utils.compare_board_words(_words(BOARD), _words(after)), 44)
    harness.check("it names the frame before", "since frame 44" in text, text)
    harness.check("an added row", '- added: "Gateway timeout on refunds"' in text, text)
    harness.check("a change with old and new", '- changed: "Status Open" → "Status Done"' in text, text)
