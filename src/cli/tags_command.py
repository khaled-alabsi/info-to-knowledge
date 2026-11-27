"""`tags --output <run folder> [--add TAG ...] [--remove TAG ...] [--set TAG ...]`: the tags the owner gives a run,
the same ones the detail view shows and changes. With no change asked for, it prints them."""

from __future__ import annotations

import argparse

import config

from cli import run_folder_argument_builder
from services import cli_log_service
from services import run_operations_service
from services import run_tagging_service

__all__ = ["add_tags_arguments", "cmd_tags", "cmd_apply_tag_rules", "cmd_tag_runs"]


def add_tags_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser)

    parser.add_argument("--add", dest="added_tags", nargs="+", default=[], metavar="TAG", help="tags to add after the ones it has")
    parser.add_argument("--remove", dest="removed_tags", nargs="+", default=[], metavar="TAG", help="tags to take off, letter case ignored")
    parser.add_argument("--set", dest="replacement_tags", nargs="*", default=None, metavar="TAG",
                        help="every tag the run is to have, replacing the ones it has; no TAG takes them all off")


def cmd_tags(args: argparse.Namespace) -> int:
    """0 with the run's tags printed, 2 when the folder does not exist."""

    result = run_operations_service.set_run_tags(args.output_root, args.added_tags, args.removed_tags,
                                                 args.replacement_tags)

    return cli_log_service.log_operation_result(args.logger, result)


def cmd_apply_tag_rules(args: argparse.Namespace) -> int:
    """`apply-tag-rules`: every run folder under output/ and knowledge/ given the tags the naming rules
    (`tags.nameRules`) give its recording's name; 0 with each folder tagged printed."""

    return cli_log_service.log_operation_result(args.logger, run_operations_service.apply_tag_rules(
        [config.DOCUMENT_ROOT, config.WORKSPACE_ROOT / "knowledge"]))


def cmd_tag_runs(args: argparse.Namespace) -> int:
    """`tag-runs`: every finished run under output/ and knowledge/ with no worker tags given up to `tags.workerTags`,
    one call each on phase 4's model; run it only while no other run uses the model."""

    return cli_log_service.log_operation_result(args.logger, run_tagging_service.add_finished_run_tags(
        [config.DOCUMENT_ROOT, config.WORKSPACE_ROOT / "knowledge"], args.logger.info))
