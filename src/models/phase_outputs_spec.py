"""What one numbered phase writes into a run folder, for `i2k reset-to` to remove."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PhaseOutputsSpec"]


@dataclass(frozen=True)
class PhaseOutputsSpec:
    files: tuple[str, ...] = ()                     # files at the run folder's root
    dirs: tuple[str, ...] = ()                      # folders at the run folder's root
    call_prefixes: tuple[str, ...] = ()             # `calls/<prefix>*.json`; the empty prefix is phase 4's rest
    special_targets: tuple[str, ...] = ()           # what cannot be named as a path: frame notes, published folders
