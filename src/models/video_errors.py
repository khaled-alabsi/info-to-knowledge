"""The failure vocabulary of the media phases. Carriers only: the phase nodes catch what the media
services raise, and neither imports the other to name a failure."""

from __future__ import annotations

__all__ = ["MissingMediaTool", "UnreadableVideo", "AudioExtractionFailed", "MissingFaceModel",
           "MissingPersonModel"]


class MissingMediaTool(RuntimeError):
    """`ffmpeg` or `ffprobe` is not installed, so no audio can be read out of a video."""


class UnreadableVideo(RuntimeError):
    """The target file could not be opened as a video, or holds no frame."""


class AudioExtractionFailed(RuntimeError):
    """The video has an audio stream, and neither ffmpeg nor pydub could extract it."""


class MissingFaceModel(RuntimeError):
    """The face detector's model file is not where `frames.faceModel` says, so a column of faces cannot be found."""


class MissingPersonModel(RuntimeError):
    """The person model's file is not where `frames.personModel` says, so a person's blocks cannot be left out of the
    capture loop's change."""
