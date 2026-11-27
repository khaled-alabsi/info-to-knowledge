"""`blocks --output <run folder>`: how often each step of phases 4 and 5 blocked, was accepted and was reset, with each
block's time and reason, read from the run's conversation logs. The detail view shows the same."""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from services import run_status_service
from utils import display_path_utils

__all__ = ["add_blocks_arguments", "render_step_outcomes", "cmd_blocks"]

# A block's time to the minute: "YYYY-MM-DDTHH:MM"
_MINUTE_STAMP_CHARS = 16


def add_blocks_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser)


def _render_block_time(stamp: str) -> str:
    """A block's time to the minute, with a space for the `T`; `no time` when it has none."""

    return stamp[:_MINUTE_STAMP_CHARS].replace("T", " ") or "no time"


def render_step_outcomes(steps: list[dict]) -> list[str]:
    """The console lines: per phase, its total, then each step that blocked or was reset with its counts and its blocks,
    and how many steps were accepted with no block."""

    lines = []
    for number in sorted({step["phase"] for step in steps}):
        in_phase = [step for step in steps if step["phase"] == number]
        troubled = [step for step in in_phase if step["blocked"] or step["reset"]]
        total = sum(step["blocked"] for step in in_phase)
        lines.append(f"phase {number}: {total} block(s) over {len(in_phase)} step(s)")
        width = max((len(step["step"]) for step in troubled), default=0)
        for step in troubled:
            lines.append(f"  {step['step']:<{width}}  blocked {step['blocked']}  accepted {step['accepted']}"
                         f"  reset {step['reset']}")
            lines += [f"    {_render_block_time(block['at']):<{_MINUTE_STAMP_CHARS}}  {block['summary']}"
                      for block in step["blocks"]]
        quiet = len(in_phase) - len(troubled)
        if quiet:
            lines.append(f"  {quiet} other step(s) with no block")

    return lines


def cmd_blocks(args: argparse.Namespace) -> int:
    """0 with the lines printed, 2 when the folder holds no phase 4 or 5 conversation."""

    output_root = args.output_root
    steps = run_status_service.list_step_outcomes(output_root)
    if not steps:
        args.logger.error(f"{display_path_utils.render_path(output_root)} holds no phase 4 or 5 conversation log")

        return 2
    args.logger.info("\n".join(render_step_outcomes(steps)))

    return 0
