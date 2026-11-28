"""A run paused from its page, holding at its pause, listed as "running" in the runs list (the owner: "it paused:) But
the pill don't show that"). Made data: a run folder with a run record and a console log, a
stand-in process the program takes for the live run, the pause sent through the page's own request, and the list read
through the page's own request before and after the run's console says it holds."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import control_signal_service
from services import run_reset_service
from services import run_status_service
from services import run_store_service
from services import web_api_service

_RUN_ID = "2026-10-01_09-00"
_NAME = "Gateway-weekly"


def _build_run(root: Path) -> Path:
    """A run folder whose phase 2 is done and whose console log says phase 3 sent a batch."""

    folder = root / _NAME
    paths = RunPaths(repository=root, module=f"{_NAME}.mp4", output_root_override=folder)
    store = run_store_service.RunStore.create(paths.run_state(_RUN_ID), RunIdentity.for_run(_RUN_ID, paths),
                                              budgets={}, models=[])
    store.set_phase("transcribe", "complete")
    store.set_phase("frames_preparing", "complete")
    store.save()
    (paths.run_dir(_RUN_ID) / "09-00.log").write_text("phase 3: batch 4 sent to the model\n", encoding="utf-8")

    return folder


def _find_row(client: TestClient) -> dict:
    return next(run for run in client.get("/api/runs").json() if run["name"] == _NAME)


def test_a_run_paused_from_its_page_reads_pausing_then_paused_in_the_runs_list_defect():
    root = Path(tempfile.mkdtemp()) / "output"
    folder = _build_run(root)
    stand_in = Path(tempfile.mkdtemp()) / "main.py"
    stand_in.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    # a process `ps` shows as `main.py run --output <folder>`, waiting on its input until it is closed
    live = subprocess.Popen([sys.executable, str(stand_in), "run", "--output", str(folder)], stdin=subprocess.PIPE)
    own_main = run_reset_service.set_own_main(stand_in)
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root
    try:
        client = TestClient(web_api_service.build_app())
        before = _find_row(client)
        sent = client.post(f"/api/runs/output/{_NAME}/control", json={"signal": "pause"})
        pausing = _find_row(client)
        run_status_service.find_newest_log(folder).write_text(
            f"phase 3: batch 4 done; paused {control_signal_service.HELD_LINE_END}\n", encoding="utf-8")
        # the page's list is built again once its few seconds are over; the test does not wait them out
        run_status_service.clear_shared_run_list()
        paused = _find_row(client)
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)
        run_reset_service.set_own_main(own_main)
        live.stdin.close()
        live.wait()
    harness.check("the live run is listed running with no pause", before["is_live"] and not before.get("pause"),
                  str(before.get("pause")))
    harness.check("the page's pause is taken", sent.status_code == 200, sent.text)
    harness.check("while its model call ends it reads pausing", pausing.get("pause") == "pausing", str(pausing.get("pause")))
    harness.check("once it holds it reads paused", paused.get("pause") == "paused", str(paused.get("pause")))
