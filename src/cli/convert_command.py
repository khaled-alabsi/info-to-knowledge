"""`convert --output <run folder> --to <ingesting profile>`: a finished run converted to another ingesting profile, as a new run
folder beside it. Phases 1 to 3 are copied, or 1 and 2 when an audio recording's two
profiles prepare its notes in other steps; the printed `run` line writes the later phases there. The run converted from is only read. The page's Convert does the same and queues that line."""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from services import cli_log_service
from services import ingesting_profile_service
from services import run_operations_service

__all__ = ["add_convert_arguments", "cmd_convert"]


def add_convert_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser, "the run folder to convert, as `i2k run --output` names it")
    # `--to`, never `--profile`: every command takes `--profile` for a settings file
    parser.add_argument("--to", dest="ingesting_profile", required=True,
                        choices=ingesting_profile_service.list_profile_names(),
                        help="the ingesting profile the new document is written in")
    parser.add_argument("--in-place", dest="is_in_place", action="store_true",
                        help="a finished audio run only: write the new document in the same run folder, keeping the "
                             "document it has as a version; for a profile that reads the same notes")


def cmd_convert(args: argparse.Namespace) -> int:
    """0 with the new folder and its run line printed, 2 when the folder, its recording or the profile is refused."""

    result = (run_operations_service.write_profile_in_place(args.output_root, args.ingesting_profile) if args.is_in_place
              else run_operations_service.copy_run_for_profile(args.output_root, args.ingesting_profile))

    return cli_log_service.log_operation_result(args.logger, result)
