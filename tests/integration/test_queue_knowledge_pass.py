"""A done PDF run's knowledge pass queued through the web API exactly as the page's "Queue the knowledge pass" sends it: the server's real app takes the body, saves the pass's wishes and queues the
run line with `--document-pass knowledge`. The settings files and the queue are made for the test; no run starts."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

import config
from services import job_queue_service
from services import web_api_service


def test_the_page_s_knowledge_pass_is_queued_with_its_wishes_defect():
    # Defect: "Queue the knowledge pass" was refused, "422: Input should be '', 'cleanup' or 'rewrite'"
    # Fix: the server's job body checks the pass against the program's one list of passes
    root = Path(tempfile.mkdtemp())
    (root / "default.json").write_text("{}", encoding="utf-8")
    (root / "local.json").write_text("{}", encoding="utf-8")
    saved = config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME, job_queue_service.QUEUE_DIR
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME = root / "default.json", root / "local.json", ""
    job_queue_service.QUEUE_DIR = root / "ui"
    body = {"items": [{"video": str(root / "Gateway guide.pdf"), "output": str(root / "output" / "gateway-guide")}],
            "only_phase": 7, "is_resuming": True, "document_pass": "knowledge",
            "document_wishes": "keep the field tables", "document_source": "current"}
    try:
        client = TestClient(web_api_service.build_app())
        answer = client.post("/api/jobs", json=body)
        refused = client.post("/api/jobs", json={**body, "document_pass": "polish"})
        settings = json.loads((root / "default.json").read_text(encoding="utf-8"))
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME, job_queue_service.QUEUE_DIR = saved
    harness.check("the knowledge pass is queued", answer.status_code == 200, answer.text)
    command = (answer.json() or [{}])[0].get("command", []) if answer.status_code == 200 else []
    harness.check("its run line asks phase 7 for the knowledge pass with the wishes",
                  "--document-pass" in command and command[command.index("--document-pass") + 1] == "knowledge"
                  and "keep the field tables" in command, str(command))
    harness.check("and to start from the current document, as the page asked",
                  "--document-source" in command and command[command.index("--document-source") + 1] == "current",
                  str(command))
    harness.check("the knowledge pass's wishes are saved as its own setting",
                  settings.get("pdf", {}).get("knowledgeWishes") == "keep the field tables", str(settings))
    harness.check("a pass the program has not is still refused", refused.status_code == 422 and "polish" in refused.text,
                  refused.text)
