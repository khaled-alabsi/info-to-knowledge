"""Phase 4's one call of a takeaway run, judged from `04_writing/takeaway.md` on disk.

The takeaway has both sections, at least one line in them, and every line ends with the transcript lines it rests on,
each a line of this run's transcript. Whether a line under "Not settled" says what is not known is the instruction's to
teach: no program reads that from the words.
"""

from __future__ import annotations

from models.run_paths import RunPaths
from models.validation_report import ValidationReport
from services import call_log_service
from services import takeaway_document_service
from utils import takeaway_markdown_utils
from validators import chain_response_validator

__all__ = ["validate_takeaway", "validate_takeaway_record"]


def validate_takeaway_record(paths: RunPaths,
                             known_ids: list[str]) -> ValidationReport:
    """The takeaway file the call left: readable, not empty, every line cited with lines of the transcript."""

    path = takeaway_document_service.build_takeaway_path(paths)
    report = ValidationReport(subject=path.name)
    report.mark_ran("TAKEAWAY_UNREADABLE")
    parsed, why = (takeaway_markdown_utils.parse_takeaway(path.read_text(encoding="utf-8")) if path.is_file()
                   else (None, f"{path.name} was not written"))
    if parsed is None:
        report.add("TAKEAWAY_UNREADABLE", why, fix='write "# <title>", then "## Agreed" and "## Not settled", each '
                                                   'holding only "- <fact> [S0001, S0002]" lines')

        return report
    items = [(heading, item) for heading, key in takeaway_markdown_utils.SECTION_KEYS.items() for item in parsed[key]]
    report.mark_ran("TAKEAWAY_EMPTY")
    if not items:
        report.add("TAKEAWAY_EMPTY", "neither section holds a line",
                   fix="write each fact the recording settled under Agreed, and each point it left open under Not "
                       "settled")
    report.mark_ran("TAKEAWAY_LINE_UNCITED")
    report.mark_ran("TAKEAWAY_LINE_UNKNOWN")
    known = set(known_ids)
    first, last = (known_ids[0], known_ids[-1]) if known_ids else ("S0001", "S0001")
    for heading, item in items:
        if not item["ids"]:
            report.add("TAKEAWAY_LINE_UNCITED", f'"{item["text"]}" under {heading} names no transcript line',
                       fix="end the line with the lines it rests on in brackets, such as [S0012, S0031]")
        unknown = [line_id for line_id in item["ids"] if line_id not in known]
        if unknown:
            report.add("TAKEAWAY_LINE_UNKNOWN", f'"{item["text"]}" names {", ".join(unknown)}, which the transcript '
                       f"does not hold: it runs from {first} to {last}", fix="name only the lines you were given")

    return report


def validate_takeaway(paths: RunPaths,
                      call_slug: str,
                      known_ids: list[str]) -> ValidationReport:
    """Phase 4's call of a takeaway run, then the takeaway it left."""

    report = chain_response_validator.validate_chain_response(call_log_service.build_call_log_path(paths, call_slug),
                                                              call_slug)
    if not report.ok:
        return report

    return validate_takeaway_record(paths, known_ids)
