"""Workflow speed: presets that set the settings changing how long a video run takes, a
run's own speed kept in its folder and laid over the settings at every start, and "custom" once a value is changed."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

import config
from cli import run_command
from models import output_contract
from models.run_job import RunJobRequest
from services import job_queue_service
from services import run_speed_service
from services import run_status_service
from services import video_workflow_service
from services import web_api_service


def test_balanced_keeps_the_large_model_and_drops_the_critique_repairs_and_rule_set():
    balanced = config.settings.speed_presets["balanced"]
    harness.check("Balanced takes the quality workflow, with no critique, fix, repair round, rule set or review",
                  balanced["video.workflow"] == "quality" and not balanced["framesProcessing.isCritiqueOn"]
                  and not balanced["framesProcessing.isCritiqueRepairOn"] and balanced["framesProcessing.repairCalls"] == 0
                  and balanced["framesProcessing.singleFrameAttempts"] == 0 and not balanced["review.isOn"]
                  and not balanced["critiqueCollection.isOn"], json.dumps(balanced))
    options = run_speed_service.describe_speed_options()
    harness.check("the menu lists the three presets and every setting, and a run nobody chose a speed for takes Balanced",
                  list(options["presets"]) == ["quality", "balanced", "fast"] and len(options["settings"]) == 10
                  and options["default_preset"] == "balanced", str(options["default_preset"]))


def test_a_value_changed_from_a_preset_makes_the_speed_custom():
    same = run_speed_service.resolve_speed("balanced", {"review.isOn": False})
    changed = run_speed_service.resolve_speed("balanced", {"review.isOn": True})
    harness.check("a value the preset holds keeps its name; another makes it custom",
                  same["name"] == "balanced" and changed["name"] == "custom" and changed["values"]["review.isOn"] is True)
    for preset, changes in (("turbo", {}), ("balanced", {"frames.mode": "change"})):
        try:
            run_speed_service.resolve_speed(preset, changes)
            refusal = ""
        except ValueError as exc:
            refusal = str(exc)
        harness.check(f"{preset} {changes}: refused by name", bool(refusal), refusal)


def test_a_run_keeps_its_speed_and_every_start_lays_it_over_the_settings():
    folder = Path(tempfile.mkdtemp())
    run_speed_service.write_chosen_speed(folder, run_command.choose_speed(folder, "balanced",
                                                                          ["framesProcessing.framesPerCall=6"]))
    harness.check("no flag names no speed", run_command.choose_speed(folder, "", []) is None)
    kept = run_speed_service.read_chosen_speed(folder)
    harness.check("the preset and the changed value are kept, and no flag leaves them",
                  kept["preset"] == "balanced" and kept["values"]["framesProcessing.framesPerCall"] == 6
                  and kept["name"] == "custom", json.dumps(kept))
    harness.check("the speed sets the run's workflow too", video_workflow_service.read_chosen_workflow(folder)
                  == {"workflow": "quality", "chosenBy": "workflow speed"})
    saved = config.settings
    try:
        run_speed_service.apply_chosen_speed(folder)
        applied = config.settings
    finally:
        config.settings = saved
    harness.check("applied, the critique, its fix, the repairs and the rule set are off and 6 frames go per call",
                  not applied.frames_processing.is_critique_on and not applied.frames_processing.is_critique_repair_on
                  and applied.frames_processing.repair_calls == 0 and applied.frames_processing.frames_per_call == 6
                  and not applied.critique_collection.is_on and not applied.review.is_on)


def test_a_balanced_run_kept_before_the_open_questions_setting_runs_without_them_defect():
    # Defect: a Balanced video run still asked each batch its open questions and their answers, since the menu had no
    # setting for them. Fix: Balanced and Fast turn them off, and a speed kept before takes the preset's new values.
    folder = Path(tempfile.mkdtemp())
    old_balanced = {key: value for key, value in config.settings.speed_presets["balanced"].items()
                    if key != "framesProcessing.isOpenQuestionsOn"}
    (folder / output_contract.RUN_SPEED_FILE).write_text(
        json.dumps({"preset": "balanced", "values": old_balanced, "name": "balanced"}), encoding="utf-8")
    saved = config.settings
    try:
        run_speed_service.apply_chosen_speed(folder)
        applied = config.settings
    finally:
        config.settings = saved
    harness.check("Balanced and Fast have no open questions, Quality has them",
                  not config.settings.speed_presets["balanced"]["framesProcessing.isOpenQuestionsOn"]
                  and not config.settings.speed_presets["fast"]["framesProcessing.isOpenQuestionsOn"]
                  and config.settings.speed_presets["quality"]["framesProcessing.isOpenQuestionsOn"])
    harness.check("a Balanced run kept without the setting runs with no open questions",
                  not applied.frames_processing.is_open_questions_on)


def test_choosing_a_run_s_speed_writes_nothing_into_its_folder_defect():
    # The defect: a resume refused under a renamed run folder's old name had already written its speed there, which made
    # an empty folder of that name the page listed as unfinished. The fix: choosing the speed writes nothing.
    folder = Path(tempfile.mkdtemp()) / "Gateway-weekly"
    speed = run_command.choose_speed(folder, "balanced", [])
    harness.check("the speed is chosen and the folder stays unmade", speed["preset"] == "balanced" and not folder.exists(),
                  str(folder.exists()))


def test_a_job_with_a_speed_names_it_on_its_run_line_and_keeps_it_in_its_folder_at_once_defect():
    # The defect: a run queued as Balanced read "Speed: Fast" on its page until its job started. The fix: the server
    # keeps the speed in an existing run folder the moment the job is queued, and the run line names it too.
    command = job_queue_service.build_run_command(RunJobRequest(video=Path("inbox/a.mp4"), output_root=Path("output/a"),
                                                                speed_preset="balanced",
                                                                speed_settings=("review.isOn=false",)))
    harness.check("the run line carries the preset and each changed value",
                  command[command.index("--speed-preset") + 1] == "balanced"
                  and command[command.index("--speed-setting") + 1] == "review.isOn=false", " ".join(command))
    root = Path(tempfile.mkdtemp())
    run = root / "output" / "Gateway-demo"
    run.mkdir(parents=True)
    (run / output_contract.LOOP_ITEMS_FILE).write_text(json.dumps({"schemaVersion": 1, "items": []}), encoding="utf-8")
    saved = (dict(run_status_service.RUN_ROOTS), job_queue_service.QUEUE_DIR)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": root / "output"})
    job_queue_service.QUEUE_DIR = root / "ui"
    try:
        client = TestClient(web_api_service.build_app())
        answer = client.post("/api/jobs", json={"items": [{"video": "inbox/Gateway-demo.mp4", "output": "output/Gateway-demo"}],
                                                "from_phase": 3, "speed_preset": "balanced",
                                                "speed_settings": {"review.isOn": True}})
        kept = run_speed_service.read_chosen_speed(run) or {}
        refused = client.post("/api/jobs", json={"items": [{"video": "inbox/x.mp4", "output": "output/x"}],
                                                 "speed_preset": "turbo"})
        # the page sends a run folder by its full path: its speed is kept the same way
        (run / output_contract.RUN_SPEED_FILE).unlink()
        by_full_path = client.post("/api/jobs", json={"items": [{"video": "inbox/Gateway-demo.mp4", "output": str(run)}],
                                                      "from_phase": 3, "speed_preset": "fast"})
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved[0])
        job_queue_service.QUEUE_DIR = saved[1]
    harness.check("queued, the run folder already holds its speed", answer.status_code == 200
                  and kept.get("preset") == "balanced" and kept.get("name") == "custom", answer.text[:300])
    harness.check("the job records the changed value as the run line takes it",
                  answer.json()[0]["request"]["speed_settings"] == ["review.isOn=true"], answer.text[:300])
    harness.check("an unknown preset is refused by name", refused.status_code == 400 and "turbo" in refused.text, refused.text)
    harness.check("a run folder sent by its full path keeps its speed at once too (defect: it was kept only for output/<name>)",
                  by_full_path.status_code == 200
                  and (run_speed_service.read_chosen_speed(run) or {}).get("preset") == "fast", by_full_path.text[:200])


def test_a_run_kept_as_balanced_with_no_change_by_hand_runs_with_balanced_s_values_of_now_defect():
    # The defect: a run chosen as Balanced showed "Speed: Custom", since its folder held an old copy of every Balanced
    # value (review on) and the page and the next start both took the old copy over Balanced as it is now. The fix: the
    # folder keeps the changes made by hand apart, and a run with none runs with the preset's current values.
    stale = {**config.settings.speed_presets["balanced"], "review.isOn": True}
    folder = Path(tempfile.mkdtemp())
    run_speed_service.write_chosen_speed(folder, run_speed_service.resolve_speed("balanced", {}))
    kept = run_speed_service.read_chosen_speed(folder)
    harness.check("a speed chosen with no change by hand keeps an empty list of changes and the name Balanced",
                  kept["changes"] == {} and kept["name"] == "balanced", json.dumps(kept))
    harness.check("a run with one change by hand keeps that change apart",
                  run_speed_service.resolve_speed("balanced", {"review.isOn": True})["changes"] == {"review.isOn": True})
    with_changes = {"preset": "balanced", "values": stale, "name": "custom", "changes": {}}
    harness.check("a speed with no change by hand takes the preset's values of now, whatever its stored copy holds",
                  run_speed_service.list_speed_values(with_changes) == config.settings.speed_presets["balanced"])
    harness.check("a change by hand stays on top of the preset",
                  run_speed_service.list_speed_values({**with_changes, "changes": {"review.isOn": True}})["review.isOn"] is True)
    legacy = {"preset": "balanced", "values": stale, "name": "custom"}
    harness.check("a speed kept before changes were kept apart still runs with the values it stored",
                  run_speed_service.list_speed_values(legacy)["review.isOn"] is True)
