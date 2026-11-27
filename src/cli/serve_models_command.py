"""The `serve-models` command: the decision model d1-2B served natively on this machine, for `providers.decision.host`.

It listens on this machine only, unless `--host` says otherwise: the frames sent to it come from confidential meetings, and
it has no sign-in.
"""

from __future__ import annotations

import argparse

from services import decision_model_server_service

__all__ = ["add_serve_models_arguments", "cmd_serve_models"]

DEFAULT_PORT = 8790
LOCAL_HOST = "127.0.0.1"


def add_serve_models_arguments(parser: argparse.ArgumentParser) -> None:
    """Where the decision model's server listens."""

    parser.add_argument("--host", default=LOCAL_HOST,
                        help=f"the address to listen on (default: {LOCAL_HOST}, this machine only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"the port (default: {DEFAULT_PORT})")


def cmd_serve_models(args: argparse.Namespace) -> int:
    """Load the decision model and serve it until stopped."""

    args.logger.info(f"loading {decision_model_server_service.MODEL_ID} (the first start downloads about 4.5 GB) ...")
    try:
        ask = decision_model_server_service.load_model_asker()
        server = decision_model_server_service.build_server(host=args.host, port=args.port, ask=ask)
    except (RuntimeError, OSError) as error:
        args.logger.error(f"could not start the decision model's server: {error}")

        return 2
    args.logger.info(f"serving the decision model on http://{args.host}:{args.port}/v1  "
                     f"(add it to Hosts and name it in providers.decision.host; Ctrl-C stops it)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0
