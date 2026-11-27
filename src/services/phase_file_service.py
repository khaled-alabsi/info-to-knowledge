"""Numbered phase files: every phase writes one, and the next phase reads it from disk.

That is what makes one phase runnable alone, hours later, against files another run produced or a
person edited. The table of files is `output_contract.PHASE_FILES`; this module is the only code that
turns a phase number into a path.

A phase whose input is missing REFUSES, naming the file and the phase that writes it. Reading
nothing and carrying on produces a run that completes over an empty input and reports success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models import output_contract
from models.pipeline_errors import MissingPhaseInput
from models.run_paths import RunPaths
from services import document_pass_service
from utils import display_path_utils
from utils import json_store_utils

__all__ = ["build_phase_file_path", "has_phase_file", "read_phase_file", "write_phase_file", "read_transcript_language", "require_phase_output",
           "has_run_frames", "is_audio_run", "describe_frameless_run"]


def build_phase_file_path(paths: RunPaths,
                          number: int) -> Path:
    if number not in output_contract.PHASE_FILES:
        raise KeyError(f"phase {number} is not declared in output_contract.PHASE_FILES")
    # phase 7's folder may still carry its name from before the rename
    if output_contract.PHASE_FILES[number]["phase"] == output_contract.DOCUMENT_RESHAPING_PHASE:
        return document_pass_service.build_reshaping_dir(paths.output_root) / output_contract.DOCUMENT_RESHAPING_FILE

    return paths.output_root / output_contract.PHASE_FILES[number]["file"]


def _is_written(path: Path) -> bool:
    """Is there a file with something in it? Zero bytes is what a killed write leaves behind: missing as much as no
    file."""

    return path.is_file() and path.stat().st_size > 0


def has_phase_file(paths: RunPaths,
                   number: int) -> bool:
    return _is_written(build_phase_file_path(paths, number))


def read_phase_file(paths: RunPaths,
                    number: int) -> Any:
    path = build_phase_file_path(paths, number)
    require_phase_output(path, number)

    return json_store_utils.read_json(path)


def read_transcript_language(paths: RunPaths) -> str:
    """The language code phase 1 detected for the recording's speech, `de`; empty when there is no transcript yet."""

    number = output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE]
    if not has_phase_file(paths, number):
        return ""

    return str(read_phase_file(paths, number).get("language") or "")


def read_transcript_text(paths: RunPaths) -> str:
    """Everything phase 1 heard or read, as one text (a PDF's page text); empty when there is no transcript yet."""

    number = output_contract.PHASE_NUMBERS[output_contract.TRANSCRIBE_PHASE]
    if not has_phase_file(paths, number):
        return ""

    return str(read_phase_file(paths, number).get("text") or "")


def is_audio_run(paths: RunPaths) -> bool:
    """Whether phase 2 wrote a frames file with no frame: an audio recording's (`"medium": "audio"`), or a PDF read as its
    text only (`"pdf-text-only"`, `"pdf-markdown"` before), which takes the audio workflow's route."""

    frames_number = output_contract.PHASE_NUMBERS[output_contract.FRAMES_PREPARING_PHASE]

    return (has_phase_file(paths, frames_number)
            and read_phase_file(paths, frames_number).get("medium") in output_contract.FRAMELESS_MEDIUMS)


def describe_frameless_run(paths: RunPaths) -> str:
    """What a run with no frame is, in a console line's words: "a PDF read as its text only" or "an audio recording"."""

    return "a PDF read as its text only" if is_pdf_run(paths) else "an audio recording"


def is_pdf_run(paths: RunPaths) -> bool:
    """Whether the run reads a PDF: its pages as the frames (`"medium": "pdf"`), or its text only
    (`"pdf-text-only"`, `"pdf-markdown"` before)."""

    frames_number = output_contract.PHASE_NUMBERS[output_contract.FRAMES_PREPARING_PHASE]

    return (has_phase_file(paths, frames_number)
            and read_phase_file(paths, frames_number).get("medium") in (output_contract.PDF_MEDIUM,
                                                                        *output_contract.PDF_TEXT_ONLY_MEDIUMS))


def has_run_frames(paths: RunPaths) -> bool:
    """Whether the run took any frame: an audio recording's has none, so phases 4 and 5 offer it no frame tool."""

    frames_number = output_contract.PHASE_NUMBERS[output_contract.FRAMES_PREPARING_PHASE]

    return has_phase_file(paths, frames_number) and bool(read_phase_file(paths, frames_number).get("items"))


def write_phase_file(paths: RunPaths,
                     number: int,
                     payload: Any) -> Path:
    phase_file = build_phase_file_path(paths, number)
    json_store_utils.write_json(phase_file, payload)

    return phase_file


def require_phase_output(path: Path,
                         number: int) -> None:
    """Refuse by name a file phase `number` writes and that is not there, in the words every missing input is
    refused in."""

    if _is_written(path):
        return
    phase_name = (output_contract.PHASE_FILES.get(number) or {}).get("phase", "")
    named = f" ({phase_name})" if phase_name else ""
    raise MissingPhaseInput(f"{display_path_utils.render_path(path)} is missing or empty. It is written by phase {number}{named}: "
                            f"run that phase first, with `--phase {number}` or `--to {number}`.")
