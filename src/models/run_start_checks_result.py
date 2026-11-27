"""What `run` checked before its first phase, for the run header (`run_command._log_run_header`)."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["RunStartChecksResult"]


@dataclass
class RunStartChecksResult:
    """Phase 1's transcription engine and speaker diarization, each as its own line, and the models' capability
    lines (`model_capability_service.check_phase_capabilities`; none in a dry run)."""

    transcription: str
    diarization: str
    capabilities: list[str] = field(default_factory=list)
