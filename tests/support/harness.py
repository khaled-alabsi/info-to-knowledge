# <!-- sync: seed/support.harness v1 -->
"""One assertion function, deliberately not unittest.

A validator suite is most useful when it tells you all six codes that broke, not the first one. So
checks collect rather than abort, and a crashing test is recorded as a failing test.
"""

from __future__ import annotations

PASSED: list[tuple[str, str]] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, is_ok: bool, detail: str = "") -> bool:
    (PASSED if is_ok else FAILED).append((name, detail))

    return bool(is_ok)


def codes(report) -> set[str]:
    """The set of finding codes in a ValidationReport."""

    return {finding.code for finding in report.findings}


def reset() -> None:
    PASSED.clear()
    FAILED.clear()
