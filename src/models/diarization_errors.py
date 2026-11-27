"""Why phase 1 wrote its transcript without speakers. Carriers only: `services/diarization_service.py` raises them and
phase 1's node records them; a run goes on with the plain transcript after either."""

from __future__ import annotations

__all__ = ["DiarizationUnavailable", "DiarizationFailed"]


class DiarizationUnavailable(RuntimeError):
    """Diarization cannot start on this machine: `pyannote.audio` is not installed, or the model is neither cached nor
    downloadable (no `HF_TOKEN`, or its terms not accepted)."""


class DiarizationFailed(RuntimeError):
    """The diarization model started and then failed on this recording's audio."""
