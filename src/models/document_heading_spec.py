"""The headings and fixed lines the program writes into a document itself, in each language a document is written in.
A document is in English unless its ingesting profile writes it in the recording's own language (`language-lesson`); a
language with no row here gets the English ones. A language lesson's document keeps its own language, headings
included. `utils/document_heading_utils.py` picks and lists them."""

from __future__ import annotations

from dataclasses import dataclass

from models import output_contract

__all__ = ["BY_LANGUAGE", "DocumentHeadingSpec", "ENGLISH", "GERMAN"]


@dataclass(frozen=True)
class DocumentHeadingSpec:
    """One language's headings and fixed lines; each field is named for the place it is written."""

    executive_summary: str
    agenda: str
    contents: str
    decisions: str
    action_items: str
    open_points: str
    # The closing section's title names the lists it holds; with the open points alone it is `open_points`
    closing_all: str
    closing_decisions: str
    closing_actions: str
    nothing_open: str
    announced_not_covered: str

    def list_closing(self) -> tuple[str, ...]:
        """Every title the closing section can have, in this language."""

        return self.closing_all, self.closing_decisions, self.closing_actions, self.open_points


ENGLISH = DocumentHeadingSpec(
    executive_summary=output_contract.EXECUTIVE_SUMMARY_HEADING, agenda=output_contract.AGENDA_HEADING,
    contents=output_contract.CONTENTS_HEADING, decisions=output_contract.DECISIONS_HEADING,
    action_items=output_contract.ACTION_ITEMS_HEADING, open_points=output_contract.OPEN_POINTS_HEADING,
    closing_all=output_contract.CLOSING_HEADING_ALL, closing_decisions=output_contract.CLOSING_HEADING_DECISIONS,
    closing_actions=output_contract.CLOSING_HEADING_ACTIONS, nothing_open=output_contract.NOTHING_OPEN,
    announced_not_covered=output_contract.ANNOUNCED_NOT_COVERED)

GERMAN = DocumentHeadingSpec(
    executive_summary="Zusammenfassung", agenda="Agenda", contents="Inhalt", decisions="Entscheidungen",
    action_items="Aufgaben", open_points="Offene Punkte", closing_all="Entscheidungen, Aufgaben und offene Punkte",
    closing_decisions="Entscheidungen und offene Punkte", closing_actions="Aufgaben und offene Punkte",
    nothing_open="Nichts blieb offen.", announced_not_covered="Angekündigt, nicht behandelt")

# By the language code phase 1's transcript carries (`de`, `en`, or a regional form such as `de-AT`)
BY_LANGUAGE = {"en": ENGLISH, "de": GERMAN}

