"""A recording that is one video file, or a folder of video files that are the parts of one meeting, opened as one video.

A meeting recorded in several files (the recording stopped and started again) is a folder of two or more videos. Its
parts are read in natural name order ("part 2" before "part 10") and every phase sees them as one video: frame numbers
and times continue from one part to the next. No file is joined or re-encoded; the parts are known only here, where a
file is opened.
"""

from __future__ import annotations

import bisect
import logging
import re
from pathlib import Path

import av
import cv2
import numpy

from models.video_errors import UnreadableVideo
from services import audio_extraction_service
from services import inbox_service

__all__ = ["build_natural_key", "list_recording_parts", "is_multi_part", "RecordingContext", "open_recording", "list_part_durations", "move_transcript_times"]

_LOG = logging.getLogger(__name__)
_NUMBER = re.compile(r"(\d+)")
# How far before a frame's time a seek lands the reader, so that it reads forward onto the frame: a seek by time in a
# screen recording landed 1.1 s after the time asked for, and one by frame number lands on the average rate's guess
_SEEK_LEAD_S = 3.0
# How many times a seek that still landed after the frame is tried again, each time twice as early
_SEEK_TRIES = 4


def _read_part_frame_times(part: Path,
                           count: int,
                           fps: float) -> list[float]:
    """Each frame's time in `part`, in seconds from its start, in the order a reader returns them, read from the
    packets' timestamps so nothing is decoded. A screen recording has a variable frame rate (120 a second while the
    screen moves, a few while it is still), so a frame number divided by the average rate is up to minutes away from
    the frame's time. A part whose packets carry no time keeps that guess."""

    try:
        with av.open(str(part)) as container:
            stream = container.streams.video[0]
            start = stream.start_time or 0
            times = sorted(float((packet.pts - start) * stream.time_base) for packet in container.demux(stream)
                           if packet.pts is not None)
    except (av.FFmpegError, IndexError) as error:
        _LOG.warning("%s: its frame times could not be read from its packets (%s)", part.name, error)
        times = []
    if len(times) < count:
        if times:
            _LOG.warning("%s: %d of %d frames carry no time", part.name, count - len(times), count)
        # The rest of the frames get the average rate's guess, which can be minutes off on a screen recording
        last = times[-1] if times else -1 / fps
        times += [last + (offset + 1) / fps for offset in range(count - len(times))]

    return times[:count]


def build_natural_key(name: str) -> list:
    """The name cut into text and numbers, so "part 2" sorts before "part 10": the order a meeting's parts are read
    in, and the order an upload proposes them in."""

    return [int(part) if part.isdecimal() else part.casefold() for part in _NUMBER.split(name)]


def list_recording_parts(recording: Path) -> list[Path]:
    """The video or audio files the recording is made of, in the order they are read: the file itself, or a folder's videos in
    natural name order."""

    if recording.is_dir():
        return sorted((path for path in recording.iterdir() if inbox_service.is_recording_file(path)), key=lambda path: build_natural_key(path.name))

    return [recording]


def is_multi_part(recording: Path) -> bool:
    """Whether the recording is a folder of two or more videos: one meeting in parts."""

    return recording.is_dir() and len(list_recording_parts(recording)) >= 2


