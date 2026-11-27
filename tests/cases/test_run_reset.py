"""`i2k reset-to N`: every phase after N removed from a run folder, its files and its progress, as if it never ran
(the owner, 2026-09-28). The phases up to N and the logs stay."""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from support import harness

from cli import reset_command
from models import output_contract
from models.phase_outputs_spec import PhaseOutputsSpec
from models.run_paths import RunPaths
from services import frame_records_service
from services import run_reset_service
from services import worker_feedback_service
from utils import json_store_utils

PUBLISHED = "a-meeting-someone-2026-03-03"


def test_the_meeting_notes_name_and_reset_owner_are_phase_3():
    harness.check("the merged meeting notes carry phase 3's prefix",
                  output_contract.NOTES_FILE == "03_notes.md", output_contract.NOTES_FILE)
    harness.check("phase 3 owns the merged meeting notes",
                  output_contract.NOTES_FILE in run_reset_service.PHASE_OUTPUTS[3].files
                  and output_contract.NOTES_FILE not in run_reset_service.PHASE_OUTPUTS[4].files,
                  str(run_reset_service.PHASE_OUTPUTS))


def _write(path: Path,
           text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_run() -> RunPaths:
    """A run folder in which every phase has written its files, and a run store recording all of them."""

    root = Path(tempfile.mkdtemp()) / "run"
    for name in ("audio.wav", "01_transcript.json", "frames/frame-000.jpg", "03_processed-frames/frame-000.json",
                 "03_batch_summaries.json", "03_notes.md", "04_document.md", "04_document.provenance.json",
                 "04_writing/parts.json", "04_writing/images/looks/a.png",
                 "05_review/review.md",
                 "logs/2026-01-01_00-00/starts/00-00.log", "calls/frames-000-003.json", "calls/frame-004.json",
                 "calls/parts.json", "calls/table-of-contents.json", "calls/review-todos.json"):
        _write(root / name)
    _write(root / "05_review/before.md", "# The document as phase 4 published it\n")
    _write(root / PUBLISHED / f"{PUBLISHED}.md", "# The document as phase 5 edited it\n")
    _write(root / PUBLISHED / "review.md", "the review")
    _write(root / PUBLISHED / "provenance.json", json.dumps({"document": f"{PUBLISHED}.md", "review": {"todos": 3}}))
    _write(root / "02_frames.json", json.dumps({"schemaVersion": 1, "items": [
        {"slug": "frame-000", "index": 0, "timestamp": 1.0, "path": "frames/frame-000.jpg",
         "record": {"frame": 0, "facts": ["A fact."]}, "builtFrom": []}]}))
    _write(root / "logs/20260101-000000/run.json", json.dumps({
        "phases": {"transcribe": "complete", "frames_preparing": "complete", "finalize": "blocked",
                   "review": "complete"},
        "items": {"frame-000": {"slug": "frame-000", "status": "written", "attempts": 2}},
        "attempts": {"frames_processing/frame-000": [{}], "finalize/table-of-contents": [{}], "review": [{}]}}))

    return RunPaths(repository=root, module="run", output_root_override=root)


def _read_store(paths: RunPaths) -> dict:
    return json.loads((paths.output_root / "logs/20260101-000000/run.json").read_text(encoding="utf-8"))


def test_a_reset_to_3_removes_phases_4_and_5_and_keeps_the_frame_notes():
    paths = _build_run()
    root = paths.output_root
    run_reset_service.reset_to_phase(paths, 3)
    gone = [name for name in ("04_document.md", "04_document.provenance.json", "04_writing",
                              "05_review", PUBLISHED,
                              "calls/parts.json", "calls/table-of-contents.json", "calls/review-todos.json")
            if (root / name).exists()]
    harness.check("every file of phases 4 and 5 is gone", not gone, str(gone))
    kept = [name for name in ("audio.wav", "01_transcript.json", "02_frames.json", "frames/frame-000.jpg",
                              "03_processed-frames/frame-000.json", "03_batch_summaries.json", "03_notes.md",
                              "calls/frames-000-003.json",
                              "calls/frame-004.json",
                              "logs/2026-01-01_00-00/starts/00-00.log") if not (root / name).exists()]
    harness.check("phases 1 to 3 and the logs stay", not kept, str(kept))
    harness.check("the frame notes stay", frame_records_service.read_frame_records(paths)[0]["facts"] == ["A fact."])
    store = _read_store(paths)
    harness.check("the store forgets phases 4 and 5, their attempts, and keeps phase 3's",
                  set(store["phases"]) == {"transcribe", "frames_preparing"} and set(store["attempts"]) == {"frames_processing/frame-000"}
                  and store["items"]["frame-000"]["status"] == "written", json.dumps(store))


def test_a_reset_to_2_removes_the_frame_notes_and_the_frames_statuses():
    paths = _build_run()
    run_reset_service.reset_to_phase(paths, 2)
    harness.check("the frames stay, without their notes",
                  (paths.output_root / "02_frames.json").is_file() and frame_records_service.read_frame_records(paths) == [])
    harness.check("phase 3's own files and call logs are gone", not any((paths.output_root / name).exists() for name in (
        "03_processed-frames", "03_batch_summaries.json", "03_notes.md",
        "calls/frames-000-003.json", "calls/frame-004.json")))
    item = _read_store(paths)["items"]["frame-000"]
    harness.check("every frame is pending again, with no attempts", item["status"] == "pending" and item["attempts"] == 0,
                  str(item))


def test_a_reset_to_4_puts_back_the_document_phase_4_published():
    paths = _build_run()
    folder = paths.output_root / PUBLISHED
    run_reset_service.reset_to_phase(paths, output_contract.PHASE_NUMBERS["finalize"])
    harness.check("the published document is phase 4's again, its review report gone, its folder kept",
                  (folder / f"{PUBLISHED}.md").read_text(encoding="utf-8") == "# The document as phase 4 published it\n"
                  and not (folder / "review.md").exists() and (paths.output_root / "04_writing").is_dir())
    harness.check("the provenance no longer names a review",
                  "review" not in json.loads((folder / "provenance.json").read_text(encoding="utf-8")))
    harness.check("phase 5's folder and call log are gone", not (paths.output_root / "05_review").exists()
                  and not (paths.output_root / "calls/review-todos.json").exists())


def test_a_reset_to_0_leaves_only_the_logs():
    paths = _build_run()
    run_reset_service.reset_to_phase(paths, 0)
    left = sorted(path.relative_to(paths.output_root).parts[0] for path in paths.output_root.iterdir())
    harness.check("nothing any phase wrote is left", set(left) <= {"logs", "calls"}, str(left))
    harness.check("the store holds no phase and no frame", _read_store(paths)["phases"] == {}
                  and _read_store(paths)["items"] == {})


def test_the_command_lists_first_and_removes_only_with_yes():
    paths = _build_run()
    logger = logging.getLogger("test-reset")
    logger.setLevel(logging.INFO)
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    logger.addHandler(handler)
    try:
        args = argparse.Namespace(phase=4, output_root=paths.output_root, is_confirmed=False, logger=logger)
        harness.check("a listing exits 0", reset_command.cmd_reset_to(args) == 0)
        harness.check("it names what it would remove and removes nothing", "05_review/" in records[-1]
                      and "nothing was removed" in records[-1] and (paths.output_root / "04_writing").is_dir(), records[-1])
        harness.check("with --yes it removes them", reset_command.cmd_reset_to(argparse.Namespace(
            phase=4, output_root=paths.output_root, is_confirmed=True, logger=logger)) == 0
                      and not (paths.output_root / "05_review").exists())
        harness.check("a phase that does not exist is refused", reset_command.cmd_reset_to(argparse.Namespace(
            phase=9, output_root=paths.output_root, is_confirmed=True, logger=logger)) == 2)
    finally:
        logger.removeHandler(handler)
    harness.check("every numbered phase has its outputs listed", set(run_reset_service.PHASE_OUTPUTS)
                  == set(output_contract.PHASE_FILES)
                  and all(isinstance(outputs, PhaseOutputsSpec) for outputs in run_reset_service.PHASE_OUTPUTS.values()),
                  str(sorted(run_reset_service.PHASE_OUTPUTS)))


def _start_fake_run(output: Path) -> subprocess.Popen:
    """A process whose command line reads like a run on `output`: `ps` shows it, nothing is written.

    It waits on its own stdin, never on a clock: a test that sleeps pays that time on every machine forever, and it
    still races on a slow one (see the ban in `~/.agents/skills/_usecases/testing.md`). Closing the pipe or killing
    it ends it at once."""

    return subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()", "main.py", "run", "--output",
                             str(output), "run"], stdin=subprocess.PIPE)


