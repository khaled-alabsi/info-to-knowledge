"""Which recordings in the drop folder (videos and audio files) have no finished document yet.

WHAT "NEW" MEANS, AND WHY IT IS NOT A QUEUE. An item is new when the output root holds no FINISHED
run for it, and finished is decided from bytes on disk: phase 4's document, `04_document.md`, exists. A run
stopped after it, in the review, is finished here and is not picked up again. There is no
queue file, no marker and no processed list, because a second record of what was done disagrees
with the output the moment a run is deleted, moved, or re-done by hand, and the disagreement is
silent. The output folder is the only record, so deleting it is how you ask for an item again.

A HALF-FINISHED RUN IS STILL NEW. An item whose run stopped after phase 2 has no document, so it is
picked up again. Phases already on disk are read rather than re-run, so the second pass starts where
the first stopped and the transcription is not paid for twice.
"""

from __future__ import annotations

from pathlib import Path

from models import output_contract
from models.ui_errors import UnknownInboxItemError

__all__ = ["VIDEO_SUFFIXES", "AUDIO_SUFFIXES", "PDF_SUFFIXES", "OFFICE_SUFFIXES", "SLIDES_SUFFIXES", "DOCUMENT_SUFFIXES", "RECORDING_SUFFIXES",
           "is_recording_file", "is_audio_recording", "is_document", "describe_file_type",
           "build_run_folder", "is_item_finished", "resolve_listed_video", "list_inbox_videos", "list_new_videos"]

# Lower case and with the dot, exactly as `Path.suffix` spells it. A file whose suffix is not here is
# left alone rather than refused: an inbox is a folder people also drop notes and subtitles into.
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".wmv", ".flv", ".ts")
# A recording with no picture: phase 2 structures its speech in place of taking frames
AUDIO_SUFFIXES = (".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus")
# A document read as a recording: each page a frame, its text what is said
PDF_SUFFIXES = (".pdf",)
# A document from an office suite, turned into markdown by pandoc: the office workflow
OFFICE_SUFFIXES = (".docx", ".odt")
# A deck of slides, drawn into a PDF and read slide by slide as pictures: the PPT workflow
SLIDES_SUFFIXES = (".pptx", ".odp")
# A document of any kind: it has no time and no sound, only text and pictures
DOCUMENT_SUFFIXES = PDF_SUFFIXES + OFFICE_SUFFIXES + SLIDES_SUFFIXES
RECORDING_SUFFIXES = VIDEO_SUFFIXES + AUDIO_SUFFIXES + DOCUMENT_SUFFIXES


def is_recording_file(path: Path) -> bool:
    """A real video or audio file, not a directory, not a dot file, not a macOS resource fork."""

    return path.is_file() and not path.name.startswith(".") and path.suffix.lower() in RECORDING_SUFFIXES


def is_audio_recording(recording: Path) -> bool:
    """Whether a recording has no picture: an audio file, or a folder whose recording files are all audio."""

    if recording.is_dir():
        parts = [path for path in recording.iterdir() if is_recording_file(path)]

        return bool(parts) and all(path.suffix.lower() in AUDIO_SUFFIXES for path in parts)

    return recording.suffix.lower() in AUDIO_SUFFIXES


def is_document(recording: Path) -> bool:
    """Whether the input is a document (a PDF, a Word or OpenDocument file): no time, no sound, no parts to merge."""

    return recording.suffix.lower() in DOCUMENT_SUFFIXES


def describe_file_type(recording: Path) -> str:
    """What the recording's own file is, `pdf`, `office`, `ppt`, `audio` or `video`, by its suffix (a folder of parts by its
    files): a video processed as audio is still a video here."""

    if recording.suffix.lower() in PDF_SUFFIXES:
        return "pdf"
    if recording.suffix.lower() in OFFICE_SUFFIXES:
        return "office"
    if recording.suffix.lower() in SLIDES_SUFFIXES:
        return "ppt"

    return "audio" if is_audio_recording(recording) else "video"


def build_run_folder(output_root: Path,
                     video: Path) -> Path:
    """Where one inbox video's run lands. The video's STEM names it, so re-dropping a file under the
    same name resumes its own folder instead of starting a second one beside it. A folder of a meeting's parts is named
    by the folder's whole name."""

    return output_root / (video.name if video.is_dir() else video.stem)


def is_item_finished(output_root: Path,
                     video: Path) -> bool:
    """True when a run for this video wrote phase 4's document, `04_document.md`. That file is the deliverable, so its
    presence is the only claim worth trusting: a run that exited 0 having written nothing does not
    make one appear."""

    return build_run_folder(output_root, video).joinpath(output_contract.SUMMARY_FILE).is_file()


def _find_folder_recording(folder: Path) -> Path | None:
    """What a folder is to process: its one recording (REQUIREMENTS.md R14), the folder itself when it holds two or more
    (one meeting in parts), or None when it holds none."""

    videos = [path for path in folder.iterdir() if is_recording_file(path)]
    if not videos:
        return None

    return videos[0] if len(videos) == 1 else folder


def list_inbox_videos(inbox_root: Path) -> list[Path]:
    """Every video directly in the inbox and each meeting folder in it (its one video, or the folder itself when it
    holds several), in the order of the names in the inbox. A folder is one meeting: its recording, and optionally the meeting tool's own
    transcript beside it (R14). A folder of several videos is one meeting recorded in parts, listed as the folder.
    Not searched deeper. A missing inbox is an empty inbox, never an error, so
    a fresh clone runs the command fine."""

    if not inbox_root.is_dir():
        return []
    items = []
    for path in sorted(inbox_root.iterdir(), key=lambda entry: entry.name):
        if is_recording_file(path):
            items.append(path)
        elif path.is_dir() and not path.name.startswith("."):
            recording = _find_folder_recording(path)
            if recording is not None:
                items.append(recording)

    return items


def list_new_videos(inbox_root: Path,
                    output_root: Path) -> list[Path]:
    """The inbox videos with no finished document, in the order they will be processed."""

    return [video for video in list_inbox_videos(inbox_root) if not is_item_finished(output_root, video)]


def resolve_listed_video(inbox_root: Path,
                         video: Path) -> Path:
    """`video` resolved, when it is one the inbox lists; a path the inbox does not list is refused by name."""

    resolved = video.resolve()
    if resolved not in {listed.resolve() for listed in list_inbox_videos(inbox_root)}:
        raise UnknownInboxItemError(f"{video.name} is not a recording in the inbox")

    return resolved
