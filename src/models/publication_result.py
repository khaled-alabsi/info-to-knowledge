"""What publishing the finished document came to: the file a reader opens, its images, and the judgement of the folder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["PublicationResult"]


@dataclass(frozen=True)
class PublicationResult:
    document: Path          # `<meeting name>/<meeting name>.md`
    image_count: int        # images left in the folder
    is_ok: bool             # did the written folder pass its checks
    report: str             # the checks' summary when it did not
