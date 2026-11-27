"""Projects: every run in the default project until moved, a member's role in a project over
its account's own, another project hidden from those no member of it, an admin everywhere."""

from __future__ import annotations

import tempfile
from pathlib import Path

from support import harness

from models.ui_errors import AccountError
from services import job_queue_service
from services import project_service

VIEWER = {"name": "Jonas", "role": "viewer"}
ADMIN = {"name": "Mira", "role": "admin"}


def _in_fresh_folder(check) -> None:
    """Run `check` with the queue's folder, where the projects live, in a new temporary folder."""

    previous = job_queue_service.QUEUE_DIR
    job_queue_service.QUEUE_DIR = Path(tempfile.mkdtemp()) / "ui"
    try:
        check()
    finally:
        job_queue_service.QUEUE_DIR = previous


def test_every_run_is_in_the_default_project_until_moved():
    def check():
        harness.check("a run no project lists is in the default project",
                      project_service.find_run_project("gateway-review") == project_service.DEFAULT_PROJECT)
        slug = project_service.add_project("Acme Gateway")
        project_service.move_run("gateway-review", slug)
        harness.check("a moved run is in its project", project_service.find_run_project("gateway-review") == "acme-gateway")
        project_service.remove_project(slug)
        harness.check("a removed project's runs go back to the default",
                      project_service.find_run_project("gateway-review") == project_service.DEFAULT_PROJECT)

    _in_fresh_folder(check)


def test_a_member_s_role_in_a_project_overrides_the_account_s_own():
    def check():
        slug = project_service.add_project("Acme Gateway")
        harness.check("a viewer is a viewer in the default project",
                      project_service.find_role(VIEWER, project_service.DEFAULT_PROJECT) == "viewer")
        harness.check("and has no access to a project it is no member of", project_service.find_role(VIEWER, slug) is None)
        project_service.set_member(slug, "Jonas", "operator")
        harness.check("a member has the role the project gives it", project_service.find_role(VIEWER, slug) == "operator")
        harness.check("the projects a viewer sees", project_service.list_visible_projects(VIEWER) == ["default", slug])
        project_service.set_member(project_service.DEFAULT_PROJECT, "Jonas", "operator")
        harness.check("a role set in the default project overrides the account's",
                      project_service.find_role(VIEWER, project_service.DEFAULT_PROJECT) == "operator")
        project_service.set_member(slug, "Jonas", None)
        harness.check("a member removed has no access again", project_service.find_role(VIEWER, slug) is None)
        harness.check("an admin may do everything everywhere", project_service.find_role(ADMIN, slug) == "admin")

    _in_fresh_folder(check)


def test_a_project_is_refused_by_name_when_it_cannot_be_made():
    def check():
        project_service.add_project("Acme Gateway")
        for label, call in (("a title of no letter", lambda: project_service.add_project("  ?! ")),
                            ("a project of the same slug", lambda: project_service.add_project("acme gateway")),
                            ("the default project removed", lambda: project_service.remove_project("default")),
                            ("an unknown role", lambda: project_service.set_member("acme-gateway", "Jonas", "owner")),
                            ("a run moved to no project", lambda: project_service.move_run("x", "nowhere"))):
            try:
                call()
                harness.check(f"{label} is refused", False)
            except AccountError:
                harness.check(f"{label} is refused", True)

    _in_fresh_folder(check)


def test_a_group_s_role_in_a_project_reaches_every_account_in_it():
    def check():
        from services import group_service

        slug = project_service.add_project("Acme Gateway")
        group_service.add_group("gateway-team")
        group_service.set_group_accounts("gateway-team", ["Jonas"])
        harness.check("a project that grants nothing gives a group's account nothing", project_service.find_role(VIEWER, slug) is None)
        project_service.set_group_role(slug, "gateway-team", "operator")
        harness.check("the group's role reaches its account", project_service.find_role(VIEWER, slug) == "operator")
        project_service.set_member(slug, "Jonas", "viewer")
        harness.check("the highest of the roles given by name and by group stands", project_service.find_role(VIEWER, slug) == "operator")
        token_identity = {"name": "Jonas", "role": "viewer", "groups": []}
        harness.check("an identity naming its groups, as a sign-on token does, is judged by those alone",
                      project_service.find_role(token_identity, slug) == "viewer")

    _in_fresh_folder(check)


