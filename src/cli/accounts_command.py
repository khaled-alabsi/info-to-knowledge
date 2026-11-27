"""`accounts list | add NAME --role R | remove NAME | role NAME R | password NAME`, `login` and `logout`: the accounts of
`i2k serve` and this machine's sign-in to it.

`accounts` works on the account files directly, so the first admin is made before any server runs. A password is read
with `getpass`, twice where it is new, and never taken from the command line, where every process list shows it.
`login` signs in to the running server and keeps the session's token for the command line's clients.
"""

from __future__ import annotations

import argparse
import getpass

from models import account_role_vocabulary
from models.ui_errors import AccountError
from services import account_service
from services import client_token_service
from services import server_client_service

__all__ = ["add_accounts_arguments", "add_login_arguments", "cmd_accounts", "cmd_login", "cmd_logout"]

_ACTIONS = ("list", "add", "remove", "role", "password")


def add_accounts_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=_ACTIONS, help="what to do with the accounts")
    parser.add_argument("name", nargs="?", default="", help="the account's name (not for `list`)")
    parser.add_argument("new_role", nargs="?", default="", choices=("", *account_role_vocabulary.ROLES),
                        metavar="ROLE", help="the new role, for `role`")
    parser.add_argument("--role", dest="role", default=account_role_vocabulary.VIEWER,
                        choices=account_role_vocabulary.ROLES, help="the role of an account `add` makes")


def add_login_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", nargs="?", default="", help="the account's name; asked when left out")


def _read_new_password() -> str:
    """A new password typed twice; an empty or mismatched pair is refused by name."""

    first = getpass.getpass("password: ")
    if first != getpass.getpass("again: "):
        raise AccountError("the two passwords differ")

    return first


def _run_action(args: argparse.Namespace) -> list[str]:
    """The lines an action prints, after doing it."""

    if args.action == "list":
        return [f"{account['name']}  {account['role']}  since {account['created']}"
                for account in account_service.list_accounts()] or ["no account: i2k serve asks nobody to sign in"]
    if not args.name:
        raise AccountError(f"`accounts {args.action}` needs the account's name")
    if args.action == "add":
        account_service.add_account(args.name, _read_new_password(), args.role)
        return [f"account {args.name} added as {args.role}"]
    if args.action == "remove":
        account_service.remove_account(args.name)
        return [f"account {args.name} removed, its sessions ended"]
    if args.action == "role":
        if not args.new_role:
            raise AccountError("`accounts role NAME ROLE` needs the new role")
        account_service.set_role(args.name, args.new_role)
        return [f"account {args.name} is {args.new_role} now"]
    account_service.set_password(args.name, _read_new_password())

    return [f"account {args.name} has a new password, its sessions ended"]


def cmd_accounts(args: argparse.Namespace) -> int:
    """0 with what was done printed, 2 when the accounts refuse it."""

    try:
        lines = _run_action(args)
    except AccountError as exc:
        args.logger.error(str(exc))
        return 2
    for line in lines:
        args.logger.info(line)

    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """0 once signed in to the running server, its token kept for the command line; 2 for a wrong name or password,
    or no server."""

    name = args.name or input("name: ")
    try:
        answer = server_client_service.call_server("POST", "/auth/login",
                                                   json={"name": name, "password": getpass.getpass("password: ")})
    except RuntimeError as exc:
        args.logger.error(str(exc))
        return 2
    path = client_token_service.write_client_token(str(answer["token"]))
    args.logger.info(f"signed in as {answer['name']} ({answer['role']}); the token is kept in {path}")

    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    """0 once the session is ended on the server, when one answers, and the kept token removed."""

    try:
        server_client_service.call_server("POST", "/auth/logout")
    except RuntimeError as exc:
        args.logger.warning(f"the server did not end the session ({exc}); the token is removed here")
    client_token_service.write_client_token("")
    args.logger.info("signed out")

    return 0
