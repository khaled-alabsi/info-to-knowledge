# <!-- sync: seed/cli.setup_commands v2 -->
"""`models` and `probe` - what someone runs before trusting a run.

Both answer the same question from opposite ends: `models` says what each phase's model RESOLVED to from the
configuration, and `probe` says whether the hosts those resolutions name are actually answering.
Neither starts a run, neither writes anything, and neither needs a repository - so they are the two
commands to reach for when a run behaves in a way the configuration does not explain.

Printing only. Every fact either one shows comes from `services/chat_model_service.py`, which is the
single place a model is resolved; a second resolution here would be a second answer to "what is
going to run".
"""

from __future__ import annotations

import argparse

from services import run_operations_service

__all__ = ["cmd_models", "cmd_probe"]


def cmd_models(args: argparse.Namespace) -> int:
    """Each phase that calls a model, what its model resolved to, and every configured model by name."""

    result = run_operations_service.describe_models()
    print(result.output)

    return result.exit_code


def cmd_probe(args: argparse.Namespace) -> int:
    """Whether each configured host answers, and what it has loaded if it does. The exit code is what a script reads:
    non-zero if any configured host is down."""

    result = run_operations_service.probe_model_hosts()
    print(result.output)

    return result.exit_code
