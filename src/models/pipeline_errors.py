"""The failure vocabulary of a phase-file pipeline. Carriers only: the handler is the command line,
the raisers are services, and neither should import the other to name a failure."""

from __future__ import annotations

__all__ = ["PhaseBlocked", "MissingPhaseInput", "MissingModelCapability", "EngineUnavailable",
           "PreprocessingDeclarationError", "DescriptionsDoNotFit"]


class PhaseBlocked(RuntimeError):
    """A step of a phase was blocked: the phase stops there, and nothing after it runs."""


class MissingPhaseInput(RuntimeError):
    """A phase was asked to start and the file the phase before it writes is not on disk."""


class MissingModelCapability(RuntimeError):
    """A phase is given a model that cannot do what the phase's calls need."""


class EngineUnavailable(ValueError):
    """The configured transcription engine cannot run: its name is not one this program has, or its package is not
    installed. Raised instead of falling back, because a silent fallback changes which engine wrote a transcript with
    nothing recording why."""


class PreprocessingDeclarationError(ValueError):
    """An ingesting profile's preparation steps (`agent-context/preprocessing/<profile>.json`) are not of the shape a
    run can follow: a step reading the items of no earlier step, two steps of one id, or a step with no section."""


class DescriptionsDoNotFit(ValueError):
    """A fast-processing run's frame descriptions leave the document model's window too little room to write in."""
