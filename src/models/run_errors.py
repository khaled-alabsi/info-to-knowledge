# <!-- sync: seed/models.run_errors v1 -->
"""The failures a run can end on, as types. Raised elsewhere; caught at the edges.

An exception is CONTRACT, not implementation. The handler is never in the layer that raises - the CLI
catches what the loader and the model registry raise - so a caller couples to this vocabulary, and it
must be able to do that without importing the implementation behind it. Catching `ModelUnavailable`
should not mean importing the whole model registry.

Data only, like everything else here: a class, a docstring, no functions and no I/O.

`agent_client/` keeps its own vocabulary in `agent_client/agent_errors.py` instead of using this one.
It has to drop into the next playbook unchanged, and it cannot reach into a host program's `models/`.

**Add this playbook's own failures here**, one class each, and never a program-wide `errors.py`: a
name that would fit any project ends up imported by every layer, and becomes the hub the whole program
depends on.
"""

from __future__ import annotations

__all__ = ["BadTarget", "ModelUnavailable", "PromptBuildError"]


class BadTarget(RuntimeError):
    """What a command was pointed at - a repository, a module, a session - cannot be resolved."""

    # Raised with the thing that was looked for and where, never a bare "not found": a refusal that
    # names what is missing is acted on in seconds, one that does not sends the reader to the source.


class ModelUnavailable(RuntimeError):
    """A configured model or its host cannot be used."""

    # Raised rather than falling back to another model: a silent fallback changes artifact quality with
    # nothing in the record explaining why, and the next person compares two runs that were never
    # comparable.


class PromptBuildError(RuntimeError):
    """A declared file, fragment or placeholder could not be resolved."""

    # Every failure in the prompt builder raises. A builder that skips an unresolvable entry and returns
    # the rest renders a prompt that looks complete and is missing the one document the phase cannot
    # work without - silently, on every item, forever.
