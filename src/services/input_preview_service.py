"""What the run form's Range editor shows of an inbox recording before any run: a small frame of a
video at a moment, for its filmstrip; the loudness of an audio recording along its length, for its waveform; a small
picture of a PDF page, for its page strip. Each is made once by ffmpeg or PyMuPDF and kept in `ui/previews/`, named by
the file's path, size and time, so a changed file is made again."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy
import pymupdf

import config
from models.video_errors import AudioExtractionFailed
from services import audio_extraction_service
from services import recording_parts_service

__all__ = ["build_frame_preview", "compute_waveform", "build_page_preview", "PREVIEWS_DIR"]

PREVIEWS_DIR = config.UI_DIR / "previews"   # every preview made, one file each, made again when its recording changes
_FRAME_WIDTH = 240                          # a filmstrip frame's width in pixels: about 40 fit across a wide screen twice
_PAGE_WIDTH = 160                           # a page thumbnail's width in pixels, the page strip's card size
_WAVEFORM_RATE_HZ = 2000                    # samples a second read for the waveform: loudness, never speech, is shown
_MAX_BINS = 2000                            # the most waveform bars asked for: twice the widest screen's pixels
_JPEG_QUALITY = 5                           # ffmpeg's -q:v for a preview frame, 2 best to 31 worst: small and still sharp


def _build_key(recording: Path,
               *details: object) -> str:
    """A name for a preview of `recording`, changed when the file (each part of a folder) changes."""

    parts = recording_parts_service.list_recording_parts(recording)
    stamp = [(str(part.resolve()), part.stat().st_size, part.stat().st_mtime) for part in parts]

    return hashlib.sha256(json.dumps([stamp, *map(str, details)]).encode("utf-8")).hexdigest()[:32]


def _find_part_moment(recording: Path,
                      at_s: float) -> tuple[Path, float]:
    """The part of `recording` the moment `at_s` of the whole falls in, and the moment inside that part."""

    parts = recording_parts_service.list_recording_parts(recording)
    durations = [audio_extraction_service.read_duration(part) for part in parts]
    offset_s = 0.0
    for part, duration_s in zip(parts, durations):
        if at_s < offset_s + duration_s:
            return part, max(at_s - offset_s, 0.0)
        offset_s += duration_s

    return parts[-1], max(durations[-1] - 1.0, 0.0)


def build_frame_preview(recording: Path,
                        at_s: float) -> Path:
    """A small JPEG of the video `recording` (a file or a folder of parts) at `at_s` seconds of the whole, made once."""

    target = PREVIEWS_DIR / f"frame-{_build_key(recording, round(at_s, 1))}.jpg"
    if target.is_file():
        return target
    part, moment_s = _find_part_moment(recording, at_s)
    target.parent.mkdir(parents=True, exist_ok=True)
    # sought before the input is opened, which is fast and lands on the keyframe before: close enough for a filmstrip
    command = [audio_extraction_service.FFMPEG, "-loglevel", "error", "-y", "-ss", f"{moment_s:.2f}", "-i", str(part),
               "-frames:v", "1", "-vf", f"scale={_FRAME_WIDTH}:-2", "-q:v", str(_JPEG_QUALITY), str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionFailed(f"ffmpeg made no frame of {recording.name} at {at_s:.0f} s: "
                                    f"{exc.stderr.decode(errors='replace').strip()}") from exc
    if not target.is_file():
        raise AudioExtractionFailed(f"{recording.name} has no picture at {at_s:.0f} s")

    return target


def _read_samples(part: Path) -> numpy.ndarray:
    """A part's sound as 16-bit mono samples at _WAVEFORM_RATE_HZ, empty when it has no sound."""

    if not audio_extraction_service.has_audio_stream(part):
        return numpy.zeros(0, dtype=numpy.int16)
    command = [audio_extraction_service.FFMPEG, "-loglevel", "error", "-i", str(part), "-vn", "-ac", "1",
               "-ar", str(_WAVEFORM_RATE_HZ), "-f", "s16le", "-"]
    try:
        decoded = subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionFailed(f"ffmpeg could not read the sound of {part.name}: "
                                    f"{exc.stderr.decode(errors='replace').strip()}") from exc

    return numpy.frombuffer(decoded.stdout, dtype=numpy.int16)


def compute_waveform(recording: Path,
                     bins: int) -> list[float]:
    """The loudness of `recording` (a file or a folder of parts) in `bins` equal stretches of its length, each the
    stretch's loudest sample against the whole recording's loudest, from 0 to 1, made once."""

    bins = min(max(bins, 1), _MAX_BINS)
    target = PREVIEWS_DIR / f"waveform-{_build_key(recording, bins)}.json"
    if target.is_file():
        return json.loads(target.read_text(encoding="utf-8"))
    samples = numpy.concatenate([_read_samples(part) for part in recording_parts_service.list_recording_parts(recording)])
    if samples.size == 0:
        peaks = [0.0] * bins
    else:
        loudness = numpy.abs(samples.astype(numpy.int32))
        edges = numpy.linspace(0, loudness.size, bins + 1).astype(int)
        stretch_peaks = numpy.array([loudness[start:max(end, start + 1)].max() if start < loudness.size else 0
                                     for start, end in zip(edges[:-1], edges[1:])], dtype=float)
        loudest = stretch_peaks.max() or 1.0
        peaks = [round(float(value / loudest), 3) for value in stretch_peaks]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(peaks), encoding="utf-8")

    return peaks


def build_page_preview(pdf: Path,
                       page_number: int) -> Path:
    """A small JPEG of page `page_number` (from 1) of `pdf`, made once; a page the PDF lacks is refused by name."""

    target = PREVIEWS_DIR / f"page-{_build_key(pdf, page_number)}.jpg"
    if target.is_file():
        return target
    with pymupdf.open(pdf) as document:
        if not 1 <= page_number <= document.page_count:
            raise ValueError(f"{pdf.name} has no page {page_number}: it has {document.page_count}")
        page = document[page_number - 1]
        zoom = _PAGE_WIDTH / page.rect.width
        target.parent.mkdir(parents=True, exist_ok=True)
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(target), jpg_quality=80)

    return target
