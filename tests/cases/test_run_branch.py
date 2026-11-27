"""A run branched from a phase: a new run folder holding the run's progress before that phase,
the run branched from untouched."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from support import harness

from cli import branch_command
from models import output_contract
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import run_branch_service
from services import run_operations_service
from services import run_status_service
from services import run_store_service
from services import web_api_service

_RUN_ID = "2026-10-01_09-00"


def _make_run(root: Path) -> Path:
    """A run folder with phases 1 to 4 recorded complete and their files on disk: a frame with its notes in
    `02_frames.json`, phase 3's notes, phase 4's draft and document, and a log."""

    run = root / "output" / "Jonas-weekly"
    paths = RunPaths(repository=root / "inbox", module="Jonas-weekly.mp4", output_root_override=run)
    store = run_store_service.RunStore.create(paths.run_state(_RUN_ID), RunIdentity.for_run(_RUN_ID, paths),
                                              budgets={}, models=[])
    for phase in ("transcribe", "frames_preparing", "frames_processing", "finalize"):
        store.set_phase(phase, "complete")
    store.data["items"] = {"frame-000": {"status": "written"}}
    store.save()
    (run / "01_transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    (run / output_contract.LOOP_ITEMS_FILE).write_text(json.dumps({"schemaVersion": 1, "items": [
        {"slug": "frame-000", "path": "frames/frame-000.jpg", "record": {"facts": ["the gateway signs the token"]}}]}),
        encoding="utf-8")
    for name in ("frames/frame-000.jpg", "03_processed-frames/frame-000.md", "03_notes.md", "04_document.md",
                 "04_writing/draft.md", f"logs/{_RUN_ID}/09-00.log"):
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        (run / name).write_text(f"<{name}>", encoding="utf-8")

    return run


def _hash_files(folder: Path) -> dict[str, str]:
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def _read_phases(run: Path) -> dict:
    return json.loads((run / "logs" / _RUN_ID / output_contract.RUN_STATE_FILE).read_text(encoding="utf-8"))["phases"]


def test_a_branch_keeps_the_phases_before_it_and_leaves_the_run_as_it_was():
    run = _make_run(Path(tempfile.mkdtemp()))
    before = _hash_files(run)
    branch = run_branch_service.create_branch(run, 3)
    files = set(_hash_files(branch))
    harness.check("the branch sits beside the run, named for the phase", branch == run.parent / "Jonas-weekly-from-3",
                  str(branch))
    harness.check("phases 1 and 2 are kept, phase 3 and 4's files are gone from the branch",
                  {"01_transcript.json", "02_frames.json", "frames/frame-000.jpg"} <= files
                  and not files & {"03_notes.md", "03_processed-frames/frame-000.md", "04_document.md", "04_writing/draft.md"},
                  str(sorted(files)))
    harness.check("the branch's run record forgets phases 3 and 4",
                  {name: status for name, status in _read_phases(branch).items()}
                  == {"transcribe": "complete", "frames_preparing": "complete"}, str(_read_phases(branch)))
    branched_frames = json.loads((branch / output_contract.LOOP_ITEMS_FILE).read_text(encoding="utf-8"))["items"][0]
    harness.check("the branch's frames lose phase 3's notes", "record" not in branched_frames, str(branched_frames))
    harness.check("the run branched from is byte for byte as it was, its notes and its phases included",
                  _hash_files(run) == before and _read_phases(run).get("finalize") == "complete")
    harness.check("a frame is linked, never copied; the frames file is a copy of its own",
                  (run / "frames/frame-000.jpg").stat().st_ino == (branch / "frames/frame-000.jpg").stat().st_ino
                  and (run / "02_frames.json").stat().st_ino != (branch / "02_frames.json").stat().st_ino)
    with (branch / f"logs/{_RUN_ID}/09-00.log").open("a", encoding="utf-8") as log:
        log.write("the branch's resume writes on\n")
    harness.check("a log the branch's run writes on in place leaves the run's own log as it was",
                  (run / f"logs/{_RUN_ID}/09-00.log").read_text(encoding="utf-8") == f"<logs/{_RUN_ID}/09-00.log>")
    harness.check("branch.json names the run and the phase",
                  {key: value for key, value in run_branch_service.read_branch(branch).items() if key != "at"}
                  == {"from": "Jonas-weekly", "phase": 3}, str(run_branch_service.read_branch(branch)))
    harness.check("the next branch from the same phase takes the next free name",
                  run_branch_service.create_branch(run, 3) == run.parent / "Jonas-weekly-from-3-2")


def test_a_phase_out_of_range_or_not_reached_is_refused_by_name():
    run = _make_run(Path(tempfile.mkdtemp()))
    too_early = run_operations_service.create_branch(run, 0)
    too_late = run_operations_service.create_branch(run, 6)
    missing = run_operations_service.create_branch(run.parent / "no-such-run", 3)
    done = run_operations_service.create_branch(run, 5)
    harness.check("phase 0 is refused, naming the phases a branch may start at",
                  too_early.exit_code == 2 and "give 1 to 7" in too_early.lines[0], str(too_early.lines))
    harness.check("a phase past the one after the last the run reached is refused",
                  too_late.exit_code == 2 and "reached phase 4" in too_late.lines[0], str(too_late.lines))
    harness.check("a folder that is not there is refused", missing.exit_code == 2, str(missing.lines))
    harness.check("phase 5, right after the last reached, branches and names the new folder and its run line",
                  done.exit_code == 0 and done.records[0]["name"] == "Jonas-weekly-from-5"
                  and "--resume" in done.lines[1], str(done.lines))


def test_the_page_branches_a_run_through_its_route():
    run = _make_run(Path(tempfile.mkdtemp()))
    previous = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": run.parent})
    try:
        answer = TestClient(web_api_service.build_app()).post("/api/runs/output/Jonas-weekly/branch", json={"phase": 4}).json()
        branched = run_status_service.describe_run_folder("output", "Jonas-weekly-from-4")["branch"]
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous)
    harness.check("the route branches the run and names the new folder for the page to open",
                  answer["exit_code"] == 0 and answer["records"][0]["name"] == "Jonas-weekly-from-4"
                  and (run.parent / "Jonas-weekly-from-4" / "03_notes.md").is_file(), str(answer))
    harness.check("the branch's detail names the run and phase it came from",
                  (branched or {}).get("from") == "Jonas-weekly" and (branched or {}).get("phase") == 4, str(branched))


def test_the_branch_command_makes_the_branch_and_exits_0():
    run = _make_run(Path(tempfile.mkdtemp()))
    code = branch_command.cmd_branch(argparse.Namespace(output_root=run, phase=4, logger=logging.getLogger("test")))
    refused = branch_command.cmd_branch(argparse.Namespace(output_root=run, phase=9, logger=logging.getLogger("test")))
    harness.check("the command exits 0 with the branch made, 2 for a phase out of range",
                  code == 0 and (run.parent / "Jonas-weekly-from-4").is_dir() and refused == 2, f"{code} {refused}")
