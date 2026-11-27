"""The Settings page's own route on made settings files: a model named with a dot
(`qwen3.5-4b` in the default settings) could not be saved, its key split at every dot. Through the real server: the
row is listed with its path, and the path the page sends back saves the value under the model's whole name."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from support import harness

import config
from services import host_model_service
from services import job_queue_service
from services import settings_service
from services import web_api_service


def _list_models_of_made_host(provider: str,
                              base_url: str) -> list[str] | None:
    """One made LM Studio on this machine serving two models; no other host answers."""

    return ["big-model", "small-model"] if (provider, base_url) == ("lmstudio", "http://localhost:1234/v1") else None


def test_a_model_named_with_a_dot_is_saved_from_the_settings_route_defect():
    # A model named with a dot could not be saved from the Settings page: the value went under the name cut at its dot
    # and was refused. The route now lists each row's path and saves by the path the page sends back
    folder = Path(tempfile.mkdtemp())
    models = {"small.v2": {"spec": "lmstudio:small-model"}}
    for name in ("playbook.default.json", "playbook.local.json"):
        (folder / name).write_text(json.dumps({"models": models}), encoding="utf-8")
    previous = (config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, job_queue_service.QUEUE_DIR)
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT, job_queue_service.QUEUE_DIR = folder, folder / "ui"
    settings_service.set_host_lister(_list_models_of_made_host)
    try:
        client = TestClient(web_api_service.build_app())
        rows = {row["key"]: row for row in client.get("/api/settings").json()}
        row = rows["models.small.v2.spec"]
        answer = client.put("/api/settings", json={"key": row["key"], "key_parts": row["path"],
                                                   "new_value": "lmstudio:big-model", "profile": ""}).json()
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT, job_queue_service.QUEUE_DIR = previous
        settings_service.set_host_lister(host_model_service.list_host_models)
    written = [json.loads((folder / name).read_text(encoding="utf-8"))["models"]
               for name in ("playbook.default.json", "playbook.local.json")]
    harness.check("the row offers the host's models", [option["value"] for option in row["choice_options"]]
                  == ["lmstudio:big-model", "lmstudio:small-model"], str(row))
    harness.check("saved under the model's whole name in both default settings files", answer.get("exit_code") == 0
                  and all(models["small.v2"]["spec"] == "lmstudio:big-model" and "small" not in models for models in written),
                  f"{answer} {written}")