def test_only_a_live_run_on_this_folder_blocks_a_reset():
    """A folder named "run" once matched every `main.py run` process, the reset command itself included."""

    base = Path(tempfile.mkdtemp())
    output_root = base / "run"
    other = _start_fake_run(base / "another-folder")
    try:
        harness.check("a live run on another folder does not block this one, whatever the folder names share",
                      run_reset_service.list_live_runs(output_root) == [], str(run_reset_service.list_live_runs(output_root)))
        shell = subprocess.Popen(["/bin/sh", "-c", f"read x; echo main.py run --output {output_root} --resume"],
                                 stdin=subprocess.PIPE)
        try:
            harness.check("a shell whose command only holds the words is no run (2026-09-30: the check listed the "
                          "operator's own shell, and a reset was refused)",
                          not any(line.split()[0] == str(shell.pid) for line in run_reset_service.list_live_runs(output_root)),
                          str(run_reset_service.list_live_runs(output_root)))
        finally:
            shell.kill()
            shell.wait()
        same = _start_fake_run(output_root)
        try:
            harness.check("a live run on this folder is listed",
                          any(line.split()[0] == str(same.pid) for line in run_reset_service.list_live_runs(output_root)))
        finally:
            same.kill()
            same.wait()
    finally:
        other.kill()
        other.wait()


