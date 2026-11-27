"""How phase 4 asks for one part of its document: the run, and the function that asks. REQUIREMENTS.md R12.

The calls path asks each part in a call of its own; the conversation asks it as a follow-up. The outline, section and
summary steps are the same for both, so they take this and never know which path they are on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from models.run_context import RunContext

__all__ = ["PartWritingContext"]


@dataclass(frozen=True)
class PartWritingContext:
    ctx: RunContext
    write_part: Callable[[str, str, Path, dict], None]  # (job kind, slug, written path, inputs); raises when blocked
