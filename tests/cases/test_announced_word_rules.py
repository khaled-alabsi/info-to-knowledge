"""The words said over a frame that only the next frame's screen shows, found on invented screens: the unit cases of
`announced_words_utils`, which the public copy runs without the private recordings its integration test reads."""

from __future__ import annotations

from support import harness

from utils import announced_words_utils


def test_words_said_before_the_next_screen_shows_them_are_named_and_common_ones_are_not():
    announced = announced_words_utils.list_announced_words(
        "now we open the Gateway settings and set the timeout to 30",
        ["AcmeSync", "Dashboard"], ["Gateway", "settings", "timeout", "30"])
    harness.check("the next screen's new words said over this frame are named",
                  announced == ["30", "gateway", "settings", "timeout"], str(announced))
    harness.check("a word this frame's screen already shows is not named",
                  announced_words_utils.list_announced_words("the Dashboard opens", ["Dashboard"],
                                                             ["Dashboard", "opens"]) == [])
    harness.check("one long word alone, with no value, is not enough",
                  announced_words_utils.list_announced_words("open Gateway", [], ["Gateway"]) == [])
    harness.check("one value alone is enough", announced_words_utils.list_announced_words(
        "set it to 99", [], ["99"]) == ["99"])


def test_a_quote_said_earlier_announces_the_screen_that_first_shows_its_telling_word():
    harness.check("a telling word new on this screen makes the quote this screen's",
                  announced_words_utils.is_announcing("there is a nice tool called Gateway", "Tools: Gateway",
                                                      ["Tools:"]))
    harness.check("a word the earlier screens already showed does not",
                  not announced_words_utils.is_announcing("there is a nice tool called Gateway", "Tools: Gateway",
                                                          ["Tools: Gateway"]))