def test_each_route_of_a_run_needs_the_role_its_project_gives():
    def check():
        from fastapi.testclient import TestClient
        from services import account_service
        from services import web_api_service

        account_service.add_account("Jonas", "pw", "viewer")
        slug = project_service.add_project("Acme Gateway")
        project_service.move_run("gateway-review", slug)
        project_service.set_member(slug, "Jonas", "operator")
        client = TestClient(web_api_service.build_app())
        bearer = {"authorization": "Bearer " + client.post("/api/auth/login", json={"name": "Jonas", "password": "pw"}).json()["token"]}
        in_project = client.put("/api/runs/output/gateway-review/tags", json={"tags": []}, headers=bearer).status_code
        in_default = client.put("/api/runs/output/other-run/tags", json={"tags": []}, headers=bearer).status_code
        harness.check("an operator of the project changes its run; a viewer of the default project may not change one of it",
                      in_project != 403 and in_default == 403, str((in_project, in_default)))
        project_service.set_member(slug, "Jonas", None)
        closed = client.get("/api/runs/output/gateway-review", headers=bearer).status_code
        harness.check("a run of a project that gives the account nothing is closed to it", closed == 403, str(closed))

    _in_fresh_folder(check)


def test_with_sign_in_off_nobody_signs_in_and_every_project_is_open():
    def check():
        from fastapi.testclient import TestClient
        from services import account_service
        from services import web_api_service

        account_service.add_account("Jonas", "pw", "viewer")
        project_service.move_run("gateway-review", project_service.add_project("Acme Gateway"))
        web_api_service.set_sign_in(False)
        try:
            client = TestClient(web_api_service.build_app())
            me = client.get("/api/auth/me").json()
            harness.check("the page is told nobody signs in", me["is_sign_in_required"] is False, str(me))
            harness.check("a run of a closed project is open with no session",
                          client.get("/api/runs/output/gateway-review").status_code != 401
                          and client.get("/api/runs/output/gateway-review").status_code != 403)
        finally:
            web_api_service.set_sign_in(True)

    _in_fresh_folder(check)


def test_an_admin_manages_projects_and_groups_and_others_see_only_theirs():
    def check():
        from fastapi.testclient import TestClient
        from services import account_service
        from services import web_api_service

        account_service.add_account("Mira", "pw", "admin")
        account_service.add_account("Jonas", "pw", "viewer")
        client = TestClient(web_api_service.build_app())

        def sign_in(name):
            return {"authorization": "Bearer " + client.post("/api/auth/login", json={"name": name, "password": "pw"}).json()["token"]}

        admin, viewer = sign_in("Mira"), sign_in("Jonas")
        made = client.post("/api/projects", json={"title": "Acme Gateway"}, headers=admin)
        client.post("/api/groups", json={"name": "gateway-team"}, headers=admin)
        client.put("/api/groups/gateway-team", json={"accounts": ["Jonas"]}, headers=admin)
        granted = client.put("/api/projects/acme-gateway/groups/gateway-team", json={"role": "operator"}, headers=admin)
        moved = client.put("/api/projects/acme-gateway/runs/gateway-review", headers=admin)
        harness.check("an admin makes a project and a group, grants the group a role and moves a run in",
                      [made.status_code, granted.status_code, moved.status_code] == [200, 200, 200], made.text)
        harness.check("a viewer may not make a project", client.post("/api/projects", json={"title": "B"}, headers=viewer).status_code == 403)
        seen = client.get("/api/projects", headers=viewer).json()
        harness.check("a viewer sees the projects it has a role in, with that role, and no group list",
                      [(p["slug"], p["role"]) for p in seen["projects"]] == [("default", "viewer"), ("acme-gateway", "operator")]
                      and seen["groups"] == [], str(seen))

    _in_fresh_folder(check)


def test_an_inbox_item_whose_run_is_in_a_closed_project_is_left_out():
    def check():
        from unittest import mock
        from fastapi.testclient import TestClient
        from services import account_service
        from services import run_status_service
        from services import web_api_service

        account_service.add_account("Jonas", "pw", "viewer")
        project_service.move_run("gateway-review", project_service.add_project("Acme Gateway"))
        items = [{"name": "a.mp4", "run_folder": "output/gateway-review"}, {"name": "b.mp4", "run_folder": "output/other"},
                 {"name": "c.mp4", "run_folder": ""}]
        client = TestClient(web_api_service.build_app())
        bearer = {"authorization": "Bearer " + client.post("/api/auth/login", json={"name": "Jonas", "password": "pw"}).json()["token"]}
        with mock.patch.object(run_status_service, "list_inbox_items", return_value=items):
            shown = [item["name"] for item in client.get("/api/inbox", headers=bearer).json()]
        harness.check("the item of a closed project's run is left out; one never run stays", shown == ["b.mp4", "c.mp4"], str(shown))

    _in_fresh_folder(check)


def test_i2k_workspace_moves_the_inbox_the_run_folders_and_ui_together():
    import os
    import subprocess
    import sys

    folder = tempfile.mkdtemp()
    shown = subprocess.run([sys.executable, "-c", "import config; print(config.INBOX_DIR, config.DOCUMENT_ROOT, config.UI_DIR)"],
                           cwd="src", env={**os.environ, "I2K_WORKSPACE": folder}, capture_output=True, text=True, check=True).stdout.split()
    real = str(Path(folder).resolve())
    harness.check("every workspace folder is under the one named", shown == [f"{real}/inbox", f"{real}/output", f"{real}/ui"], str(shown))
