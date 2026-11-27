"""One call of phase 3's preparation of an audio recording, judged from its file under `03_prepared/` on disk.

Every item holds the sections its step names; every section names the paragraphs it rests on, only paragraphs the
call was given, unless it says the recording does not say it; every number it states stands in those paragraphs; a
step whose items a later step details places every paragraph in an item; and a call about one item answers one item.
"""

from __future__ import annotations

import re
from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from services import call_log_service
from services import prepared_file_service
from utils import prepared_markdown_utils
from validators import chain_response_validator

__all__ = ["validate_prepared", "validate_prepared_record"]

_NUMBER = re.compile(r"\d(?:[\d,.]*\d)?")
_THOUSANDS_MARK = re.compile(r"[,.](?=\d{3}(?!\d))")       # a mark before exactly three last digits: "1,200", "1.200"
# The number words up to twenty of each language the document is written in (`document_heading_spec`), each at its value
_NUMBER_WORDS = (("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                  "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"),
                 ("null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf",
                  "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn",
                  "zwanzig"))
_WORD = re.compile(r"[^\W\d_]+")
_SHAPE = '"## <item>", then each "### <section>" with its text and "[S0041, S0052]"'


def _normalise_number(number: str) -> str:
    """A number as the check compares it: its thousands marks dropped ("1,200" and "1.200" are 1200), its decimal mark
    kept as a point ("1,5" is 1.5), so "1.5 days" never sources "15 days"."""

    return _THOUSANDS_MARK.sub("", number).replace(",", ".")


def _list_held_numbers(texts: list[str]) -> set[str]:
    """Every number the texts hold, without thousands marks, a number word up to twenty counted as its digits."""

    held = {_normalise_number(number) for text in texts for number in _NUMBER.findall(text)}
    words = {word.casefold() for text in texts for word in _WORD.findall(text)}

    return held | {str(value) for language in _NUMBER_WORDS for value, word in enumerate(language) if word in words}


def _check_section(report: ValidationReport,
                   where: str,
                   section: dict[str, Any],
                   paragraphs: list[dict[str, Any]]) -> list[str]:
    """One section's ids and numbers; the paragraphs it cites."""

    if prepared_markdown_utils.is_not_said(section["text"]) and not section["cited"]:
        return []
    cited, unknown = prepared_markdown_utils.resolve_cited_paragraphs(section["cited"], paragraphs)
    if not section["cited"]:
        report.add("PREP_IDS_MISSING", f"{where} names no paragraph",
                   fix="end the section with the ids of the paragraphs it rests on, such as [S0041, S0052], or write "
                       f"only {output_contract.PREPARED_NOT_SAID!r} when the recording does not say it")
    if unknown:
        report.add("PREP_ID_UNKNOWN", f"{where} names {', '.join(unknown)}, no paragraph you were given",
                   fix="name only the paragraphs of this request, each by its first line's id")
    texts = [paragraph["text"] for paragraph in paragraphs if paragraph["first"] in cited]
    held = _list_held_numbers(texts)
    unsourced = [number for number in _NUMBER.findall(section["text"])
                 if _normalise_number(number) not in held]
    if cited and unsourced:
        report.add("PREP_NUMBER_UNSOURCED",
                   f"{where} states {', '.join(dict.fromkeys(unsourced))}, which none of its paragraphs holds",
                   fix="write each number as the paragraph that says it, and cite that paragraph; leave out a number "
                       "no paragraph says")

    return cited


def validate_prepared_record(paths: RunPaths,
                             step: dict[str, Any],
                             item_number: int | None,
                             paragraphs: list[dict[str, Any]],
                             is_every_paragraph_due: bool) -> ValidationReport:
    """The call's file as the program wrote it from the answer, against the paragraphs it was given. `step` is
    `{"id", "title", "input", "sections"}`; `item_number` is the earlier step's item a detail call is about."""

    path = prepared_file_service.build_prepared_path(paths, step["id"], item_number)
    report = ValidationReport(subject=path.relative_to(paths.output_root).as_posix())
    report.mark_ran("PREP_MISSING")
    if not path.is_file():
        report.add("PREP_MISSING", f"{path.name} was not written", fix=f"answer with {_SHAPE}")

        return report
    parsed, why = prepared_markdown_utils.parse_prepared(path.read_text(encoding="utf-8"))
    report.mark_ran("PREP_UNREADABLE")
    if parsed is None:
        report.add("PREP_UNREADABLE", f"the answer could not be read: {why}", fix=f"answer with {_SHAPE}")

        return report

    # 1. a call about one item answers that one item
    report.mark_ran("PREP_ITEM_COUNT")
    if item_number is not None and len(parsed["items"]) != 1:
        report.add("PREP_ITEM_COUNT", f"the answer holds {len(parsed['items'])} items",
                   fix='write exactly one "## <item>" heading: the item this request is about')

    # 2. every item holds every section of its step, and each section its paragraphs and their numbers
    for code in ("PREP_SECTION_MISSING", "PREP_IDS_MISSING", "PREP_ID_UNKNOWN", "PREP_NUMBER_UNSOURCED"):
        report.mark_ran(code)
    covered: set[str] = set()
    for item in parsed["items"]:
        names = {section["name"].strip().casefold() for section in item["sections"]}
        missing = [name for name in step["sections"] if name.casefold() not in names]
        if missing:
            headings = ", ".join(f"### {name}" for name in missing)
            report.add("PREP_SECTION_MISSING", f'"## {item["title"]}" has no {headings}',
                       fix="write every section of the step under each item, in its order: "
                           + ", ".join(f"### {name}" for name in step["sections"])
                           + f"; one the recording says nothing about holds only {output_contract.PREPARED_NOT_SAID!r}")
        for section in item["sections"]:
            covered.update(_check_section(report, f'"## {item["title"]}", "### {section["name"]}"', section, paragraphs))

    # 3. a step whose items a later step details leaves no paragraph out
    report.mark_ran("PREP_PARAGRAPH_UNCOVERED")
    if is_every_paragraph_due:
        left = [paragraph["first"] for paragraph in paragraphs if paragraph["first"] not in covered]
        if left:
            report.add("PREP_PARAGRAPH_UNCOVERED", f"{', '.join(left)} belong{'s' if len(left) == 1 else ''} to no item",
                       fix="cite every paragraph in the item it is about; a paragraph about no item goes into the item "
                           "nearest it in time")

    return report


def validate_prepared(paths: RunPaths,
                      step: dict[str, Any],
                      item_number: int | None,
                      paragraphs: list[dict[str, Any]],
                      is_every_paragraph_due: bool) -> ValidationReport:
    """One preparation call, by its call log under the step's and item's slug, then the file it left."""

    call_slug = prepared_file_service.build_call_slug(step["id"], item_number)
    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, call_slug),
                                                              call_slug)
    if not report.ok:
        return report

    return validate_prepared_record(paths, step, item_number, paragraphs, is_every_paragraph_due)