def _start_fake_inbox() -> subprocess.Popen:
    """A process whose command line reads like `main.py inbox`: it names no run folder, as the real one does not."""

    return subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()", "main.py", "inbox"],
                            stdin=subprocess.PIPE)


def test_a_run_whose_path_holds_a_space_and_an_inbox_run_are_both_live():
    """The page showed no phase running: every meeting recording's folder name holds a space, which the `--output`
    match missed, and an `inbox` run names no folder on its command line at all."""

    base = Path(tempfile.mkdtemp())
    output_root = base / "a meeting of two words"
    (output_root / "logs" / "2026-01-01_00-00").mkdir(parents=True)
    spaced = _start_fake_run(output_root)
    try:
        harness.check("a live run on a folder whose name holds a space is listed",
                      any(line.split()[0] == str(spaced.pid) for line in run_reset_service.list_live_runs(output_root)),
                      str(run_reset_service.list_live_runs(output_root)))
    finally:
        spaced.kill()
        spaced.wait()
    inbox = _start_fake_inbox()
    try:
        harness.check("an inbox run is no live run of a folder its record does not name",
                      run_reset_service.list_live_runs(output_root) == [],
                      str(run_reset_service.list_live_runs(output_root)))
        json_store_utils.write_json(output_root / "logs" / "2026-01-01_00-00" / "run.json",
                                    {"runId": "2026-01-01_00-00", "pid": inbox.pid})
        harness.check("the process the folder's record names is listed, whatever its command line says",
                      any(line.split()[0] == str(inbox.pid) for line in run_reset_service.list_live_runs(output_root)),
                      str(run_reset_service.list_live_runs(output_root)))
    finally:
        inbox.kill()
        inbox.wait()


def test_a_reset_to_2_forgets_each_frame_s_last_refusal():
    paths = _build_run()
    store_path = paths.output_root / "logs/20260101-000000/run.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    data["items"]["frame-000"]["lastRefusal"] = "FRAME_FACTS_EMPTY: no fact"
    store_path.write_text(json.dumps(data), encoding="utf-8")
    run_reset_service.reset_to_phase(paths, 2)
    harness.check("no refusal of a removed attempt is kept", "lastRefusal" not in _read_store(paths)["items"]["frame-000"],
                  str(_read_store(paths)["items"]["frame-000"]))


def test_a_critique_collection_call_log_is_listed_once_under_phase_6():
    paths = _build_run()
    _write(paths.output_root / f"calls/{output_contract.CRITIQUE_COLLECTION_SLUG}.json")
    targets = run_reset_service.list_reset_targets(paths, 3)
    harness.check("listed once",
                  sum(1 for line in targets if f"{output_contract.CRITIQUE_COLLECTION_SLUG}.json" in line) == 1,
                  str(targets))


def test_a_run_made_before_the_rename_has_its_phase_6_taken_back_defect():
    # Phase 6 was renamed from "lessons" to "critique collection"; a run made before keeps 06_lessons/ and calls/lessons.json.
    # The reset takes both back under the retired names, so a reset to phase 5 leaves no old rule set behind
    paths = _build_run()
    _write(paths.output_root / f"calls/{output_contract.RETIRED_CRITIQUE_COLLECTION_SLUG}.json")
    _write(paths.output_root / output_contract.RETIRED_CRITIQUE_COLLECTION_DIR_NAME
           / output_contract.RETIRED_CRITIQUE_COLLECTION_FILE)
    targets = run_reset_service.list_reset_targets(paths, 5)
    harness.check("the old call log and folder are both listed",
                  any(f"{output_contract.RETIRED_CRITIQUE_COLLECTION_SLUG}.json" in line for line in targets)
                  and any(output_contract.RETIRED_CRITIQUE_COLLECTION_DIR_NAME in line for line in targets), str(targets))


