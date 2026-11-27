"""Accounts and their sessions: kept in the queue's folder, a password never stored, a
session ended with its account or its password."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from support import harness

from cli import accounts_command
from models import account_role_vocabulary
from models.ui_errors import AccountError
from services import account_service
from services import job_queue_service
from utils import route_role_utils


def _in_fresh_folder(check) -> None:
    """Run `check` with the queue's folder, where the accounts live, in a new temporary folder."""

    previous = job_queue_service.QUEUE_DIR
    job_queue_service.QUEUE_DIR = Path(tempfile.mkdtemp()) / "ui"
    try:
        check()
    finally:
        job_queue_service.QUEUE_DIR = previous


def _refuses(action) -> str:
    try:
        action()
    except AccountError as exc:
        return str(exc)

    return ""


def test_a_role_may_do_what_the_roles_before_it_may():
    harness.check("an admin may do an operator's work, a viewer may not, an unknown role nothing",
                  route_role_utils.is_role_allowed("admin", "operator")
                  and not route_role_utils.is_role_allowed("viewer", "operator")
                  and not route_role_utils.is_role_allowed("guest", "viewer"))


def test_an_account_signs_in_with_its_password_only_and_its_hash_is_never_listed():
    def check():
        harness.check("no account at first", not account_service.has_accounts())
        account_service.add_account("Jonas", "s3cret", "operator")
        token = account_service.sign_in("Jonas", "s3cret")
        stored = json.loads((job_queue_service.QUEUE_DIR / account_service.ACCOUNTS_FILE).read_text(encoding="utf-8"))
        harness.check("the right password opens a session of the account's role",
                      token and account_service.read_session(token) == {"name": "Jonas", "role": "operator"})
        harness.check("a wrong password or name opens none",
                      account_service.sign_in("Jonas", "wrong") is None and account_service.sign_in("Mara", "s3cret") is None)
        harness.check("the password is stored as a salted hash, never as itself", "s3cret" not in json.dumps(stored)
                      and set(stored["Jonas"]) >= {"salt", "hash", "role"})
        harness.check("the listing holds the name and role, never the hash",
                      account_service.list_accounts() == [{"name": "Jonas", "role": "operator",
                                                           "created": stored["Jonas"]["created"]}])

    _in_fresh_folder(check)


def test_a_session_ends_with_sign_out_a_new_password_or_its_account():
    def check():
        account_service.add_account("Jonas", "one", "admin")
        first, second = account_service.sign_in("Jonas", "one"), account_service.sign_in("Jonas", "one")
        account_service.sign_out(first)
        harness.check("signing out ends that session only",
                      account_service.read_session(first) is None and account_service.read_session(second) is not None)
        account_service.set_role("Jonas", "viewer")
        harness.check("a role changed is read by the live session", account_service.read_session(second)["role"] == "viewer")
        account_service.set_password("Jonas", "two")
        harness.check("a new password ends every session and opens with the new one only",
                      account_service.read_session(second) is None and account_service.sign_in("Jonas", "one") is None
                      and account_service.sign_in("Jonas", "two") is not None)
        third = account_service.sign_in("Jonas", "two")
        account_service.remove_account("Jonas")
        harness.check("a removed account's sessions end, and no account is left",
                      account_service.read_session(third) is None and not account_service.has_accounts())

    _in_fresh_folder(check)


def test_an_account_change_the_store_cannot_make_is_refused_by_name():
    def check():
        account_service.add_account("Jonas", "pw", "viewer")
        harness.check("a name taken, an unknown role, an empty name or password, an unknown account: each refused",
                      "exists" in _refuses(lambda: account_service.add_account("Jonas", "pw", "viewer"))
                      and "no role" in _refuses(lambda: account_service.add_account("Mara", "pw", "owner"))
                      and "name" in _refuses(lambda: account_service.add_account("  ", "pw", "viewer"))
                      and "password" in _refuses(lambda: account_service.add_account("Mara", "", "viewer"))
                      and "no account" in _refuses(lambda: account_service.remove_account("Mara")))

    _in_fresh_folder(check)


def _client():
    from fastapi.testclient import TestClient
    from services import web_api_service

    return TestClient(web_api_service.build_app())


def test_with_no_account_the_server_asks_nobody_to_sign_in():
    def check():
        client = _client()
        me = client.get("/api/auth/me").json()
        harness.check("a read and a change go through, and the page is told no accounts exist",
                      client.get("/api/accounts").status_code == 200 and me["has_accounts"] is False
                      and me["account"] is None)

    _in_fresh_folder(check)


