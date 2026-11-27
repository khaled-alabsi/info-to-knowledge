# <!-- sync: seed/services.artifact_snapshot_service v1 -->
"""Pre-attempt snapshots, and the restore that makes a continue-style retry safe.

The problem this solves: reusing a session id to continue a worker is what preserves its context
across a failed attempt, but a resumed worker appends to files it already appended to - so a retry
would write a SECOND trace into the same chain file and a second copy of every record into the same
graph fragment.

Restoring the snapshot before the retry removes the hazard. The worker keeps its conversation; the
disk is back to the scaffold it started from, so there is nothing to double-append to.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Snapshot:
    """Bytes of a set of files as they were before an attempt, plus which ones did not exist."""

    directory: Path
    files: dict[str, Path]          # logical name -> live path
    absent: set[str]                # logical names that did not exist at snapshot time

    @classmethod
    def take(cls,
             targets: list[Path],
             into: Path) -> "Snapshot":
        into.mkdir(parents=True, exist_ok=True)
        files = {path.name: path for path in targets}      # the one place a name is derived
        absent = set()
        for name, path in files.items():
            if path.is_file():
                shutil.copy2(path, into / name)
            else:
                absent.add(name)

        return cls(directory=into, files=files, absent=absent)

    def restore(self) -> list[str]:
        """Put every snapshotted file back and delete anything the attempt created. Returns names."""

        restored = []
        for name, path in self.files.items():
            saved = self.directory / name
            if name in self.absent:
                # It did not exist before the attempt, so anything there now is the attempt's
                # partial output - a half-written fragment that would be appended to on retry.
                if path.exists():
                    path.unlink()
                    restored.append(f"{name} (deleted)")
                continue
            if saved.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved, path)
                restored.append(name)

        return restored

    def read_content(self,
                     name: str) -> str | None:
        saved = self.directory / name
        if not saved.is_file():
            return None

        return saved.read_text(encoding="utf-8", errors="replace")


def read_scaffold_of(text: str,
                     marker: str) -> str:
    """Everything above the analysis marker - the part a worker must never rewrite."""

    index = text.find(marker)

    return text if index == -1 else text[:index]
