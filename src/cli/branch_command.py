"""`branch --output <run folder> --from-phase N`: a new run folder beside the run, holding its progress before phase N.
The run branched from is only read; `i2k run --resume` on the new folder runs phase N on. The
page's Branch does the same."""

from __future__ import annotations

import argparse

from cli import run_folder_argument_builder
from models import output_contract
from models.input_range_spec import InputRangeSpec
from services import cli_log_service
from services import input_range_service
from services import run_operations_service

__all__ = ["add_branch_arguments", "cmd_branch"]


def add_branch_arguments(parser: argparse.ArgumentParser) -> None:
    run_folder_argument_builder.add_run_folder_argument(parser, "the run folder to branch, as `i2k run --output` names it")
    parser.add_argument("--from-phase", dest="phase", type=int, required=True,
                        help="the phase the branch runs again: it keeps the phases before it")
    parser.add_argument("--pdf-mode", dest="pdf_mode", default="", choices=output_contract.PDF_MODES,
                        help="a PDF run's processing mode in the branch, from phase 2 only: pages-as-images (each page "
                             "a picture, read in batches), text-only (its text alone, no picture) or markdown-conversion "
                             "(its text converted to markdown, no model at all)")
    parser.add_argument("--start", dest="range_start", default="", metavar="TIME",
                        help="the branch processes its recording from this time on, from phase 1 only")
    parser.add_argument("--end", dest="range_end", default="", metavar="TIME",
                        help="the branch processes its recording up to this time, from phase 1 only")
    parser.add_argument("--pages", dest="range_pages", default="", metavar="FIRST-LAST",
                        help="the branch processes only these pages of its PDF, from phase 1 only")
    parser.add_argument("--whole", dest="is_whole", action="store_true",
                        help="the branch processes the whole input again, after a run of a part, from phase 1 only")


def cmd_branch(args: argparse.Namespace) -> int:
    """0 with the new folder printed, 2 when the folder, the phase or the range is refused."""

    try:
        input_range = input_range_service.build_range(start=getattr(args, "range_start", ""),
                                                      end=getattr(args, "range_end", ""),
                                                      pages=getattr(args, "range_pages", ""))
    except ValueError as exc:
        args.logger.error(str(exc))

        return 2
    is_whole = getattr(args, "is_whole", False)
    if is_whole and input_range is not None:
        args.logger.error("--whole processes the whole input: give no --start, --end or --pages with it")

        return 2
    branch_range = InputRangeSpec() if is_whole else input_range
    result = run_operations_service.create_branch(output_root=args.output_root,
                                                  phase=args.phase,
                                                  pdf_mode=getattr(args, "pdf_mode", ""),
                                                  input_range=branch_range)

    return cli_log_service.log_operation_result(args.logger, result)
