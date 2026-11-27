"""`--resume` on a run folder whose `run.json` is gone: the phases the folder holds are read off its files.

`reset-to N` forgets the progress of every phase after N, and a run folder can outlive its logs. Before this, both
left a folder `--resume` refused, and the operator had to work out `--from N` themselves - getting it wrong re-runs
phase 1 over a transcript that is already there. The phases are now adopted from the files each one writes, only as
far as the first phase whose file is missing, and the frames that already hold notes are adopted with them.
"""

import argparse
import logging
from pathlib import Path

from support import fixtures as fx
from support import harness

import config as app_config
from cli import run_command
from models import output_contract
from models.run_errors import BadTarget
from services import phase_file_service
from services import run_resume_service

ITEMS = [{"slug": "frame-000", "index": 0, "timestamp": 0.0, "path": "frames/frame-000.jpg", "ignored": False},
         {"slug": "frame-001", "index": 1, "timestamp": 6.0, "path": "frames/frame-001.jpg", "ignored": False}]
RECORD = {"frame": 0, "screen_kind": "slide", "facts": ["A refund can be split into at most three parts."],
          "diagram": "", "screen": '- slide: white, the whole frame\n  - text "Refunds": centre', "speech_visual_alignment": "", "builds_on": [],
          "contradicts_earlier": [], "open_questions": [],
          "urls": [], "action_items": [], "questions_asked": [], "screenshots": [], "tags": ["refund policy", "reference"],
          "third_person_description": "The presenter opens the refund slide and reads out the limit.",
          "tags": ["refund policy", "reference"], "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}


def _write_transcript(paths) -> None:
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"],
                                       {"schemaVersion": 1, "hasAudio": True, "segments": [], "words": []})


def _write_frames(paths,
                  noted_slugs: tuple[str, ...] = ()) -> None:
    items = [{**item, **({"record": {**RECORD, "frame": item["index"]}, "builtFrom": []}
                         if item["slug"] in noted_slugs else {})} for item in ITEMS]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"],
                                       {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION, "items": items})


def test_the_finished_phases_are_read_off_the_files_the_folder_holds():
    paths = fx.make_paths()
    harness.check("an empty folder has finished nothing", run_resume_service.list_finished_phase_numbers(paths) == [])
    _write_transcript(paths)
    harness.check("a transcript alone is phase 1", run_resume_service.list_finished_phase_numbers(paths) == [1])
    _write_frames(paths)
    harness.check("the frames file adds phase 2", run_resume_service.list_finished_phase_numbers(paths) == [1, 2])
    harness.check("phase 3 is not finished while a frame has no notes",
                  output_contract.PHASE_NUMBERS["frames_processing"] not in run_resume_service.list_finished_phase_numbers(paths))
    _write_frames(paths, noted_slugs=tuple(item["slug"] for item in ITEMS))
    harness.check("every frame noted is phase 3 as well",
                  run_resume_service.list_finished_phase_numbers(paths) == [1, 2, 3])
    harness.check("they are named in the store's own vocabulary",
                  run_resume_service.describe_finished_phases(paths)
                  == {"transcribe": "complete", "frames_preparing": "complete", "frames_processing": "complete"})


def test_a_later_phase_file_beside_a_missing_earlier_one_is_never_adopted():
    paths = fx.make_paths()
    _write_frames(paths)                      # phase 2's file, with no transcript before it
    harness.check("nothing is adopted while phase 1's file is missing",
                  run_resume_service.list_finished_phase_numbers(paths) == [])


def test_the_frames_already_noted_are_listed_so_they_are_not_asked_again():
    paths = fx.make_paths()
    _write_frames(paths, noted_slugs=("frame-000",))
    harness.check("only the noted frame is listed", run_resume_service.list_written_slugs(paths) == ["frame-000"])


def test_a_resume_with_no_run_record_mints_a_run_that_has_the_folder_s_phases_complete():
    paths = fx.make_paths()
    _write_transcript(paths)
    _write_frames(paths)
    store = run_command._open_run(paths, True, False, logging.getLogger("test-resume"))
    harness.check("phases 1 and 2 are recorded complete, so their nodes read them from disk",
                  store.data["phases"] == {"transcribe": "complete", "frames_preparing": "complete"}, str(store.data["phases"]))
    harness.check("the run says where that came from", store.data.get("resumedFrom"))
    harness.check("and it is on disk, as the record the next resume reads", store.path.is_file())


def test_a_resume_over_a_folder_with_no_finished_phase_is_still_refused_by_name():
    paths = fx.make_paths()
    try:
        run_command._open_run(paths, True, False, logging.getLogger("test-resume"))
    except BadTarget as exc:
        harness.check("the refusal names the folder it looked in", str(paths.output_root) in str(exc), str(exc))
    else:
        harness.check("a resume with nothing to resume is refused", False)


def test_a_resume_of_a_run_begun_at_a_later_phase_takes_the_earlier_phases_from_the_folder():
    paths = fx.make_paths()
    _write_transcript(paths)
    _write_frames(paths, noted_slugs=tuple(item["slug"] for item in ITEMS))
    started = run_command._open_run(paths, False, False, logging.getLogger("test-resume"))
    started.data["phases"] = {"finalize": "blocked"}          # a run started with `--from 4`, which stopped in phase 4
    started.save()
    store = run_command._open_run(paths, True, False, logging.getLogger("test-resume"))
    harness.check("the same run is continued", store.path == started.path, str(store.path))
    harness.check("phases 1 to 3 are complete from their files, and phase 4 keeps what the run recorded",
                  store.data["phases"] == {"transcribe": "complete", "frames_preparing": "complete", "frames_processing": "complete",
                                           "finalize": "blocked"}, str(store.data["phases"]))


def test_a_resume_runs_on_the_budgets_the_settings_hold_now():
    """The owner: a run stopped and run again takes the new settings, its attempts included."""

    paths = fx.make_paths()
    _write_transcript(paths)
    started = run_command._open_run(paths, False, False, logging.getLogger("test-resume"))
    previous = app_config.settings.budgets.max_attempts
    app_config.settings.budgets.max_attempts = previous + 4
    try:
        store = run_command._open_run(paths, True, False, logging.getLogger("test-resume"))
    finally:
        app_config.settings.budgets.max_attempts = previous
    harness.check("the same run is continued, with the attempts the settings hold now",
                  store.path == started.path and store.read_budget("maxAttempts") == previous + 4,
                  str(store.data.get("budgets")))