def test_the_recorded_pid_is_the_real_run_s_not_an_older_dry_run_s():
    import os

    paths = _build_run()
    (paths.output_root / "logs/20260101-000000/run.json").unlink()
    _write(paths.output_root / "logs/dry-2026-01-01_00-00/run.json", json.dumps({"pid": 999_999}))
    _write(paths.output_root / "logs/2026-01-02_00-00/run.json", json.dumps({"pid": os.getpid()}))
    harness.check("the newest real run's pid", run_reset_service._read_recorded_pid(paths.output_root) == os.getpid(),
                  str(run_reset_service._read_recorded_pid(paths.output_root)))


def test_a_run_of_one_root_does_not_name_a_folder_of_the_same_name_under_another():
    base = Path(tempfile.mkdtemp())
    (base / "output" / "Weekly sync").mkdir(parents=True)
    (base / "knowledge" / "Weekly sync").mkdir(parents=True)
    line = f"python main.py run --video x.mp4 --output {base / 'output' / 'Weekly sync'}"
    harness.check("the output folder is named, the knowledge folder of the same name is not",
                  run_reset_service._is_output_named(line, base / "output" / "Weekly sync")
                  and not run_reset_service._is_output_named(line, base / "knowledge" / "Weekly sync"))


def test_a_run_of_a_folder_does_not_name_a_folder_whose_name_starts_its_name():
    base = Path(tempfile.mkdtemp())
    (base / "Weekly").mkdir()
    (base / "Weekly sync").mkdir()
    line = f"python main.py run --output {base / 'Weekly sync'} --video x.mp4"
    harness.check("Weekly sync is named, Weekly is not",
                  run_reset_service._is_output_named(line, base / "Weekly sync")
                  and not run_reset_service._is_output_named(line, base / "Weekly"))


def test_a_live_run_with_a_long_run_line_is_matched_and_never_raises_defect():
    # A live run whose line held many flags after `--output` made every folder asked about raise "File name too long",
    # so the run list's rename and reset failed. Now the folder's words end at the next flag
    base = Path(tempfile.mkdtemp())
    (base / "Weekly sync").mkdir()
    flags = " ".join(f"--speed-setting framesProcessing.setting{number}=false" for number in range(12))
    line = f"123 python main.py run --video x.mp4 --output {base / 'Weekly sync'} --speed-preset balanced {flags} --resume"
    harness.check("the live run's folder is named", run_reset_service._is_output_named(line, base / "Weekly sync"), line)
    harness.check("another folder is not, and nothing raises",
                  not run_reset_service._is_output_named(line, base / "Weekly"), "")
    harness.check("and the live-run list reads it", run_reset_service.list_live_runs(base / "Weekly sync", [line]) == [line],
                  "")


def test_a_reset_forgets_every_field_phase_3_wrote_on_a_frame():
    paths = _build_run()
    store_path = paths.output_root / "logs/20260101-000000/run.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    data["items"]["frame-000"].update({"blockedReason": "a refusal", "blockedFindings": [{"code": "X"}],
                                       "answeredIn": "frames-1-2-repair", "additionsFrom": "frames-1-2-second-look"})
    store_path.write_text(json.dumps(data), encoding="utf-8")
    run_reset_service.reset_to_phase(paths, 2)
    item = _read_store(paths)["items"]["frame-000"]
    harness.check("none of phase 3's fields stays",
                  not {"blockedReason", "blockedFindings", "answeredIn", "additionsFrom"} & set(item), str(item))


def test_a_finished_run_reset_is_no_longer_complete():
    paths = _build_run()
    store_path = paths.output_root / "logs/20260101-000000/run.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    data.update({"status": "complete", "endedAt": "2026-01-01T01:00:00Z"})
    store_path.write_text(json.dumps(data), encoding="utf-8")
    run_reset_service.reset_to_phase(paths, 3)
    after = _read_store(paths)
    harness.check("the run reads as stopped short, with no end time",
                  after.get("status") == "incomplete" and "endedAt" not in after, str({key: after.get(key) for key in ("status", "endedAt")}))


