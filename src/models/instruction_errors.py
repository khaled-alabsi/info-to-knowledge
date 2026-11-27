"""The failure vocabulary of the instruction store: an instruction file the program does not ship, or an edit it cannot
send to a model."""

from __future__ import annotations

__all__ = ["UnknownInstructionFileError", "InstructionRefusedError"]


class UnknownInstructionFileError(LookupError):
    """A file name, or a version of it, that the program's instructions do not hold."""


class InstructionRefusedError(ValueError):
    """An edit the program could not build a worker's message from, such as one that drops a placeholder it fills."""
