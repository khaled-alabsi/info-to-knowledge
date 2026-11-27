"""`02_speech.md` in the run folder: phase 2's paragraphs of an audio recording, written by the program from the
model's answer and read back by the validator, the merge and the notes file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models import output_contract
from models.run_paths import RunPaths
from utils import speech_markdown_utils

__all__ = ["build_speech_path", "write_speech", "read_speech", "read_speech_of"]


def build_speech_path(paths: RunPaths) -> Path:
    return paths.output_root / output_contract.SPEECH_FILE


def write_speech(paths: RunPaths,
                 parsed: dict[str, Any]) -> Path:
    """The paragraphs written as the file's own markdown; the file's path."""

    path = build_speech_path(paths)
    path.write_text(speech_markdown_utils.render_speech(parsed), encoding="utf-8")

    return path


def read_speech(paths: RunPaths) -> dict[str, Any] | None:
    """`{"profile", "paragraphs"}` of the run's `02_speech.md`; None when the run has none (a video's run) or it cannot
    be read."""

    return read_speech_of(paths.output_root)


def read_speech_of(run_folder: Path) -> dict[str, Any] | None:
    """`read_speech` of a run folder named by its path, for the code that has no run's paths (a conversion)."""

    path = run_folder / output_contract.SPEECH_FILE
    if not path.is_file():
        return None
    parsed, _why = speech_markdown_utils.parse_speech(path.read_text(encoding="utf-8"))

    return parsed