class RecordingContext:
    """The parts' videos open as one video: frame numbers run on from one part into the next. Every part must have the
    same frame rate, or a frame number would not say one time. Lives while the recording is read; `release` ends it."""

    def __init__(self,
                 parts: list[Path]) -> None:
        self._parts = parts
        self._counts: list[int] = []
        self._fps = 0.0
        self._size = (0, 0)
        for part in parts:
            capture = cv2.VideoCapture(str(part))
            fps, count = capture.get(cv2.CAP_PROP_FPS), int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            is_part_open = capture.isOpened()
            capture.release()
            if not is_part_open or fps <= 0 or count <= 0:
                raise UnreadableVideo(f"{part} could not be opened as a video, or it holds no frame")
            if self._fps and abs(fps - self._fps) > 0.01:
                raise UnreadableVideo(f"{part} runs at {fps:.2f} frames a second and {parts[0]} at {self._fps:.2f}: "
                                      "the parts of one recording must share their frame rate")
            if not self._counts:
                self._fps, self._size = fps, size
            self._counts.append(count)
        self._index, self._capture = -1, None
        self._times: list[list[float]] | None = None
        self._open_part(0)

    def _open_part(self,
                   index: int) -> bool:
        if self._capture is not None:
            self._capture.release()
        self._index, self._capture = index, None
        if index >= len(self._parts):
            return False
        self._capture = cv2.VideoCapture(str(self._parts[index]))

        return True

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_count(self) -> int:
        return sum(self._counts)

    @property
    def width(self) -> int:
        return self._size[0]

    @property
    def height(self) -> int:
        return self._size[1]

    def is_open(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def skip_frame(self) -> bool:
        """Move on one frame without decoding it, into the next part when this one ends. False after the last."""

        while self._capture is not None:
            if self._capture.grab():
                return True
            if not self._open_part(self._index + 1):
                return False

        return False

    def decode_frame(self) -> tuple[bool, numpy.ndarray | None]:
        """The frame `skip_frame` moved onto, decoded."""

        return self._capture.retrieve() if self._capture is not None else (False, None)

    def read_frame(self) -> tuple[bool, numpy.ndarray | None]:
        return self.decode_frame() if self.skip_frame() else (False, None)

    def _list_part_times(self) -> list[list[float]]:
        """Each part's frame times, read once and kept while the recording is open."""

        if self._times is None:
            self._times = [_read_part_frame_times(part, count, self._fps)
                           for part, count in zip(self._parts, self._counts)]

        return self._times

    def list_frame_times(self) -> list[float]:
        """Every frame's time in seconds from the recording's start, by frame number: a part's times run on from the
        end of the part before it."""

        times, offset = [], 0.0
        for part_times, duration in zip(self._list_part_times(), self.list_part_durations()):
            times += [offset + time for time in part_times]
            offset += duration

        return times

    def _move_before_frame(self,
                           part_times: list[float],
                           frame_index: int) -> bool:
        """Leave this part's reader on the frame before `frame_index`, so the next `skip_frame` reads that frame: seek
        to a little before its time, find where the seek landed by the time of the frame it reads, and step forward.
        A seek that landed after the frame is tried again from earlier."""

        if frame_index == 0:
            return self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        lead = _SEEK_LEAD_S
        for _ in range(_SEEK_TRIES):
            target = max(0.0, part_times[frame_index] - lead)
            if not self._capture.set(cv2.CAP_PROP_POS_MSEC, target * 1000) or not self._capture.grab():
                return False
            landed = bisect.bisect_left(part_times, self._capture.get(cv2.CAP_PROP_POS_MSEC) / 1000 - 1e-4)
            if landed < frame_index:
                return all(self._capture.grab() for _ in range(frame_index - 1 - landed))
            lead *= 2

        return False

    def move_to_frame(self,
                      frame_number: int) -> bool:
        """Seek to a frame number of the whole recording, so the next `read_frame` returns that frame: it opens the part
        that holds it and reads forward to it by the frames' own times."""

        remaining = int(frame_number)
        for index, count in enumerate(self._counts):
            if remaining < count:
                if index != self._index:
                    self._open_part(index)
                return self._move_before_frame(self._list_part_times()[index], remaining)
            remaining -= count
        self._open_part(len(self._parts))

        return False

    def list_part_durations(self) -> list[float]:
        """Each part's length in seconds, in reading order: its last frame's time plus one frame at the average rate."""

        return [(part_times[-1] if part_times else 0.0) + 1 / self._fps for part_times in self._list_part_times()]

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None


def open_recording(recording: Path) -> RecordingContext:
    """The recording, a file or a folder of parts, open at its first frame. Raises UnreadableVideo by name."""

    parts = list_recording_parts(recording)
    if not parts:
        raise UnreadableVideo(f"{recording} holds no video")

    return RecordingContext(parts)


def list_part_durations(recording: Path) -> list[float]:
    """Each part's length in seconds, in reading order: from ffprobe for audio parts, which have no frames, and from
    the frame times for video parts."""

    if inbox_service.is_audio_recording(recording):
        return [audio_extraction_service.read_duration(part) for part in list_recording_parts(recording)]
    capture = open_recording(recording)
    try:
        return capture.list_part_durations()
    finally:
        capture.release()


def _move_entry_times(entry: dict,
                      offset_s: float) -> dict:
    """A segment or a word with its start and end plus `offset_s`, and its words moved the same way."""

    moved = {**entry, **{key: entry[key] + offset_s for key in ("start", "end") if isinstance(entry.get(key), (int, float))}}
    if isinstance(entry.get("words"), list):
        moved["words"] = [_move_entry_times(word, offset_s) for word in entry["words"]]

    return moved


def move_transcript_times(transcript: dict,
                          offset_s: float) -> dict:
    """One part's transcript moved to the meeting's time: every segment's and word's start and end plus `offset_s`."""

    return {**transcript, "segments": [_move_entry_times(segment, offset_s) for segment in transcript.get("segments") or []]}