def test_once_an_account_exists_each_route_needs_a_session_of_its_role():
    def check():
        for name, role in (("Vera", "viewer"), ("Otto", "operator"), ("Ada", "admin")):
            account_service.add_account(name, "pw", role)
        client = _client()
        signed_out = client.get("/api/accounts").status_code
        wrong = client.post("/api/auth/login", json={"name": "Vera", "password": "nope"}).status_code
        viewer = client.post("/api/auth/login", json={"name": "Vera", "password": "pw"})
        token = viewer.json()["token"]
        bearer = {"authorization": f"Bearer {token}"}
        reads = client.get("/api/profiles", headers=bearer).status_code
        change = client.put("/api/runs/output/x/tags", json={"tags": []}, headers=bearer).status_code
        harness.check("signed out: 401; a wrong password: 401; a viewer reads but may not change",
                      signed_out == 401 and wrong == 401 and reads == 200 and change == 403, str((signed_out, wrong, reads, change)))
        harness.check("the page's cookie carries the same session", client.get("/api/auth/me").json()["account"]
                      == {"name": "Vera", "role": "viewer", "groups": []})
        client.cookies.clear()
        operator = {"authorization": "Bearer " + client.post("/api/auth/login", json={"name": "Otto", "password": "pw"}).json()["token"]}
        harness.check("an operator may not read the accounts or change a setting",
                      client.get("/api/accounts", headers=operator).status_code == 403
                      and client.put("/api/settings", json={"key": "frames.mode", "value": "change"}, headers=operator).status_code == 403)
        admin = {"authorization": "Bearer " + client.post("/api/auth/login", json={"name": "Ada", "password": "pw"}).json()["token"]}
        added = client.post("/api/accounts", json={"name": "Lea", "password": "pw", "role": "viewer"}, headers=admin)
        changed = client.put("/api/accounts/Lea", json={"role": "operator"}, headers=admin)
        refused = client.post("/api/accounts", json={"name": "Lea", "password": "pw", "role": "viewer"}, headers=admin)
        harness.check("an admin adds and changes an account, and a name taken is refused with its reason",
                      added.status_code == 200 and {"name": "Lea", "role": "operator"}.items()
                      <= next(a for a in changed.json()["accounts"] if a["name"] == "Lea").items()
                      and refused.status_code == 400 and "exists" in refused.json()["detail"], refused.text)
        client.post("/api/auth/logout", headers=admin)
        harness.check("signed out, the token opens nothing", client.get("/api/profiles", headers=admin).status_code == 401)

    _in_fresh_folder(check)


def test_every_route_of_the_server_is_in_the_role_table():
    """A route the table did not know would need an operator by default; this names each route's role so a new route's
    role is a decision, read here, never an accident."""

    from fastapi.routing import APIRoute
    from utils import route_role_utils

    from services import web_api_service

    roles = {}
    for route in web_api_service.router.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in route.methods:
                roles[(method, route.path)] = route_role_utils.find_needed_role(method, route.path)
    open_routes = sorted(path for (method, path), role in roles.items() if role is None)
    admin_reads = sorted(path for (method, path), role in roles.items() if role == "admin" and method == "GET")
    viewer_changes = sorted(path for (method, path), role in roles.items() if role == "viewer" and method != "GET")
    harness.check("only signing in, out and asking who is signed in are open; an admin alone reads the accounts; no "
                  "change is a viewer's",
                  set(open_routes) == set(route_role_utils.OPEN_PATHS) and admin_reads == ["/api/accounts"]
                  and viewer_changes == [], str((open_routes, admin_reads, viewer_changes)))


def _run_cli(argv: list[str], typed: list[str]) -> int:
    """The command line run with `typed` as what getpass and input read, in order."""

    import builtins
    import getpass
    from cli import main

    answers = iter(typed)
    previous_getpass, previous_input = getpass.getpass, builtins.input
    getpass.getpass = lambda prompt="": next(answers)
    builtins.input = lambda prompt="": next(answers)
    try:
        return main(argv)
    finally:
        getpass.getpass, builtins.input = previous_getpass, previous_input


def test_the_command_line_makes_accounts_and_signs_in_with_a_kept_token():
    def check():
        from services import client_token_service
        from services import server_client_service

        made = _run_cli(["accounts", "add", "Ada", "--role", "admin"], ["pw", "pw"])
        mismatch = _run_cli(["accounts", "add", "Lea"], ["one", "two"])
        role = _run_cli(["accounts", "role", "Ada", "operator"], [])
        harness.check("an account is made, a mistyped password is refused, a role is changed",
                      made == 0 and mismatch == 2 and role == 0
                      and account_service.list_accounts()[0]["role"] == "operator" and len(account_service.list_accounts()) == 1)
        client = _client()
        server_client_service.set_http_client(client)
        try:
            signed_in = _run_cli(["login", "Ada"], ["pw"])
            token = client_token_service.read_client_token()
            client.headers.update(client_token_service.build_auth_headers())
            reads = client.get("/api/profiles").status_code
            signed_out = _run_cli(["logout"], [])
        finally:
            server_client_service.set_http_client(None)
        harness.check("login keeps a live token that the clients send; logout ends it and removes it",
                      signed_in == 0 and account_service.read_session(token) is None and reads == 200 and signed_out == 0
                      and client_token_service.read_client_token() == "", str((signed_in, reads, signed_out)))

    _in_fresh_folder(check)


def test_the_accounts_command_takes_only_the_known_roles():
    import argparse

    parser = argparse.ArgumentParser(exit_on_error=False)
    accounts_command.add_accounts_arguments(parser)
    made = parser.parse_args(["add", "Ada", "--role", "operator"])
    try:
        parser.parse_args(["add", "Ada", "--role", "owner"])
        unknown = ""
    except argparse.ArgumentError as exc:
        unknown = str(exc)
    harness.check("an account's role is one of the roles, viewer when none is named",
                  made.role == "operator" and parser.parse_args(["add", "Lea"]).role == "viewer" and "owner" in unknown,
                  unknown)
