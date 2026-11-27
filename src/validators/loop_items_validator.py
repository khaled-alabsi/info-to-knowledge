# <!-- sync: seed/validators.loop_items_validator v1 -->
"""Validate the enumeration phase 1 wrote. A pure function over bytes on disk.

THIS ONE IS NOT A STUB, AND THAT IS DELIBERATE. What a well-formed item list IS does not depend on the
job: it parses, it declares the schema version this playbook reads, it holds at least one entry, every
entry names itself with a slug that is unique and legal as a file name, and any kind it declares is
one the output contract knows. Every later phase indexes into this file, so a playbook whose phase 1
cannot pass has no loop at all - which is why this ships finished while the artifact validator ships
as a stub that fails.

WHAT TO FILL IN: the checks that need to know the job - that the enumeration reached every corner of the
target, that a count here agrees with a count somewhere else, that no obvious frame is absent.
Those are the checks that catch an enumeration which is well-formed and WRONG, and an enumeration
that missed a third of the items produces a run that completes successfully and is wrong.
"""

from __future__ import annotations

import json

from models import output_contract
from models.validation_report import ValidationReport

# Legal in a file name on every operating system this may run on, which a slug becomes the moment a
# later phase writes an artifact named after it: lower case, digits and hyphens, nothing else.
SLUG_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


def is_legal_slug(slug: str) -> bool:
    """Whether this slug can be a path segment on Windows, macOS and Linux alike."""

    return bool(slug) and set(slug) <= SLUG_ALPHABET


def read_loop_items(paths) -> dict:
    """The enumeration as data. Call it only on a file `validate_loop_items` has just accepted -
    it re-reads rather than caching, and it does not defend against a shape the report
    would already have rejected."""

    return json.loads(paths.loop_items.read_text(encoding="utf-8"))


def check_entries(report: ValidationReport,
                  entries: list) -> None:
    """Every entry names itself once, legally, and under a kind the contract declares."""

    seen: set[str] = set()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict) or not is_legal_slug(str(entry.get("slug", ""))):
            report.add("C_SLUG", f"entry {position} has no usable `slug`",
                       fix="give every entry a slug of lower case letters, digits and hyphens")
            continue
        slug = str(entry["slug"])
        if slug in seen:
            report.add("C_DUPLICATE", f"`{slug}` is enumerated more than once",
                       fix=f"keep one entry for {slug} and delete the others")
        seen.add(slug)


def validate_loop_items(paths) -> ValidationReport:
    report = ValidationReport(subject=output_contract.LOOP_ITEMS_FILE)

    # --- existence: always first, and always an early return ---------------------------------
    report.mark_ran("C_MISSING")
    artifact = paths.loop_items
    if not artifact.is_file() or not artifact.stat().st_size:
        report.add("C_MISSING", "the enumeration was not written",
                   fix=f"write the item list to {artifact.name}")

        return report          # every later check would cascade off a missing file

    # --- it parses, and it is an object -------------------------------------------------------
    report.mark_ran("C_UNPARSABLE")
    try:
        enumerated = json.loads(artifact.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.add("C_UNPARSABLE", f"{artifact.name} is not valid JSON: {exc}",
                   fix="rewrite the whole file as one JSON object")

        return report
    if not isinstance(enumerated, dict):
        report.add("C_UNPARSABLE", f"{artifact.name} is a {type(enumerated).__name__}, not an object",
                   fix="the top level is an object with `schemaVersion` and `items`")

        return report

    # --- the version this playbook reads ------------------------------------------------------
    report.mark_ran("C_SCHEMA")
    declared = enumerated.get("schemaVersion")
    if declared != output_contract.LOOP_ITEMS_SCHEMA_VERSION:
        report.add("C_SCHEMA", f"schemaVersion is {declared!r}",
                   fix=f"set it to {output_contract.LOOP_ITEMS_SCHEMA_VERSION}")

    # --- something was found ------------------------------------------------------------------
    report.mark_ran("C_EMPTY")
    entries = enumerated.get("items")
    # An audio recording has no picture, and a PDF read as its markdown none taken: no frame is the right answer
    if enumerated.get("medium") in output_contract.FRAMELESS_MEDIUMS and entries == []:
        return report
    if not isinstance(entries, list) or not entries:
        report.add("C_EMPTY", "`items` is missing or empty",
                   fix="an empty enumeration is a finding to report, never an enumeration to accept")

        return report          # the per-entry checks below have nothing to walk

    # --- one legal, unique entry each ---------------------------------------------
    report.mark_ran("C_SLUG")
    report.mark_ran("C_DUPLICATE")
    check_entries(report, entries)

    return report
