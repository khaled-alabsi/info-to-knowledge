"""`i2k refine-frames --output <run folder>`: the frame knowledge classifier's decision on every frame phase 3 has not
read, listed; nothing changes, unless `--yes`: then every frame it proposes is dropped. `i2k drop-frames frame-003 frame-007 --output <run folder> --yes`: those frames dropped
from the run before phase 3 reads them. Without `--yes` it only lists what it would drop."""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from services import cli_log_service
from services import run_operations_service

__all__ = ["add_refine_frames_arguments", "add_drop_frames_arguments", "cmd_refine_frames", "cmd_drop_frames"]


def add_refine_frames_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser)
    run_folder_argument_builder.add_confirm_argument(
        parser, "drop every frame it proposes; without it the command only lists them")


def cmd_refine_frames(args: argparse.Namespace) -> int:
    """0 on a listing, 1 when the classifier failed, 2 when the folder cannot be refined."""

    result = run_operations_service.list_or_drop_proposed_frames(args.output_root, args.is_confirmed)

    return cli_log_service.log_operation_result(args.logger, result)


def add_drop_frames_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("frames", nargs="+", metavar="FRAME", help="the frames to drop, by slug: frame-003")
    run_folder_argument_builder.add_run_folder_argument(parser)
    run_folder_argument_builder.add_confirm_argument(
        parser, "drop them; without it the command only lists what it would drop")


def cmd_drop_frames(args: argparse.Namespace) -> int:
    """0 on a drop or a listing, 2 when a frame or the folder cannot be dropped from."""

    result = run_operations_service.drop_run_frames(args.output_root, args.frames,
                                                    args.is_confirmed)

    return cli_log_service.log_operation_result(args.logger, result)
