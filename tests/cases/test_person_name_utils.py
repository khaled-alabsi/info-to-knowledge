"""People named "<Last>, <First>" on a screen, as the text reader finds them, for phase 2's call-window test and phase
4's personal-data check on cuts. Words are laid out on one line unless placed otherwise."""

from support import harness

from utils import person_name_utils


def _line(texts: list[str],
          top: int = 0) -> list[dict]:
    return [{"text": text, "box": [at * 60, top, at * 60 + 50, top + 20]} for at, text in enumerate(texts)]


def _names(words: list[dict]) -> tuple[list[str], list[str]]:
    marked, unmarked = person_name_utils.list_listed_names(words)

    return [name["text"] for name in marked], [name["text"] for name in unmarked]


def test_a_person_is_marked_by_a_guest_mark_or_an_honorific_and_a_bare_pair_is_kept_apart():
    harness.check("a guest mark marks the pair", _names(_line(["Berg,", "Jonas", "(extern)"]))
                  == (["Berg, Jonas (extern)"], []), str(_names(_line(["Berg,", "Jonas", "(extern)"]))))
    harness.check("an honorific marks the pair", _names(_line(["Berg,", "Dr.", "Jonas"])) == (["Berg, Dr. Jonas"], []),
                  str(_names(_line(["Berg,", "Dr.", "Jonas"]))))
    # A call tile wrote a person with two first names before the guest mark (2026-10-10)
    harness.check("a guest mark after two first names marks the whole name",
                  _names(_line(["Lind,", "Mara", "Sofie", "(extern)"])) == (["Lind, Mara Sofie (extern)"], []),
                  str(_names(_line(["Lind,", "Mara", "Sofie", "(extern)"]))))
    harness.check("a bare pair is unmarked", _names(_line(["Sund,", "Ella"])) == ([], ["Sund, Ella"]),
                  str(_names(_line(["Sund,", "Ella"]))))


def test_a_list_of_nouns_with_commas_names_nobody_and_a_pair_split_over_two_lines_is_no_name():
    # A slide of "Kauf, Tausch, Sparplan" beside a photo was taken for a call tile (2026-10-10)
    harness.check("no person in a comma list", _names(_line(["Kauf,", "Tausch,", "Sparplan"])) == ([], []),
                  str(_names(_line(["Kauf,", "Tausch,", "Sparplan"]))))
    split = _line(["Berg,"]) + [{"text": "Jonas", "box": [0, 200, 50, 220]}]
    harness.check("words on two lines are no pair", _names(split) == ([], []), str(_names(split)))
