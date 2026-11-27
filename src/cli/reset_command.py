"""`i2k reset-to N --output <run folder>`: every phase after N removed from a run folder, its files and its progress,
as if it had never run. `i2k reset-chapter N --output <run folder>`: chapter N and every later chapter removed from
phase 4, written again on the next `--resume`. Without `--yes` each only lists what it would remove."""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from services import cli_log_service
from services import run_operations_service

__all__ = ["add_reset_to_arguments", "add_reset_chapter_arguments", "cmd_reset_to", "cmd_reset_chapter"]


def add_reset_to_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("phase", type=int, metavar="N",
                        help="the last phase to keep; every phase after it is removed. 0 removes every phase")
    run_folder_argument_builder.add_run_folder_argument(parser, "the run folder to reset, as `i2k run --output` names it")
    run_folder_argument_builder.add_confirm_argument(
        parser, "remove them; without it the command only lists what it would remove")


def cmd_reset_to(args: argparse.Namespace) -> int:
    """0 on a reset or a listing, 2 when the line cannot be carried out."""

    result = run_operations_service.reset_run_to_phase(args.output_root, args.phase,
                                                       args.is_confirmed)

    return cli_log_service.log_operation_result(args.logger, result)


def add_reset_chapter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("chapter", type=int, metavar="N",
                        help="the first chapter to write again; it and every later chapter are removed from phase 4")
    run_folder_argument_builder.add_run_folder_argument(parser)
    run_folder_argument_builder.add_confirm_argument(
        parser, "remove them; without it the command only lists what it would remove")
    parser.add_argument("--text-only", dest="is_text_only", action="store_true",
                        help="keep the draft headings of chapter N and after; remove only their texts and the final parts")


def cmd_reset_chapter(args: argparse.Namespace) -> int:
    """0 on a reset or a listing, 2 when the line cannot be carried out. A chapter whose notes changed after it was
    written is asked again from its draft headings on the next `--resume`; the chapters before it stay accepted."""

    result = run_operations_service.reset_run_from_chapter(args.output_root, args.chapter,
                                                           args.is_confirmed, args.is_text_only)

    return cli_log_service.log_operation_result(args.logger, result)
