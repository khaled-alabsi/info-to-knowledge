"""The `inbox` command: run the pipeline over every dropped video that has no document yet.

ONE IMPLEMENTATION OF WHAT A RUN IS. This command decides WHICH videos to run and in what order,
and nothing else. Each one is then handed to `run_command.cmd_run` through the same arguments a
hand-typed `run` line would produce, so there is no second copy of the run logic to drift from the
first, and everything `run` refuses, `inbox` refuses identically.

ONE FAILURE DOES NOT STOP THE BATCH. A video that fails is reported by name and the next one starts,
because stopping would discard every item still queued behind the one that failed. The exit code is
non-zero when any item failed, so a scheduled invocation still reports the batch honestly.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import config
from cli import run_command
from models import run_job
from services import inbox_service
from utils import display_path_utils

__all__ = ["cmd_inbox", "add_inbox_arguments"]


def _build_run_args(args: argparse.Namespace,
                    video: Path,
                    run_folder: Path) -> argparse.Namespace:
    """The `run` command line this item would have been given by hand: every phase and no `--resume`, so a
    half-finished item starts again from phase 1."""

    parser = argparse.ArgumentParser(add_help=False)
    run_command.add_run_arguments(parser)
    line = ["--video", str(video), "--output", str(run_folder)]
    if args.is_dry_run:
        line.append("--dry-run")
    if args.is_forced:
        line.append("--force")
    run_args = parser.parse_args(line)
    run_args.logger = args.logger

    return run_args


def _report_plan(args: argparse.Namespace,
                 inbox_root: Path,
                 documents_root: Path,
                 new_videos: list[Path]) -> None:
    """What the command is about to do, before it does any of it."""

    total = len(inbox_service.list_inbox_videos(inbox_root))
    args.logger.info(f"inbox  : {display_path_utils.render_path(inbox_root)}")
    args.logger.info(f"output : {display_path_utils.render_path(documents_root)}")
    args.logger.info(f"{len(new_videos)} new of {total} video(s) in the inbox")
    for position, video in enumerate(new_videos, start=1):
        args.logger.info(f"  {position}. {video.name}")


def add_inbox_arguments(parser: argparse.ArgumentParser) -> None:
    """The flags of the `inbox` command. BOTH FOLDERS DEFAULT, unlike `run`: they are fixed places in
    this project rather than something the caller chooses per video, and the command prints both
    before it starts so a wrong one is visible rather than discovered afterwards."""

    parser.add_argument("--inbox", dest="inbox_root", type=Path, default=config.INBOX_DIR,
                        help=f"the folder videos are dropped into (default: {config.INBOX_DIR.name}/)")
    parser.add_argument("--output", dest="documents_root", type=Path, default=config.DOCUMENT_ROOT,
                        help=f"the folder ONE RUN FOLDER PER VIDEO is written under "
                             f"(default: {config.DOCUMENT_ROOT.name}/)")
    parser.add_argument("--list", dest="is_listing_only", action="store_true",
                        help="print what is new and stop, running nothing")
    run_command.add_run_switches(parser)


def cmd_inbox(args: argparse.Namespace) -> int:
    """Run every new inbox video to the end. 0 when all of them finished or the inbox was only listed, 1 when any item
    failed, 130 when one was stopped (the items after it are not started)."""

    inbox_root = args.inbox_root.expanduser().resolve()
    documents_root = args.documents_root.expanduser().resolve()
    new_videos = inbox_service.list_new_videos(inbox_root, documents_root)
    _report_plan(args, inbox_root, documents_root, new_videos)
    if args.is_listing_only or not new_videos:
        return 0

    failed = []
    for position, video in enumerate(new_videos, start=1):
        run_folder = inbox_service.build_run_folder(documents_root, video)
        args.logger.info(f"\n=== {position}/{len(new_videos)}  {video.name} ===")
        try:
            code = run_command.cmd_run(_build_run_args(args, video, run_folder))
        except Exception:  # noqa: BLE001 (one item's error is that item's failure, never the batch's end)
            args.logger.exception(f"{video.name}: stopped on an error")
            code = 1
        if code == run_job.EXIT_INTERRUPTED:
            args.logger.warning(f"{video.name}: stopped; the {len(new_videos) - position} item(s) after it are not started")

            return run_job.EXIT_INTERRUPTED
        if code != 0:
            failed.append(video.name)
            args.logger.error(f"{video.name}: did not finish, moving to the next item")

    args.logger.info(f"\ninbox done: {len(new_videos) - len(failed)} finished, {len(failed)} failed")
    for name in failed:
        args.logger.error(f"  failed: {name}")

    return 1 if failed else 0
