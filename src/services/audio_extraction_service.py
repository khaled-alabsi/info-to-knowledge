"""The audio of a video, as the 16 kHz mono WAV the transcription reads.

Carried from the archived program: ffmpeg does the extraction, and pydub is the second path when
ffmpeg fails on a file that does have audio. What changed is how a video with NO audio is detected.
The archived program ran ffmpeg and matched one sentence of its error text, which depends on the
ffmpeg version. Here `ffprobe` is asked whether an audio stream exists, before anything is extracted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydub import AudioSegment

from models.video_errors import AudioExtractionFailed, MissingMediaTool

__all__ = ["has_audio_stream", "read_duration", "extract_audio", "FFMPEG", "FFPROBE"]

# The two binaries, by the name they are found under.
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

_SAMPLE_RATE_HZ = 16000
_CHANNELS = 1


def _run_tool(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise MissingMediaTool(f"`{command[0]}` is not installed or not on PATH. On macOS: `brew install ffmpeg`, "
                               "which provides both ffmpeg and ffprobe.") from exc


def _run_ffprobe(arguments: list[str],
                 media_path: Path) -> str:
    """What ffprobe prints for `media_path` with these arguments; a file it cannot read is a named failure."""

    try:
        probed = _run_tool([FFPROBE, "-v", "error", *arguments, str(media_path)])
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionFailed(f"ffprobe could not read {media_path}: "
                                    f"{exc.stderr.decode(errors='replace').strip()}") from exc

    return probed.stdout.decode(errors="replace").strip()


def has_audio_stream(video_path: Path) -> bool:
    """Does this video carry at least one audio stream? Asked of ffprobe, never read off an error."""

    return bool(_run_ffprobe(["-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0"], video_path))


def read_duration(recording_path: Path) -> float:
    """A media file's length in seconds, as ffprobe reads it from the container: how an audio part's length is known,
    since an audio file has no frames to count."""

    printed = _run_ffprobe(["-show_entries", "format=duration", "-of", "csv=p=0"], recording_path)
    try:
        return float(printed)
    except ValueError as exc:
        # A container that stores no length prints `N/A` or nothing
        raise AudioExtractionFailed(f"ffprobe gave no duration for {recording_path}: {printed!r}") from exc


def extract_audio(video_path: Path,
                  audio_path: Path,
                  start_s: float | None = None,
                  end_s: float | None = None) -> Path | None:
    """Write the video's audio to `audio_path`, from `start_s` to `end_s` when given (a run's range),
    the WAV then starting at `start_s`. `None` when the video has no audio stream at all."""

    # STEP 1: a video with no audio stream is an answer, not a failure
    if not has_audio_stream(video_path):
        return None

    # STEP 2: extract with ffmpeg, as PCM 16-bit, 16 kHz, mono; a window is cut after the input is opened, so it starts
    # at the sample asked for and not at the keyframe before it
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    window = [*(["-ss", f"{start_s:.3f}"] if start_s else []), *(["-to", f"{end_s:.3f}"] if end_s is not None else [])]
    try:
        _run_tool([FFMPEG, "-loglevel", "error", "-y", "-i", str(video_path), *window, "-vn", "-acodec", "pcm_s16le",
                   "-ar", str(_SAMPLE_RATE_HZ), "-ac", str(_CHANNELS), str(audio_path)])

        return audio_path
    except subprocess.CalledProcessError as ffmpeg_error:
        ffmpeg_message = ffmpeg_error.stderr.decode(errors="replace").strip()

    # STEP 3: the carried second path, for a file ffmpeg's direct extraction could not handle
    try:
        audio = AudioSegment.from_file(str(video_path)).set_channels(_CHANNELS).set_frame_rate(_SAMPLE_RATE_HZ)
        start_ms = round((start_s or 0.0) * 1000)
        end_ms = round(end_s * 1000) if end_s is not None else len(audio)
        audio[start_ms:end_ms].export(str(audio_path), format="wav")
    except Exception as pydub_error:  # noqa: BLE001 (pydub raises whatever its decoder raised)
        raise AudioExtractionFailed(f"{video_path} has an audio stream and it could not be extracted. "
                                    f"ffmpeg said: {ffmpeg_message}. pydub said: {pydub_error}") from pydub_error

    return audio_path
