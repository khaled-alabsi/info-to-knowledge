"""`i2k tidy-cuts`: the cuts a run folder never placed, moved into its `.trash/cuts/`.

A run that finishes does this as its last step. The command is for the run folders that finished before it existed,
and for a folder whose figures changed by hand since.
"""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from services import cli_log_service
from services import run_operations_service

__all__ = ["add_tidy_cuts_arguments", "cmd_tidy_cuts"]


def add_tidy_cuts_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser)
    run_folder_argument_builder.add_confirm_argument(
        parser, "move them; without it the command only lists what it would move")


def cmd_tidy_cuts(args: argparse.Namespace) -> int:
    """0 on a move or a listing, 2 when the line cannot be carried out."""

    result = run_operations_service.move_unused_cuts_aside(args.output_root, args.is_confirmed)

    return cli_log_service.log_operation_result(args.logger, result)
