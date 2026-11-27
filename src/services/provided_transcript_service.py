"""Find the meeting tool's own transcript beside a recording, and describe it for phase 1's file. REQUIREMENTS.md R14.

A transcript belongs to a recording when its name starts with the recording's name, or when it lies in a folder whose
only recording that is: a meeting tool names its transcript after the meeting, not after the file it exported.
"""

from __future__ import annotations

from pathlib import Path

from services import inbox_service
from utils import provided_transcript_utils

__all__ = ["find_provided_transcript", "describe_provided_transcript", "TRANSCRIPT_SUFFIXES"]

TRANSCRIPT_SUFFIXES = (".vtt", ".srt")


def _is_listed_file(path: Path,
                    suffixes: tuple[str, ...]) -> bool:
    return path.is_file() and not path.name.startswith(".") and path.suffix.lower() in suffixes


def find_provided_transcript(video_path: Path) -> Path | None:
    """The transcript that belongs to `video_path`, or None. For a meeting recorded in parts (a folder), the one
    transcript file inside the folder, when there is exactly one: it covers the whole meeting."""

    if video_path.is_dir():
        inside = sorted(path for path in video_path.iterdir() if _is_listed_file(path, TRANSCRIPT_SUFFIXES))
        return inside[0] if len(inside) == 1 else None
    folder = video_path.parent
    transcripts = sorted(path for path in folder.iterdir() if _is_listed_file(path, TRANSCRIPT_SUFFIXES))
    videos = [path for path in folder.iterdir() if inbox_service.is_recording_file(path)]
    # A transcript named as the video, or starting with its name (`call.en.vtt`), unless another video's longer name
    # claims it: `call.mp4` beside `call-2.mp4` took `call-2.vtt`, which sorts first
    longer = [video.stem for video in videos if video.stem != video_path.stem and video.stem.startswith(video_path.stem)]
    named = [path for path in transcripts if path.stem.startswith(video_path.stem)
             and not any(path.stem.startswith(stem) for stem in longer)]
    exact = [path for path in named if path.stem == video_path.stem]
    if named:
        return (exact or named)[0]

    return transcripts[0] if transcripts and videos == [video_path] else None


def describe_provided_transcript(video_path: Path,
                                 segments: list[dict]) -> dict | None:
    """`{"file", "cues", "words", "agreement", "speakers"}` for the transcript beside `video_path`, compared with phase
    1's `segments`; None when there is none."""

    found = find_provided_transcript(video_path)
    if found is None:
        return None
    cues = provided_transcript_utils.read_cues(found.read_text(encoding="utf-8-sig", errors="replace"))

    return {"file": found.name, "cues": len(cues), "words": provided_transcript_utils.count_words(cues),
            "agreement": provided_transcript_utils.compute_agreement(cues, segments),
            "speakers": provided_transcript_utils.list_speakers(cues)}
