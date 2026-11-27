"""The Manager's Rename on a run and a branch made from it, which share one AI name (made data, the folders of
pdf-02's two runs: a branch named "-from-1-from-5" beside one named "-from-1-2-from-4", both with one topic). The
branch renamed first took the AI name, and the other run was kept for good with "is taken" (the owner: "those 2
files cannt be renamed"). Through the page's own request: both runs ticked at once take the name, one with "-2", and
renamed back each has its own file name again."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from models.run_paths import RunPaths
from services import document_file_service
from services import run_status_service
from services import web_api_service

TOPIC = "Gateway refund report tables"
FOLDERS = ("report-tables-from-1-from-5", "report-tables-from-1-2-from-4")


def _build_run(root: Path,
               name: str) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    parts = document_file_service.build_parts_path(RunPaths(repository=folder, module=folder.name,
                                                            output_root_override=folder))
    parts.parent.mkdir(parents=True, exist_ok=True)
    parts.write_text(json.dumps({"topic": TOPIC}), encoding="utf-8")


def _post_names(client: TestClient,
                names: list[str],
                is_by_topic: bool) -> dict:
    runs = [{"root": "output", "name": name} for name in names]

    return client.post("/api/runs/display-names", json={"runs": runs, "is_by_topic": is_by_topic}).json()


def test_a_run_and_its_branch_sharing_an_ai_name_are_both_renamed_and_back_defect():
    root = Path(tempfile.mkdtemp()) / "output"
    for name in FOLDERS:
        _build_run(root, name)
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root
    try:
        client = TestClient(web_api_service.build_app())
        answer = _post_names(client, list(FOLDERS), True)
        names = sorted(path.name for path in root.iterdir())
        harness.check("both runs take the AI name, the second with -2, and neither is kept as taken",
                      names == ["gateway-refund-report-tables", "gateway-refund-report-tables-2"]
                      and "is taken" not in answer["output"], f"{names} {answer['output']}")
        shown = [run for run in client.get("/api/runs").json() if run["name"] in names]
        harness.check("the run list shows both under the AI name as written",
                      len(shown) == 2 and all(run["display_name"] == TOPIC for run in shown), str(shown))
        _post_names(client, names, False)
        harness.check("renamed back, each has its own file name again",
                      sorted(path.name for path in root.iterdir()) == sorted(FOLDERS), str(list(root.iterdir())))
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)
