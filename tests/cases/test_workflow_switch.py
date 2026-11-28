"""A video run started again on another workflow than its phase 3 was made under (the owner: "between the fast and other
flow ... If it show for example warning message that it have to reset to phase two because phase one and phase two I
think they are the only shared between all flow"). Made run folders, no recording and no model."""

import json
import tempfile
from pathlib import Path

from support import harness

from models import output_contract
from models import video_workflow_vocabulary
from models.run_paths import RunPaths
from services import run_reset_service
from services import run_speed_service
from services import video_workflow_service
from services import workflow_switch_service

_KEPT_FILES = ("audio.wav", "01_transcript.json", "02_frames.json", "frames/frame-000.jpg")


def _write(path: Path,
           text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_run(phase_3_files: tuple[str, ...]) -> RunPaths:
    """A video run folder whose phases 1 and 2 are done and whose phase 3 wrote `phase_3_files`, with phase 4's
    folder and a run store recording phase 3's frames as written."""

    root = Path(tempfile.mkdtemp()) / "run"
    for name in (*_KEPT_FILES[:2], _KEPT_FILES[3], *phase_3_files, "04_writing/parts.json"):
        _write(root / name)
    _write(root / "02_frames.json", json.dumps({"schemaVersion": 1, "items": [
        {"slug": "frame-000", "index": 0, "timestamp": 1.0, "path": "frames/frame-000.jpg", "builtFrom": []}]}))
    _write(root / "logs/20260101-000000/run.json", json.dumps({
        "phases": {"transcribe": "complete", "frames_preparing": "complete", "frames_processing": "complete"},
        "items": {"frame-000": {"slug": "frame-000", "status": "written", "attempts": 1}},
        "attempts": {"frames_processing/frame-000": [{}]}}))

    return RunPaths(repository=root, module="run", output_root_override=root)


def _read_store(paths: RunPaths) -> dict:
    return json.loads((paths.output_root / "logs/20260101-000000/run.json").read_text(encoding="utf-8"))


def _list_missing_kept(paths: RunPaths) -> list[str]:
    return [name for name in _KEPT_FILES if not (paths.output_root / name).exists()]


def test_a_quality_run_started_on_the_fast_speed_goes_back_to_phase_2_defect():
    # A run made under Quality, started again on the Fast speed, resumed past phase 3 with no draft, and phase 4
    # stopped on "03_draft.md is missing"; now the start removes phase 3 on and runs it again under Fast
    paths = _build_run(("03_processed-frames/frame-000.json", "03_notes.md", "03_batch_summaries.json"))
    warning = workflow_switch_service.reset_for_workflow(paths, video_workflow_vocabulary.DIRECT_DRAFT, False)
    harness.check("the warning names both workflows and phase 2",
                  "from quality to direct draft" in warning and "phases 1 and 2 stay" in warning, warning)
    gone = [name for name in ("03_processed-frames", "03_notes.md", "03_batch_summaries.json", "04_writing")
            if (paths.output_root / name).exists()]
    harness.check("phase 3 and phase 4 are removed", not gone, str(gone))
    harness.check("phases 1 and 2 stay", not _list_missing_kept(paths), str(_list_missing_kept(paths)))
    store = _read_store(paths)
    harness.check("the store forgets phase 3 and its frames are pending again",
                  "frames_processing" not in store["phases"] and store["items"]["frame-000"]["status"] == "pending",
                  json.dumps(store))


def test_a_fast_run_started_on_quality_goes_back_to_phase_2_and_loses_its_draft_defect():
    # A run made under the Fast speed, started again on Quality or Balanced, kept its draft and had no notes for phase
    # 4; now the draft, the outline and phase 3's progress are removed
    paths = _build_run((output_contract.DRAFT_FILE, f"04_writing/{output_contract.DIRECT_DRAFT_OUTLINE_FILE}"))
    warning = workflow_switch_service.reset_for_workflow(paths, video_workflow_vocabulary.QUALITY, False)
    harness.check("the warning names both workflows", "from direct draft to quality" in warning, warning)
    harness.check("the draft and phase 4's outline are removed",
                  not (paths.output_root / output_contract.DRAFT_FILE).exists()
                  and not (paths.output_root / "04_writing").exists())
    harness.check("phases 1 and 2 stay", not _list_missing_kept(paths), str(_list_missing_kept(paths)))


def test_a_reset_to_2_removes_a_fast_run_s_draft_and_frame_descriptions_defect():
    # `reset-to 2` on a Fast run left its draft behind, so the next start read it as phase 3 done; the earlier fast
    # workflow's frame descriptions likewise
    paths = _build_run((output_contract.DRAFT_FILE, output_contract.FRAME_DESCRIPTIONS_FILE,
                        f"{output_contract.CALLS_DIR_NAME}/{output_contract.FAST_NOTE_SLUG_PREFIX}004.json"))
    run_reset_service.reset_to_phase(paths, 2)
    left = [name for name in (output_contract.DRAFT_FILE, output_contract.FRAME_DESCRIPTIONS_FILE,
                              f"{output_contract.CALLS_DIR_NAME}/{output_contract.FAST_NOTE_SLUG_PREFIX}004.json")
            if (paths.output_root / name).exists()]
    harness.check("both fast workflows' phase 3 files are removed", not left, str(left))


def test_balanced_after_quality_keeps_every_phase():
    paths = _build_run(("03_processed-frames/frame-000.json", "03_notes.md"))
    warning = workflow_switch_service.reset_for_workflow(paths, video_workflow_vocabulary.QUALITY, False)
    harness.check("the same workflow warns of nothing", warning == "", warning)
    harness.check("and removes nothing", (paths.output_root / "03_notes.md").is_file()
                  and (paths.output_root / "04_writing/parts.json").is_file())


def test_a_run_whose_phase_3_has_not_begun_is_not_reset():
    paths = _build_run(())
    warning = workflow_switch_service.reset_for_workflow(paths, video_workflow_vocabulary.DIRECT_DRAFT, False)
    harness.check("no phase 3 file: nothing to reset", warning == "", warning)
    harness.check("the store keeps its progress", _read_store(paths)["items"]["frame-000"]["status"] == "written")


def test_a_dry_run_warns_and_removes_nothing():
    paths = _build_run(("03_notes.md",))
    warning = workflow_switch_service.reset_for_workflow(paths, video_workflow_vocabulary.DIRECT_DRAFT, True)
    harness.check("the dry run says what a real start removes", "a real start removes phase 3" in warning, warning)
    harness.check("and keeps the notes", (paths.output_root / "03_notes.md").is_file())


def test_a_speed_changed_while_the_run_worked_reaches_its_workflow_at_the_next_start_defect():
    # The page's speed chip changed a working run from Quality to Fast: only the speed was kept, the workflow file still
    # said quality, and the next start ran Quality while the page showed Fast; now the next start takes the speed's
    paths = _build_run(())
    folder = paths.output_root
    run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed("quality", {}))
    run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed("fast", {}), is_workflow_kept=False)
    harness.check("the working run's workflow file still says quality",
                  video_workflow_service.resolve_workflow(folder) == video_workflow_vocabulary.QUALITY)
    next_workflow = run_speed_service.resolve_next_workflow(folder, "", None)
    harness.check("the next start takes the Fast speed's workflow",
                  next_workflow == video_workflow_vocabulary.DIRECT_DRAFT, next_workflow)
    workflow_switch_service.keep_next_workflow(folder, next_workflow)
    harness.check("and keeps it in the folder for the phases to read",
                  video_workflow_service.resolve_workflow(folder) == video_workflow_vocabulary.DIRECT_DRAFT)


