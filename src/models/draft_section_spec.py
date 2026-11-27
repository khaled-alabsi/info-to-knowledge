"""One section of a direct-draft run's draft: a heading and the lines under it, up to the next heading. Phase 3's batches
write the draft, phase 4 reads it as these."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["DraftSectionSpec", "LEAD_ID"]

# The id of the text before the draft's first heading, which has no heading of its own
LEAD_ID = "H00"


@dataclass
class DraftSectionSpec:
    id: str                                  # H01, H02 ... in the draft's order; `LEAD_ID` for the lead text
    level: int                               # the heading's number of `#`; 0 for the lead text
    title: str
    lines: list[str] = field(default_factory=list)
