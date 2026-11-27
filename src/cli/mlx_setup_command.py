"""`setup-mlx [MODEL]`: make mlx-vlm usable on this Mac, in a venv of its own, and download MODEL when named.

The steps and their order live in `services/mlx_setup_service.py`; this module only reads the command line.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import config
from services import mlx_setup_service
from services import setup_check_service

__all__ = ["add_setup_mlx_arguments", "cmd_setup_mlx"]


def add_setup_mlx_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("model", nargs="?", default=None,
                        help="a Hugging Face model id to download for mlx-vlm; without it only mlx-vlm is set up")
    parser.add_argument("--venv", dest="venv_dir", type=Path, default=mlx_setup_service.DEFAULT_VENV,
                        help=f"the venv to create when mlx-vlm is not installed (default {mlx_setup_service.DEFAULT_VENV})")


def cmd_setup_mlx(args: argparse.Namespace) -> int:
    """0 when mlx-vlm imports (and the model, when named, is downloaded); 2 when the machine lacks what it needs; 1
    when an install or download step failed. Refused with 2 in a container: mlx-vlm needs Apple silicon."""

    if config.IS_CONTAINER_MODE:
        print(setup_check_service.describe_container_refusal("Setting up mlx-vlm"))

        return 2

    return mlx_setup_service.set_up_mlx(args.model, args.venv_dir.expanduser(),
                                        mlx_setup_service.build_system_context())
