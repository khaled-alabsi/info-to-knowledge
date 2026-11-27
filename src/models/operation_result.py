"""What one backend operation did, for whichever front end asked: the CLI prints the lines and exits with the code; the
web server returns both as `{exit_code, output}`, with `records` when it has any; the MCP server returns them as its tool's text."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["OperationResult"]


@dataclass
class OperationResult:
    """`exit_code` as the CLI's: 0 done or listed, 1 failed while working, 2 the request cannot be carried out."""

    exit_code: int
    lines: list[str] = field(default_factory=list)
    # One record per thing the lines name, for a front end that shows them itself (the frames a Refine proposes)
    records: list[dict] = field(default_factory=list)

    @property
    def output(self) -> str:
        return "\n".join(self.lines)
