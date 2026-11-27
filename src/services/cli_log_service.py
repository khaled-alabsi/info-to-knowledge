# <!-- sync: twin/services.cli_log_service v1 -->
"""The run's console, saved as it is written - one file per process start, never reused.

The supervisor's redirect of stdout was the only copy of what the console said, and it lived outside
the artifact tree, under whichever wrapper happened to launch the process. A process that was killed
and resumed appended to the same file, so a reader could not tell where one life of the run ended
and the next began. Here every start of `run` opens its own file under the run's own directory -
`logs/<runId>/HH-MM.log`, and `logs/<runId>/HH-MM-resume.log` for a start after the first - and every line the console
gets is written to it the moment it is logged, so a reader can `tail -f` the file while the run is alive.

A new run gets a new run directory; a resume shares the directory and gets a new file. A start in the
same minute as an earlier one - a crash loop - gets a `-2`, `-3` suffix rather than the earlier file.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from models.operation_result import OperationResult

__all__ = ["log_operation_result", "build_cli_log_path", "open_cli_log", "close_cli_log"]

# The console dims its context labels with colour codes when stdout is a terminal. A file is read in
# an editor, days later, and escape codes there are noise in the middle of every line.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


class _PlainTextFormatter(logging.Formatter):
    def format(self,
               record: logging.LogRecord) -> str:
        return _ANSI_ESCAPE.sub("", super().format(record))


def build_cli_log_path(run_dir: Path,
                       now: datetime) -> Path:
    """`<run_dir>/HH-MM.log` for the first start of this run, `<run_dir>/HH-MM-resume.log` for every later one, named
    by the minute it began, suffixed `-2`, `-3`... when that name is taken. The files sit directly in the run's log
    folder, with no subfolder."""

    stamp = now.strftime("%H-%M") + ("-resume" if any(run_dir.glob("*.log")) else "")
    path = run_dir / f"{stamp}.log"
    ordinal = 2
    while path.exists():
        path = run_dir / f"{stamp}-{ordinal}.log"
        ordinal += 1

    return path


def open_cli_log(run_dir: Path,
                 logger: logging.Logger,
                 now: datetime | None = None) -> logging.FileHandler:
    """Start saving the console to a fresh file; the handler's `baseFilename` is where. `logger` is the one the console
    writes to: a handler on any other saves an empty file."""

    path = build_cli_log_path(run_dir, now or datetime.now())
    path.parent.mkdir(parents=True, exist_ok=True)
    # `FileHandler` flushes after every record, which is what makes the file readable while the run
    # is alive - a buffered writer would show an empty file until the process ended.
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(_PlainTextFormatter("%(message)s"))
    logger.addHandler(handler)

    return handler


def close_cli_log(handler: logging.FileHandler,
                  logger: logging.Logger) -> None:
    """Stop saving: the file keeps what it has, and later lines go to the console alone."""

    logger.removeHandler(handler)
    handler.close()


def log_operation_result(log: logging.Logger,
                         result: OperationResult) -> int:
    """A backend operation's lines on the console, as one message (an error when it was refused or failed), and its
    exit code for the command to return."""

    if result.lines:
        (log.info if result.exit_code == 0 else log.error)(result.output)

    return result.exit_code
