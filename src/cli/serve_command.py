"""The `serve` command: the web page, the API both UIs use, and the one job queue that starts runs.

It listens on every address of this machine, so the page opens from the same network, unless `--host` says otherwise.
The recordings are confidential meetings: with sign-in off, an address beyond this machine prints one warning line and
goes ahead.
"""

from __future__ import annotations

import argparse
import os
import socket

import uvicorn

import config
from services import job_queue_service
from services import run_operations_service
from services import server_client_service
from services import web_api_service

__all__ = ["add_serve_arguments", "cmd_serve"]

# A stop waits this long for the requests still being answered, then closes them.
_SHUTDOWN_WAIT_S = 2


def add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    """Where the server listens."""

    parser.add_argument("--host", default=config.SERVE_LISTEN_HOST,
                        help=f"the address to listen on (default: {config.SERVE_LISTEN_HOST}, this machine and its "
                             f"network; {config.SERVER_HOST} for this machine only)")
    parser.add_argument("--port", type=int, default=config.SERVER_PORT, help=f"the port (default: {config.SERVER_PORT})")
    parser.add_argument("--fd", dest="socket_fd", type=int, default=None,
                        help="serve on this already-listening socket instead, as `i2k tui` hands one over")
    parser.add_argument("--sign-in", dest="sign_in", choices=("on", "off"), default=os.environ.get("I2K_SIGN_IN", "on"),
                        help="on: everyone signs in once an account exists, and projects and roles apply; off: nobody "
                             "signs in and every project is open to all (default: I2K_SIGN_IN, else on)")


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the queue's worker and serve until stopped. Running jobs keep going after the server stops."""

    web_api_service.set_sign_in(args.sign_in == "on")
    if args.host not in config.LOCAL_HOSTS and args.sign_in == "off":
        args.logger.warning(f"listening on {args.host} with sign-in off: anyone who reaches this port can upload, run "
                            "and reset")
    # The port is bound before the queue's worker starts: a second `serve` on a port that is taken must not start a
    # queued job and then exit
    listener: socket.socket | None = None
    if args.socket_fd is not None:
        listen_fd, address_text = args.socket_fd, f"the socket handed over (fd {args.socket_fd})"
    else:
        try:
            listener = socket.create_server((args.host, args.port),
                                            family=server_client_service.pick_address_family(args.host))
        except OSError as error:
            args.logger.error(f"could not listen on {server_client_service.format_url_host(args.host)}:{args.port}: {error}")

            return 2
        listen_fd, address_text = listener.fileno(), f"http://{server_client_service.format_url_host(args.host)}:{args.port}"
    # a rename the Manager asked while a run wrote into its folder is made when the run's last try ends
    job_queue_service.set_rename_after_run(run_operations_service.rename_after_run)
    job_queue_service.start_worker(job_queue_service.QUEUE_DIR)
    if not web_api_service.WEB_DIST_DIR.is_dir():
        args.logger.warning("the web page is not built: run `npm install && npm run build` in web/")
    args.logger.info(f"serving on {address_text}  (Ctrl-C stops the server, not a running job)")
    try:
        uvicorn.run(web_api_service.build_app(), fd=listen_fd, log_level="warning",
                    timeout_graceful_shutdown=_SHUTDOWN_WAIT_S)
    finally:
        if listener is not None:
            listener.close()

    return 0
