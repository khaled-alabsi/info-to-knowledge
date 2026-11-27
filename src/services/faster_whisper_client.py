"""Speech to text with faster-whisper, on the CPU.

Runs with the `audio.fasterWhisper` settings of `config.py`, which hold the values that gave a correct German
transcript on the archived program.

EVERY ARGUMENT HERE IS FASTER-WHISPER'S OWN. `vad_filter` and `beam_size` do not exist on the other
engine, which is why they are inside this module and not in the dispatcher.

Word timings are what the per-frame slices are cut from, so they are always requested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from faster_whisper import WhisperModel

from config import AudioSettings

__all__ = ["produce_transcript", "is_installed", "set_model_factory", "MODEL_FACTORY", "ENGINE_NAME"]

ENGINE_NAME = "faster-whisper"

# What builds the speech model. A module-level name with a setter, because the suite runs with no
# model download and no audio: it swaps in a fake that records what it was asked.
MODEL_FACTORY: Callable[..., Any] = WhisperModel


def _describe_segment(segment: Any) -> dict:
    # `segment.words` is None when the model returned no word timings for it.
    words = [{"word": word.word, "start": word.start, "end": word.end, "probability": word.probability}
             for word in (segment.words or [])]

    return {"text": segment.text, "start": segment.start, "end": segment.end, "words": words}


def set_model_factory(factory: Callable[..., Any]) -> None:
    global MODEL_FACTORY
    MODEL_FACTORY = factory


def is_installed() -> tuple[bool, str]:
    """Always true: faster-whisper is a pinned requirement, so the import at the top would have
    failed the run long before this is asked."""

    return True, ""


def produce_transcript(audio_path: Path,
                       settings: AudioSettings) -> dict:
    """The engine contract: see `transcription_service` for the dictionary every engine returns."""

    engine_settings = settings.faster_whisper
    model = MODEL_FACTORY(settings.whisper_model, device=engine_settings.device, compute_type=engine_settings.compute_type)
    segments, info = model.transcribe(str(audio_path),
                                      beam_size=engine_settings.beam_size,
                                      word_timestamps=True,
                                      vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": engine_settings.vad_min_silence_ms},
                                      language=settings.language)
    described = [_describe_segment(segment) for segment in segments]

    return {"language": info.language,
            "languageProbability": info.language_probability,
            "model": settings.whisper_model,
            "segments": described}
