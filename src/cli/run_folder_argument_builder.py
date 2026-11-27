"""The `--output` argument every command over one run folder takes, read as the folder's resolved path."""

from __future__ import annotations

import argparse
from pathlib import Path

__all__ = ["add_run_folder_argument", "add_confirm_argument", "RUN_FOLDER_HELP"]

RUN_FOLDER_HELP = "the run folder, as `i2k run --output` names it"


def _resolve_run_folder(folder_text: str) -> Path:
    return Path(folder_text).expanduser().resolve()


def add_run_folder_argument(parser: argparse.ArgumentParser,
                            help_text: str = RUN_FOLDER_HELP) -> None:
    """`--output`, required, into `args.output_root` as the resolved folder."""

    parser.add_argument("--output", dest="output_root", type=_resolve_run_folder, required=True, help=help_text)


def add_confirm_argument(parser: argparse.ArgumentParser,
                         help_text: str) -> None:
    """`--yes`, into `args.is_confirmed`: without it a command that changes a run folder only lists what it would do."""

    parser.add_argument("--yes", dest="is_confirmed", action="store_true", help=help_text)
