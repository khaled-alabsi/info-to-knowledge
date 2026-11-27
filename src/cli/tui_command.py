"""The `tui` command: the terminal UI, talking to the server `serve` runs.

When no server answers, it starts one in the background on this machine (its own console in
`ui/server.log`), so one command opens the terminal UI. That server keeps running after the terminal
UI closes, with the job queue it holds.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from urllib.parse import urlsplit

import config
from services import job_queue_service
from services import server_client_service
from services import ui_api_client
from tui import console_tui_runner
from utils import display_path_utils

__all__ = ["add_tui_arguments", "cmd_tui"]

_SERVER_START_TIMEOUT_S = 20.0
_SERVER_LOG = job_queue_service.QUEUE_DIR / "server.log"


def _start_local_server(host: str,
                        port: int) -> str | None:
    """A `serve` in the background on a socket this process binds, answering within the start limit. None when it
    answers; else why not, in words."""

    # The socket listens from the moment it is bound here, so a request sent at once waits in its queue until the
    # server accepts it: one request with the start limit as its timeout is the readiness check, with no polling. A
    # server that dies closes the socket's last copy, and the request fails at once.
    _SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        listener = socket.create_server((host, port), family=server_client_service.pick_address_family(host))
    except OSError as error:
        return f"could not listen on {host}:{port}: {error}"
    command = [sys.executable, str(config.PLAYBOOK_ROOT / "main.py"), "serve", "--fd", str(listener.fileno())]
    with listener, _SERVER_LOG.open("ab") as console:
        subprocess.Popen(command, cwd=config.PLAYBOOK_ROOT, stdout=console, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True, pass_fds=(listener.fileno(),))
    if ui_api_client.is_server_up(f"http://{server_client_service.format_url_host(host)}:{port}", timeout_s=_SERVER_START_TIMEOUT_S):
        return None

    return (f"the server did not answer within {_SERVER_START_TIMEOUT_S:.0f}s; see "
            f"{display_path_utils.render_path(_SERVER_LOG)}")


def add_tui_arguments(parser: argparse.ArgumentParser) -> None:
    """Which server to talk to."""

    parser.add_argument("--server", default=os.environ.get(server_client_service.SERVER_URL_ENV,
                                                          server_client_service.DEFAULT_SERVER_URL),
                        help=f"the server's address (default: ${server_client_service.SERVER_URL_ENV} or this "
                             f"machine's, started when none answers)")


def cmd_tui(args: argparse.Namespace) -> int:
    """Open the terminal UI; 2 when no server answers and none could be started."""

    # One address for the check, the start and the UI: a `--server` with no scheme is http, and one with no port means
    # the server's default port
    server = args.server.rstrip("/")
    address = urlsplit(server if "://" in server else f"http://{server}")
    port = address.port or config.SERVER_PORT
    host = address.hostname or config.SERVER_HOST
    base_url = f"{address.scheme}://{server_client_service.format_url_host(host)}:{port}"
    if not ui_api_client.is_server_up(base_url):
        if host not in config.LOCAL_HOSTS:
            args.logger.error(f"no server answers at {base_url}")

            return 2
        args.logger.info(f"no server at {base_url}; starting one "
                         f"(console: {display_path_utils.render_path(_SERVER_LOG)})")
        refusal = _start_local_server(host, port)
        if refusal:
            args.logger.error(refusal)

            return 2
    console_tui_runner.ConsoleApp(base_url).run()

    return 0
