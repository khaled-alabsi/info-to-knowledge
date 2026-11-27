"""What a run folder itself says about how far it got, for a `--resume` that has no run record to read.

A run's progress normally lives in `logs/<run id>/run.json`, and `--resume` continues the newest one. Two ordinary
things leave a folder without that record: `reset-to N`, whose whole purpose is to run the phases after N again, and a
run whose log directory was moved or removed while its phase files stayed. Refusing there sends the operator to
`--from N`, which they have to work out themselves, and which re-runs phase 1 when they get it wrong - over a
transcript that cost the most of any phase.

So the phases are read off the files instead. A phase writes its numbered file only once its output passed its own
validator, so "the file is there" is the same claim the store's `complete` makes, and the phases already read their
input from those files. Only the LEADING run of finished phases is adopted: a later phase's file beside a missing
earlier one is a folder nobody can explain, and guessing there would skip a phase that never ran.
"""

from __future__ import annotations

from models import output_contract
from models.run_paths import RunPaths
from services import frame_records_service
from services import phase_file_service

__all__ = ["list_finished_phase_numbers", "describe_finished_phases", "list_written_slugs"]


def _is_phase_finished(paths: RunPaths,
                       number: int) -> bool:
    """Whether phase `number` left its output in this folder. Phase 3 completion is decided from every frame's notes,
    not only from `03_notes.md`, so a partial phase cannot look complete because its merged file exists."""

    if number == output_contract.PHASE_NUMBERS["frames_processing"]:
        return frame_records_service.has_every_frame_record(paths)


    return phase_file_service.has_phase_file(paths, number)


def list_finished_phase_numbers(paths: RunPaths) -> list[int]:
    """The phases this folder already holds the output of, from phase 1 up to the first one it does not."""

    finished = []
    for number in sorted(output_contract.PHASE_FILES):
        if not _is_phase_finished(paths, number):
            break
        finished.append(number)

    return finished


def describe_finished_phases(paths: RunPaths) -> dict[str, str]:
    """`{phase name: "complete"}` for those phases, in the vocabulary the run store and every phase node use."""

    return {output_contract.PHASE_FILES[number]["phase"]: "complete"
            for number in list_finished_phase_numbers(paths)}


def list_written_slugs(paths: RunPaths) -> list[str]:
    """The frames whose notes are already in the frames file, so a run that seeds its items from that file does not
    ask the model for them again."""

    return [record["slug"] for record in frame_records_service.read_frame_records(paths)]
