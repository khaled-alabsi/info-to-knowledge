"""A folder a refused start left, holding only the speed and workflow a run line chose, was shown on the page as an
unfinished run at phase 1 (DEFECTS 720). Made data: a real run folder and such a leftover under a made output root,
read through the server's real run list route."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from services import run_status_service
from services import web_api_service


def _make_leftover(root: Path) -> None:
    leftover = root / "Gateway-weekly"
    leftover.mkdir(parents=True)
    (leftover / "run-speed.json").write_text('{"preset": "balanced", "values": {}}', encoding="utf-8")
    (leftover / "run-workflow.json").write_text('{"workflow": "quality"}', encoding="utf-8")


def _make_run(root: Path) -> None:
    run = root / "gateway-weekly-review"
    (run / "logs" / "run-1").mkdir(parents=True)
    (run / "logs" / "run-1" / "run.json").write_text('{"status": "complete", "video": "inbox/Gateway-weekly.mp4"}',
                                                     encoding="utf-8")
    (run / "run-speed.json").write_text('{"preset": "balanced", "values": {}}', encoding="utf-8")


def test_a_leftover_folder_of_chosen_settings_is_not_listed_as_a_run_defect():
    # The page listed the leftover under Not finished; now the route leaves it out and keeps the real run
    output = Path(tempfile.mkdtemp()) / "output"
    _make_leftover(output)
    _make_run(output)
    previous = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS.update({"output": output})
    run_status_service.clear_shared_run_list()
    try:
        answer = TestClient(web_api_service.build_app()).get("/api/runs")
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous)
        run_status_service.clear_shared_run_list()
    names = [run["name"] for run in answer.json()] if answer.status_code == 200 else []
    harness.check("the route answers", answer.status_code == 200, str(answer.status_code))
    harness.check("the leftover is not a run, the real run is", names == ["gateway-weekly-review"], str(names))