def test_a_reset_forgets_a_later_phase_recorded_under_its_retired_name():
    paths = _build_run()
    store_path = paths.output_root / "logs/20260101-000000/run.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    data["phases"] = {"transcribe": "complete", "enumerate": "complete", "work": "complete"}
    data["attempts"] = {"work/frame-000": [{"attempt": 1}]}
    store_path.write_text(json.dumps(data), encoding="utf-8")
    run_reset_service.reset_to_phase(paths, 2)
    after = _read_store(paths)
    harness.check("the retired `work` and its attempts go, `enumerate` (phase 2) stays",
                  "work" not in after["phases"] and "enumerate" in after["phases"] and not after["attempts"],
                  str({key: after.get(key) for key in ("phases", "attempts")}))


def test_a_reset_promises_no_put_back_without_the_copy_phase_5_kept():
    paths = _build_run()
    (paths.output_root / "05_review/before.md").unlink()
    targets = run_reset_service.list_reset_targets(paths, 4)
    harness.check("no line says the published document is put back", not any("put back" in line for line in targets),
                  str(targets))


def test_an_audio_run_s_phase_2_and_3_files_belong_to_their_phases():
    paths = _build_run()
    root = paths.output_root
    for name in ("02_speech.md", "calls/speech-structuring.json", "calls/prepared-items-01.json",
                 "03_prepared/items.md"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x", encoding="utf-8")
    run_reset_service.reset_to_phase(paths, 3)
    harness.check("a reset to 3 keeps phase 2's speech and its call, and phase 3's prepared notes",
                  all((root / name).exists() for name in ("02_speech.md", "calls/speech-structuring.json",
                                                          "calls/prepared-items-01.json", "03_prepared/items.md")))
    run_reset_service.reset_to_phase(paths, 2)
    harness.check("a reset to 2 removes the prepared notes and their calls, and keeps the speech",
                  not (root / "03_prepared").exists() and not (root / "calls/prepared-items-01.json").exists()
                  and (root / "02_speech.md").exists())
    run_reset_service.reset_to_phase(paths, 1)
    harness.check("a reset to 1 removes the speech and its call",
                  not (root / "02_speech.md").exists() and not (root / "calls/speech-structuring.json").exists())


def test_a_reset_takes_back_the_reports_sent_from_the_phases_it_removes_defect():
    # A branch from phase 2 kept phase 3's worker report on a frame its new phase 2 no longer took, and the page showed it
    # as a problem of the new run. Now a reset removes every report whose step belongs to a phase it takes back.
    paths = _build_run()
    feedback = paths.output_root / output_contract.WORKER_FEEDBACK_FILE
    for where in ("frames-047-056", "chapter-02-text", "review-todo-01"):
        worker_feedback_service.record_report(feedback, "unclear_instruction", f"a problem at {where}", "", where)
    harness.check("the reset lists phase 4 and 5's reports", any("2 report(s)" in line
                  for line in run_reset_service.list_reset_targets(paths, 3)), str(run_reset_service.list_reset_targets(paths, 3)))
    run_reset_service.reset_to_phase(paths, 3)
    harness.check("a reset to 3 keeps phase 3's report only",
                  [report.where for report in worker_feedback_service.read_reports(feedback)] == ["frames-047-056"])
    run_reset_service.reset_to_phase(paths, 2)
    harness.check("a reset to 2 removes the file with its last report", not feedback.exists())


def test_a_reset_takes_back_the_timing_rows_of_the_phases_it_removes_defect():
    # Defect: a phase 3 redone after `reset-to 1` was timed from its first run a month before, and the batch table
    # listed the old batches beside the new. Fix: the reset writes a line, and the rows of later phases before it go
    from services import performance_log_service

    paths = _build_run()
    run_dir = paths.output_root / "logs/20260101-000000"
    for row in ({"level": "session", "session": "s1"}, {"level": "phase", "phase": "transcribe", "startedAtS": 1.0},
                {"level": "batch", "phase": "frames_processing", "slug": "frames-000-003", "startedAtS": 2.0}):
        (run_dir / performance_log_service.PERFORMANCE_FILE).open("a", encoding="utf-8").write(json.dumps(row) + "\n")
    run_reset_service.reset_to_phase(paths, 1)
    (run_dir / performance_log_service.PERFORMANCE_FILE).open("a", encoding="utf-8").write(json.dumps(
        {"level": "batch", "phase": "frames_processing", "slug": "frames-000-004", "startedAtS": 9.0}) + "\n")
    rows = performance_log_service.read_rows(run_dir)
    harness.check("the old batch is gone, the new batch and the earlier phase stay",
                  [row.get("slug") or row.get("phase") or row["level"] for row in rows]
                  == ["session", "transcribe", "frames-000-004"], str(rows))
