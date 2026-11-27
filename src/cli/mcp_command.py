"""`i2k mcp`: serve the program's controls as MCP tools over stdio, for an agent. The tools call
the running `i2k serve`, so start that first; `--server` or `I2K_SERVER_URL` names it when it is not on this machine's
default port.

Register it with an agent, for Claude Code: `claude mcp add i2k -- <playbook>/i2k mcp`.
"""

from __future__ import annotations

import argparse

from services import mcp_server_service
from services import server_client_service

__all__ = ["add_mcp_arguments", "cmd_mcp"]


def add_mcp_arguments(parser: argparse.ArgumentParser) -> None:
    mcp_server_service.add_mcp_arguments(parser)


def cmd_mcp(args: argparse.Namespace) -> int:
    """0 when the agent closes the connection."""

    if args.server_url:
        server_client_service.set_server_url(args.server_url)
    # Standard output carries the MCP messages: `cli.main` has sent every log line to standard error
    mcp_server_service.build_mcp_server().run("stdio")

    return 0
