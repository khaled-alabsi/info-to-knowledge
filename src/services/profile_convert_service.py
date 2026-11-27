"""A finished run converted to another ingesting profile, as a NEW run folder beside it:
converting is always possible and never replaces the document a run has.

The new folder `<run>-<profile>` gets what phases 1 to 3 wrote (their numbered files, `03_processed-frames/`, the
frames) and the run's tags, by hard link where the disk allows, and its profile, `chosenBy: convert`. Phase 4 then
runs there from the same notes (`--from 4`), so the new document rests on the same entries, times and frames. An audio
recording's phase 3 prepares its notes as its profile declares: when
the two profiles declare other steps, only phases 1 and 2 are copied and phase 3 runs again there (`--from 3`). The run
converted from is only read.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from models import output_contract
from models import phase_guide
from models.run_paths import RunPaths
from services import ingesting_profile_service
from services import phase_file_service
from services import preprocessing_step_service
from services import speech_file_service

__all__ = ["build_target_folder", "copy_run_for_profile", "describe_job", "select_first_phase"]

# What phases 1 to 3 wrote, by the start of its name, and what else a run carries into phase 4
_PHASE_PREFIXES = ("01_", "02_", "03_")
_PHASE_3_PREFIX = _PHASE_PREFIXES[2]
_CARRIED = (output_contract.FRAME_IMAGES_DIR_NAME, output_contract.RUN_TAGS_FILE, output_contract.RUN_MEDIUM_FILE)


def _copy_by_link(source: str,
                  target: str) -> None:
    """A hard link, which costs no space; a copy where the disk refuses one (another volume)."""

    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def select_first_phase(run_folder: Path,
                       profile: str) -> int:
    """The phase the converted run starts at: 3 for an audio recording that the two profiles prepare in other steps,
    else 4."""

    speech = speech_file_service.read_speech_of(run_folder)
    if speech is None:
        return 4
    count = len(speech["paragraphs"])
    source = preprocessing_step_service.select_due_steps(ingesting_profile_service.resolve_profile(run_folder), count)
    target = preprocessing_step_service.select_due_steps(profile, count)

    return 3 if source != target else 4


def describe_job(run_folder: Path,
                 profile: str,
                 first_phase: int) -> str:
    """What the conversion's queued job runs, as the queue shows it: the profile, and each phase by its number and
    title. An audio recording ends at phase 4, its phases 5 and 6 being off, so the label names the phases that run."""

    # An audio run's frames file says so; a takeaway run writes no speech file, so the file alone would call it a video
    paths = RunPaths(repository=run_folder, module=run_folder.name, output_root_override=run_folder)
    is_audio = (run_folder / output_contract.SPEECH_FILE).is_file() or phase_file_service.is_audio_run(paths)
    last_phase = (min(output_contract.AUDIO_OFF_PHASES) - 1 if is_audio
                  else output_contract.PHASE_NUMBERS[output_contract.CRITIQUE_COLLECTION_PHASE])
    titles = {**{number: name.replace("_", " ") for name, number in output_contract.PHASE_NUMBERS.items()},
              **phase_guide.PHASE_TITLES, **(phase_guide.AUDIO_PHASE_TITLES if is_audio else {})}
    span = f"phase {first_phase} only" if first_phase == last_phase else f"phases {first_phase} to {last_phase}"
    names = ", ".join(titles[number] for number in range(first_phase, last_phase + 1))

    return f"convert to {profile} · {span} ({names})"


def build_target_folder(run_folder: Path,
                        profile: str) -> Path:
    return run_folder.parent / f"{run_folder.name}-{profile}"


def copy_run_for_profile(run_folder: Path,
                         profile: str) -> Path:
    """The new run folder for `profile`, holding phases 1 to 3 (1 and 2 when phase 3 runs again there,
    `select_first_phase`) and the tags of `run_folder`; refused by name when the profile is not one of the list or the
    folder exists already."""

    # 1. a known profile and a folder never written over
    target = build_target_folder(run_folder, profile)
    if profile not in ingesting_profile_service.list_profile_names():
        raise ValueError(f"no ingesting profile {profile!r}: the profiles are "
                         + ", ".join(ingesting_profile_service.list_profile_names()))
    if target.exists():
        raise ValueError(f"{target.name} exists already: it holds this run converted to {profile}")

    # 2. phases 1 to 3 and what they carry, linked or copied; phase 3 left out when it runs again
    is_phase_3_kept = select_first_phase(run_folder, profile) == 4
    target.mkdir()
    try:
        for entry in sorted(run_folder.iterdir()):
            if not (entry.name.startswith(_PHASE_PREFIXES) or entry.name in _CARRIED):
                continue
            if entry.name.startswith(_PHASE_3_PREFIX) and not is_phase_3_kept:
                continue
            if entry.is_dir():
                shutil.copytree(entry, target / entry.name, copy_function=_copy_by_link)
            else:
                _copy_by_link(str(entry), str(target / entry.name))

        # 3. the profile the new document is written in
        ingesting_profile_service.write_profile(target, profile, "convert")
    except BaseException:
        # The folder was made here and holds nothing else; left half filled, the next try was refused as converted
        shutil.rmtree(target, ignore_errors=True)
        raise

    return target
