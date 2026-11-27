"""Whether a worker's report names a problem at all, shared by phase 3's reader of a reply's `# Feedback` and the
`report_problem` tool: a batch's reply wrote `# Feedback` with the summary "None", and the run's file kept it as an entry."""

from __future__ import annotations

import re

__all__ = ["is_empty_report"]

# A summary that says there is nothing to report
_NOTHING_TO_REPORT = re.compile(r"^(?:none|n/?a|nothing(?: to report)?|no (?:feedback|problems?|issues?)(?: to report)?)"
                                r"[.!]?$", re.IGNORECASE)


def is_empty_report(summary: str) -> bool:
    """Whether a report's summary is empty or says only that there is nothing to report."""

    line = " ".join((summary or "").split()).strip("`*_ ")

    return not line or bool(_NOTHING_TO_REPORT.match(line))
