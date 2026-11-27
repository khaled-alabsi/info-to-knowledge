"""Audio as a model server takes it: compressed, and a long recording in windows.

Phase 1 writes 16 kHz mono 16-bit WAV files, which are about 115 MB an hour. A server on another machine is sent Opus
in an Ogg file instead (32 kbit/s, about 14 MB an hour), made by ffmpeg from the samples, so the request stays small and
the server decodes it as any audio file.

A recording longer than the window is cut into consecutive windows, each sent on its own; the caller moves each answer's
times back by the window's offset. Words said across the cut between two windows may be split or lost, as they are at
the edge of any window a speech model is given.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from models.endpoint_spec import MultipartFileSpec
from models.video_errors import AudioExtractionFailed, MissingMediaTool
from services import audio_extraction_service

__all__ = ["SAMPLE_RATE", "DEFAULT_WINDOW_S", "set_window_s", "read_wav_pcm", "join_parts_pcm",
           "build_compressed_file", "list_windows"]

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
# 20 minutes of Opus at 32 kbit/s are 4.8 MB, far under the 25 MB most speech servers take in one request
DEFAULT_WINDOW_S = 1200.0
_OPUS_BITRATE = "32k"

_window_s = DEFAULT_WINDOW_S


def set_window_s(seconds: float) -> float:
    """Make a window `seconds` long, for a test that cannot make a 20 minute recording; returns the length it replaced."""

    global _window_s
    previous, _window_s = _window_s, seconds

    return previous


def read_wav_pcm(wav_path: Path) -> bytes:
    """The samples of a 16 kHz mono 16-bit WAV, as phase 1's extraction writes it, without the file's header."""

    with wave.open(str(wav_path), "rb") as reader:
        if (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) != (SAMPLE_RATE, 1, BYTES_PER_SAMPLE):
            raise AudioExtractionFailed(f"{wav_path.name} is not 16 kHz mono 16-bit PCM: rate {reader.getframerate()}, "
                                        f"{reader.getnchannels()} channel(s), {reader.getsampwidth() * 8}-bit")

        return reader.readframes(reader.getnframes())


def join_parts_pcm(parts: list[tuple[Path, float]]) -> bytes:
    """The samples of every part placed at its offset in seconds on the meeting's clock, in one run of samples: silence
    fills a gap, and where a part runs past the next part's offset, the next part wins."""

    placed = [(round(offset_s * SAMPLE_RATE) * BYTES_PER_SAMPLE, read_wav_pcm(path)) for path, offset_s in parts]
    meeting = bytearray(max((start + len(pcm) for start, pcm in placed), default=0))
    for start, pcm in placed:
        meeting[start:start + len(pcm)] = pcm

    return bytes(meeting)


def build_compressed_file(pcm: bytes,
                          file_name: str) -> MultipartFileSpec:
    """The 16 kHz mono 16-bit samples `pcm` as an Ogg file of Opus audio named `file_name`, ready to send."""

    command = [audio_extraction_service.FFMPEG, "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
               "-i", "pipe:0", "-c:a", "libopus", "-b:a", _OPUS_BITRATE, "-f", "ogg", "pipe:1"]
    try:
        done = subprocess.run(command, input=pcm, check=True, capture_output=True)
    except FileNotFoundError as missing:
        raise MissingMediaTool("`ffmpeg` is not installed or not on PATH, and it compresses the audio sent to a model "
                               "server. On macOS: `brew install ffmpeg`.") from missing
    except subprocess.CalledProcessError as failed:
        raise AudioExtractionFailed(f"ffmpeg could not compress {file_name}: "
                                    f"{failed.stderr.decode(errors='replace').strip()}") from failed

    return MultipartFileSpec(file_name=file_name, content=done.stdout, media_type="audio/ogg")


def list_windows(wav_path: Path) -> list[tuple[float, MultipartFileSpec]]:
    """The WAV cut into consecutive windows, each as its offset in seconds on the WAV's clock and its compressed file;
    no window for a WAV with no samples."""

    pcm = read_wav_pcm(wav_path)
    window_bytes = max(1, round(_window_s * SAMPLE_RATE)) * BYTES_PER_SAMPLE
    window_list = []
    for number, start in enumerate(range(0, len(pcm), window_bytes), start=1):
        offset_s = start / BYTES_PER_SAMPLE / SAMPLE_RATE
        file = build_compressed_file(pcm=pcm[start:start + window_bytes], file_name=f"{wav_path.stem}-{number:03d}.ogg")
        window_list.append((offset_s, file))

    return window_list
