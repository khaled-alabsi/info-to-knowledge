"""Speech to text with mlx-whisper, on the GPU through Metal.

WHY IT EXISTS. faster-whisper runs on CTranslate2, which has no Metal backend at all, so on Apple
silicon it can only use the CPU cores. MLX runs the same model on the GPU. Measured elsewhere on
Apple silicon: 30 to 40 percent faster on comparable models, and more on the turbo ones.

WHY IT TRANSCRIBES IN CHUNKS. macOS kills a Metal command buffer that runs long enough to block the
interface. Measured here: a 4.5 minute file finished fine, and an 87 minute file in one call died with
`[METAL] Command buffer execution failed: Impacting Interactivity`. So the audio is decoded once and
handed over in windows of `audio.mlx.chunkSeconds`, each window's times shifted back to the whole
file's clock. Set it to 0 for one call, which is the old behaviour and fine for a short file.

THE LANGUAGE IS DETECTED ONCE, ON THE FIRST WINDOW, AND FORCED ON THE REST. Otherwise a quiet window
in the middle of a German meeting is detected as English and that window's text comes back translated
or wrong, with nothing saying so.

TWO MORE DIFFERENCES FROM FASTER-WHISPER. MLX takes a Hugging Face REPO ID where faster-whisper takes
a size, so `audio.mlx.repositories` (default `config.MLX_WHISPER_REPOSITORIES`) maps one to the other and an unmapped size is refused by
name. And MLX HAS NO VAD FILTER: silence is decoded instead of skipped, and only `no_speech_threshold`
discards it afterwards, so on a meeting with long pauses some of the speed is given back. Compare the
two engines on the same file rather than assuming.

IT REPORTS NO LANGUAGE PROBABILITY. The contract allows `None` there, and phase 1 already writes
`None` for audio with no speech, so nothing downstream breaks. It does mean a detection this engine
got wrong cannot be spotted from the confidence alone.

The imports are LAZY, in the functions that need them, because `mlx` is an optional dependency: a
module-level import would fail every run on a machine that has only faster-whisper installed.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable

from config import AudioSettings

__all__ = ["produce_transcript", "is_installed", "set_transcriber", "set_audio_loader",
           "ENGINE_NAME", "SAMPLE_RATE"]

ENGINE_NAME = "mlx"
PACKAGE = "mlx_whisper"
SAMPLE_RATE = 16000
NOT_INSTALLED = (f"the {ENGINE_NAME!r} engine needs the optional package `mlx-whisper`. "
                 f"Install it with: .venv/bin/python -m pip install mlx-whisper")

# What performs the transcription, and what decodes the audio file into samples. `None` means resolve
# the real one from `mlx_whisper` on first use; the suite sets fakes through the setters, so no model
# is downloaded and no GPU is touched.
_transcriber: Callable[..., Any] | None = None
_audio_loader: Callable[..., Any] | None = None


def _read_transcriber() -> Callable[..., Any]:
    if _transcriber is not None:
        return _transcriber

    return importlib.import_module(PACKAGE).transcribe


def _read_audio_loader() -> Callable[..., Any]:
    if _audio_loader is not None:
        return _audio_loader

    return importlib.import_module(f"{PACKAGE}.audio").load_audio


def _read_repository(settings: AudioSettings) -> str:
    """The repo id this run's model size resolves to, or a refusal naming the sizes that do resolve."""

    repositories = settings.mlx.repositories
    size = settings.whisper_model
    if size in repositories:
        return repositories[size]
    if "/" in size:
        # Already a repo id. Passing one through is how an unlisted model is used without an edit.
        return size

    raise ValueError(f"the {ENGINE_NAME!r} engine has no repository for model {size!r}. "
                     f"Known: {', '.join(sorted(repositories))}. "
                     f"Or set `audio.whisperModel` to a Hugging Face repo id directly.")


def _move_segment_to_file_clock(segment: dict,
                                offset_s: float) -> dict:
    """One segment of a window, its times and its words' times moved by `offset_s` onto the whole file's clock. MLX
    returns openai-whisper's shape: a dict per segment, `words` absent when not requested."""

    # Phase 2 cuts its frame slices from these numbers, so an unshifted window would put every later frame against
    # the wrong speech.
    words = [{"word": word["word"], "start": word["start"] + offset_s, "end": word["end"] + offset_s,
              "probability": word.get("probability")}
             for word in (segment.get("words") or [])]

    return {"text": segment.get("text", ""), "start": segment["start"] + offset_s,
            "end": segment["end"] + offset_s, "words": words}


def _list_windows(sample_count: int,
                  chunk_seconds: float) -> list[tuple[int, int]]:
    """Start and end sample of each window. One window covering everything when chunking is off."""

    step = int(chunk_seconds * SAMPLE_RATE)
    # A window shorter than one sample is no window: the file is read whole, as with chunking off
    if step < 1:
        return [(0, sample_count)]

    return [(start, min(start + step, sample_count)) for start in range(0, max(sample_count, 1), step)]


def set_transcriber(transcriber: Callable[..., Any] | None) -> Callable[..., Any] | None:
    """Swap the transcriber, for a test; returns the one it replaced."""

    global _transcriber
    previous, _transcriber = _transcriber, transcriber

    return previous


def set_audio_loader(loader: Callable[..., Any] | None) -> Callable[..., Any] | None:
    """Swap the audio loader, for a test; returns the one it replaced."""

    global _audio_loader
    previous, _audio_loader = _audio_loader, loader

    return previous


def is_installed() -> tuple[bool, str]:
    """Whether this engine can run at all, asked BEFORE a run starts rather than after the audio has
    been extracted."""

    if _transcriber is not None:
        return True, ""
    try:
        importlib.import_module(PACKAGE)
    except ImportError:
        return False, NOT_INSTALLED

    return True, ""


def produce_transcript(audio_path: Path,
                       settings: AudioSettings) -> dict:
    """The engine contract: see `transcription_service` for the dictionary every engine returns."""

    mlx_settings = settings.mlx
    repository = _read_repository(settings)
    transcribe = _read_transcriber()
    samples = _read_audio_loader()(str(audio_path), sr=SAMPLE_RATE)
    language = settings.language
    segments: list[dict] = []
    for start, end in _list_windows(len(samples), mlx_settings.chunk_seconds):
        reply = transcribe(samples[start:end],
                           path_or_hf_repo=repository,
                           word_timestamps=True,
                           no_speech_threshold=mlx_settings.no_speech_threshold,
                           condition_on_previous_text=mlx_settings.condition_on_previous_text,
                           language=language)
        offset_s = start / SAMPLE_RATE
        segments.extend(_move_segment_to_file_clock(segment, offset_s) for segment in (reply.get("segments") or []))
        # Detected on the first window, forced on every one after it.
        language = language or reply.get("language")

    return {"language": language,
            "languageProbability": None,
            "model": repository,
            "segments": segments}