def test_a_workflow_chosen_on_the_run_line_stays_over_the_kept_speed():
    paths = _build_run(())
    folder = paths.output_root
    run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed("fast", {}), is_workflow_kept=False)
    video_workflow_service.write_chosen_workflow(folder, video_workflow_vocabulary.QUALITY, "user")
    next_workflow = run_speed_service.resolve_next_workflow(folder, "", None)
    harness.check("the person's own choice wins", next_workflow == video_workflow_vocabulary.QUALITY, next_workflow)
    flagged = run_speed_service.resolve_next_workflow(folder, video_workflow_vocabulary.DIRECT_DRAFT, None)
    harness.check("and the run line's --workflow wins over both",
                  flagged == video_workflow_vocabulary.DIRECT_DRAFT, flagged)


def test_a_run_that_chose_fast_when_fast_was_the_earlier_workflow_keeps_it():
    # The Fast preset now points at direct-draft; a run that chose Fast before kept fast-processing in its speed's
    # values, and its resume must not switch it
    paths = _build_run((output_contract.FRAME_DESCRIPTIONS_FILE,))
    folder = paths.output_root
    old_fast = {"preset": "fast", "values": {"video.workflow": video_workflow_vocabulary.FAST_PROCESSING}, "name": "fast"}
    run_speed_service.write_chosen_speed(folder, old_fast)
    next_workflow = run_speed_service.resolve_next_workflow(folder, "", None)
    harness.check("the run keeps the earlier fast workflow",
                  next_workflow == video_workflow_vocabulary.FAST_PROCESSING, next_workflow)
    warning = workflow_switch_service.reset_for_workflow(paths, next_workflow, False)
    harness.check("and nothing is reset", warning == "" and (folder / output_contract.FRAME_DESCRIPTIONS_FILE).is_file(),
                  warning)
