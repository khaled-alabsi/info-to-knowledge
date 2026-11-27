"""The page's instruction routes: the tree, one file, a save, a refusal, the versions, a reset and the full prompt."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from services import instruction_store_service
from services import web_api_service
from utils import route_role_utils

TAKEAWAY = "instructions/audio/takeaway.md"
ADDED_RULE = "Write the Gateway's retry count as AcmeSync states it."


def test_the_instruction_routes_save_compare_and_reset_one_file():
    original_store = instruction_store_service.STORE_DIR
    with tempfile.TemporaryDirectory() as folder:
        instruction_store_service.set_store_dir(Path(folder) / "instructions")
        web_api_service.set_sign_in(False)
        try:
            client = TestClient(web_api_service.build_app())
            tree = client.get("/api/instructions").json()
            harness.check("the tree lists the workflows and the files", [w["key"] for w in tree["workflows"]]
                          == ["video", "audio", "pdf"] and any(f["id"] == TAKEAWAY for f in tree["files"]))
            file = client.get("/api/instructions/file", params={"id": TAKEAWAY}).json()
            harness.check("one file gives its default, its text in force and its title",
                          file["default"] == file["text"] and not file["is_edited"] and file["title"] == "Takeaway",
                          str(file)[:200])
            harness.check("a file the program does not ship is a 404",
                          client.get("/api/instructions/file", params={"id": "../config.py"}).status_code == 404)
            saved = client.put("/api/instructions/file", json={"id": TAKEAWAY, "text": file["default"] + ADDED_RULE,
                                                               "note": "retry counts"}).json()
            harness.check("a save is kept, with its stamp", saved["is_saved"] and saved["at"], str(saved))
            refused = client.put("/api/instructions/file", json={"id": TAKEAWAY,
                                                                 "text": "{{SHARED:gateway-rules}}"}).json()
            harness.check("a text no prompt can be built from is refused with why",
                          not refused["is_saved"] and any("gateway-rules" in line for line in refused["problems"]),
                          str(refused))
            versions = client.get("/api/instructions/versions", params={"id": TAKEAWAY}).json()
            harness.check("the versions list the save", [v["note"] for v in versions] == ["retry counts"], str(versions))
            version = client.get("/api/instructions/version", params={"id": TAKEAWAY, "at": versions[0]["at"]}).json()
            harness.check("a version gives its text and the default it was made over",
                          ADDED_RULE in version["text"] and version["default_text"] == file["default"])
            preview = client.post("/api/instructions/preview", json={"step": "takeaway"}).json()
            harness.check("the full prompt holds the text in force", ADDED_RULE in preview["text"], preview["error"])
            drafted = client.post("/api/instructions/preview", json={"step": "takeaway", "id": TAKEAWAY,
                                                                     "text": "Only the draft."}).json()
            harness.check("the full prompt of a draft holds the draft", drafted["text"] == "Only the draft.")
            reset = client.post("/api/instructions/reset", json={"id": TAKEAWAY}).json()
            back = client.get("/api/instructions/file", params={"id": TAKEAWAY}).json()
            harness.check("a reset puts the default back", reset["is_changed"] and not back["is_edited"]
                          and back["text"] == back["default"] and back["versions"] == 2, str(reset))
        finally:
            web_api_service.set_sign_in(True)
            instruction_store_service.set_store_dir(original_store)


def test_only_an_admin_may_change_an_instruction():
    harness.check("saving an instruction needs the admin role",
                  route_role_utils.find_needed_role("PUT", "/api/instructions/file") == "admin")
    harness.check("a reset needs the admin role", route_role_utils.find_needed_role("POST", "/api/instructions/reset") == "admin")
    harness.check("reading the tree needs a viewer", route_role_utils.find_needed_role("GET", "/api/instructions") == "viewer")
