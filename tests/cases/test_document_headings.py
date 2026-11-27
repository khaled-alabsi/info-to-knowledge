"""The headings the program writes into a document itself, in the document's language (`models/document_heading_spec.py`,
`utils/document_heading_utils.py`): a regional language code takes its language's headings, a language with none of its
own takes the English ones, and the checks that find a heading know every language's form of it."""

from __future__ import annotations

from support import harness

from models import document_heading_spec
from models import output_contract
from utils import document_heading_utils


def test_a_language_takes_its_own_headings_and_any_other_the_english_ones():
    harness.check("a regional German code takes the German headings",
                  document_heading_utils.select_headings("de-AT") is document_heading_spec.GERMAN)
    harness.check("a language with no headings of its own takes the English ones",
                  document_heading_utils.select_headings("fr") is document_heading_spec.ENGLISH
                  and document_heading_utils.select_headings(None) is document_heading_spec.ENGLISH)


def test_a_check_finds_a_heading_in_every_language_english_first():
    every = document_heading_utils.list_every("open_points")
    harness.check("the open points' title in every language, English first",
                  every == (output_contract.OPEN_POINTS_HEADING, "Offene Punkte"), str(every))
    harness.check("every closing title of both languages is known",
                  {"Offene Punkte", output_contract.CLOSING_HEADING_ALL} <= set(document_heading_utils.list_closing_headings()))
    try:
        document_heading_utils.list_every("no_such_heading")
        harness.check("a heading the spec does not have is refused by name", False)
    except ValueError as exc:
        harness.check("a heading the spec does not have is refused by name", "no_such_heading" in str(exc), str(exc))
