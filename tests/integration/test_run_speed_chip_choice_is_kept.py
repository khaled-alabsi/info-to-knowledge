"""A run's page read "Speed: Custom" though the owner chose Balanced, and choosing Balanced again in the chip was not
kept: the chip held its choice on the page alone until the next Resume, and the run's folder kept an old copy of every
Balanced value (review on) that the page read as a change by hand. Made data: an invented run folder holding such an
old copy, the real server route the chip now calls, the run's page data read back, and the values its next start takes."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

import config
from models import output_contract
from services import job_queue_service
from services import run_speed_service
from services import run_status_service
from services import video_workflow_service
from services import web_api_service


def _make_run_with_old_balanced_copy() -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp())
    run = root / "output" / "Gateway-demo"
    run.mkdir(parents=True)
    (run / output_contract.LOOP_ITEMS_FILE).write_text(json.dumps({"schemaVersion": 1, "items": []}), encoding="utf-8")
    old_balanced = {**config.settings.speed_presets["balanced"], "review.isOn": True}
    (run / output_contract.RUN_SPEED_FILE).write_text(
        json.dumps({"preset": "balanced", "values": old_balanced, "name": "custom"}), encoding="utf-8")

    return root, run


def test_choosing_balanced_again_in_the_chip_keeps_it_and_the_page_reads_balanced_defect():
    # Before: the chip's choice stayed on the page and the folder kept the old copy, named custom. Now: the route keeps
    # the preset and no change by hand, and the next start takes Balanced's values of now.
    root, run = _make_run_with_old_balanced_copy()
    saved = (dict(run_status_service.RUN_ROOTS), job_queue_service.QUEUE_DIR)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": root / "output"})
    job_queue_service.QUEUE_DIR = root / "ui"
    try:
        client = TestClient(web_api_service.build_app())
        before = run_speed_service.read_chosen_speed(run)
        answer = client.post("/api/runs/output/Gateway-demo/speed", json={"speed_preset": "balanced", "speed_settings": {}})
        refused = client.post("/api/runs/output/Gateway-demo/speed", json={"speed_preset": "turbo", "speed_settings": {}})
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved[0])
        job_queue_service.QUEUE_DIR = saved[1]
    kept = run_speed_service.read_chosen_speed(run) or {}
    harness.check("the folder held an old Balanced copy named custom before the choice",
                  before["name"] == "custom" and before["values"]["review.isOn"] is True, json.dumps(before))
    harness.check("the route answers and the folder now holds Balanced with no change by hand",
                  answer.status_code == 200 and kept["preset"] == "balanced" and kept["changes"] == {}
                  and kept["name"] == "balanced", answer.text[:300])
    harness.check("the next start takes Balanced's values of now, review off",
                  run_speed_service.list_speed_values(kept)["review.isOn"] is False)
    harness.check("an unknown preset is refused by name and changes nothing",
                  refused.status_code == 400 and "turbo" in refused.text and (run_speed_service.read_chosen_speed(run) or {}) == kept,
                  refused.text[:200])


def test_a_speed_chosen_while_the_run_works_leaves_its_workflow_file_alone_defect():
    # The run reads its workflow file while it works, so a chip choice made then is kept in the speed file and the
    # workflow follows at the next start
    root, run = _make_run_with_old_balanced_copy()
    video_workflow_service.write_chosen_workflow(run, "quality", "test")
    saved = (dict(run_status_service.RUN_ROOTS), job_queue_service.QUEUE_DIR, run_status_service.list_live_pids)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": root / "output"})
    job_queue_service.QUEUE_DIR = root / "ui"
    run_status_service.list_live_pids = lambda folder: [4242]
    try:
        client = TestClient(web_api_service.build_app())
        answer = client.post("/api/runs/output/Gateway-demo/speed", json={"speed_preset": "fast", "speed_settings": {}})
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(saved[0])
        job_queue_service.QUEUE_DIR = saved[1]
        run_status_service.list_live_pids = saved[2]
    harness.check("the speed file holds Fast and the workflow file still says quality",
                  answer.status_code == 200 and (run_speed_service.read_chosen_speed(run) or {}).get("preset") == "fast"
                  and video_workflow_service.read_chosen_workflow(run)["workflow"] == "quality", answer.text[:300])
