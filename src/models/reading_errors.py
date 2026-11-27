"""The failure vocabulary of the reading tools of phases 4 and 5: returned to the agent in words, never raised past
the tool."""

from __future__ import annotations

__all__ = ["ReadArgumentError"]


class ReadArgumentError(ValueError):
    """A reading tool's argument the model sent that the tool cannot take, such as a number written as a word."""
