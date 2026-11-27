"""`recording-to-inbox --output <run folder>`: move the recording a done run keeps in its folder back into the inbox,
so phases 1 and 2 can run again; `recording-to-run --output <run folder>`: keep a done run's inbox recording in its
folder, as the run does when it ends; `compress-recording --output <run folder>`:
re-encode the recording a done run keeps to 720p in place."""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from services import cli_log_service
from services import run_operations_service

__all__ = ["add_recording_arguments", "cmd_recording_to_inbox", "cmd_recording_to_run", "cmd_compress_recording"]


def add_recording_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser)


def cmd_recording_to_inbox(args: argparse.Namespace) -> int:
    """0 when the recording is back in the inbox, 2 when the line cannot be carried out."""

    result = run_operations_service.move_recording_to_inbox(args.output_root)

    return cli_log_service.log_operation_result(args.logger, result)


def cmd_recording_to_run(args: argparse.Namespace) -> int:
    """0 when the recording is kept in the run folder, 2 when the line cannot be carried out."""

    result = run_operations_service.keep_recording_in_run(args.output_root)

    return cli_log_service.log_operation_result(args.logger, result)


def cmd_compress_recording(args: argparse.Namespace) -> int:
    """0 when every video of the kept recording is compressed or kept as it was, 1 when ffmpeg failed, 2 when the line
    cannot be carried out. Each line is printed as it happens; the result's lines are not printed again."""

    result = run_operations_service.shrink_kept_recording(args.output_root, args.logger.info)
    if result.exit_code != 0:
        args.logger.error(result.lines[-1])

    return result.exit_code
