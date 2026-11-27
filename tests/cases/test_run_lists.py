"""The person's own lists of the run list: kept in `ui/lists.json`, saved whole, each named, a row in
one list at most."""

import tempfile
from pathlib import Path

from support import harness

from services import run_lists_service


def _with_store():
    previous = run_lists_service.LISTS_PATH
    run_lists_service.set_lists_path(Path(tempfile.mkdtemp()) / "lists.json")

    return previous


def test_lists_are_saved_and_read_back_in_order():
    previous = _with_store()
    try:
        harness.check("none before anything is saved", run_lists_service.read_lists() == [])
        saved = run_lists_service.save_lists([{"id": "a", "name": " Review later ", "members": ["/inbox/x.mp4", "/inbox/x.mp4"]},
                                              {"id": "b", "name": "Clients", "members": ["run:output/acmesync"]}])
        harness.check("names trimmed, a row named twice kept once",
                      saved[0] == {"id": "a", "name": "Review later", "members": ["/inbox/x.mp4"]}, str(saved))
        harness.check("read back as saved, in order", run_lists_service.read_lists() == saved)
    finally:
        run_lists_service.set_lists_path(previous)


def test_a_list_with_no_name_or_a_row_in_two_lists_is_refused():
    previous = _with_store()
    try:
        for lists, word in (([{"id": "a", "name": " ", "members": []}], "name"),
                            ([{"id": "a", "name": "One", "members": ["k"]}, {"id": "b", "name": "Two", "members": ["k"]}],
                             "two lists"),
                            ([{"id": "a", "name": "One"}, {"id": "a", "name": "Two"}], "share the id")):
            try:
                run_lists_service.save_lists(lists)
            except ValueError as error:
                harness.check(f"refused: {word}", word in str(error), str(error))
                continue
            harness.check(f"refused: {word}", False)
        harness.check("nothing was written by a refusal", run_lists_service.read_lists() == [])
    finally:
        run_lists_service.set_lists_path(previous)
