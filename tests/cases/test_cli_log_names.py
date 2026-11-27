"""A launch's log says whether it is the first start of the run or a resume (the owner, 2026-09-29)."""

import tempfile
from datetime import datetime
from pathlib import Path

from support import harness

from services import cli_log_service


def test_the_first_start_is_plain_and_every_later_one_is_a_resume():
    run_dir = Path(tempfile.mkdtemp())
    first = cli_log_service.build_cli_log_path(run_dir, datetime(2026, 9, 29, 9, 44))
    harness.check("the first start is a plain file in the run folder", first == run_dir / "09-44.log", str(first))
    first.write_text("x", encoding="utf-8")
    second = cli_log_service.build_cli_log_path(run_dir, datetime(2026, 9, 29, 11, 21))
    harness.check("a later start says it is a resume, in the same folder", second == run_dir / "11-21-resume.log", str(second))
    second.write_text("x", encoding="utf-8")
    again = cli_log_service.build_cli_log_path(run_dir, datetime(2026, 9, 29, 11, 21))
    harness.check("the same minute twice gets a number", again == run_dir / "11-21-resume-2.log", str(again))
