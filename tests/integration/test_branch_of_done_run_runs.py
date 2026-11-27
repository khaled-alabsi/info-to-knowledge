"""A done run branched from phase 2 by the real branch service, as `i2k branch` does (DEFECTS 270): froscon talk 3211,
done, was branched, and the run line the branch printed was refused as a recording processed already, naming the run
it was branched from. Made data: an invented recording's bytes and a done run folder."""

import tempfile
from pathlib import Path

from support import harness
from cases import test_duplicate_recordings as duplicates
from cli import run_command
from services import run_branch_service


def test_the_run_line_a_real_branch_prints_is_not_refused_defect():
    # the branch's own run line was refused
    root = Path(tempfile.mkdtemp())
    inbox, output, folder = duplicates._build_done_run(root, b"<video bytes>")
    with duplicates._Swap(root, inbox, output):
        branch = run_branch_service.create_branch(folder, 2)
        refusal = run_command.find_duplicate_refusal(inbox / "AcmeSync-review.mp4", branch, is_forced=False)
    harness.check("the branch's run is not refused", refusal is None, str(refusal))
