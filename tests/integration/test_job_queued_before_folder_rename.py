"""A job queued before the repository's folder was renamed kept its recording's and run folder's paths under the old
folder, and its start was refused "does not exist". Here the real command line starts a dry run with both paths under
the old folder, the workspace moved to a fresh folder, and the run must start on the recording in that folder."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from support import harness

import config


def test_a_run_started_with_paths_under_the_old_folder_runs_on_the_recording_there_now_defect():
    # A queued job named its recording under the repository's old folder and was refused at its start; the start now
    # reads each path under the folder it is now, the recording found and the run folder written there
    old_workspace = config.PLAYBOOK_ROOT.parent / "video-to-knowledge"
    if old_workspace.exists():
        harness.check("the old folder name is free for this test", False, str(old_workspace))
        return
    workspace = Path(tempfile.mkdtemp())
    (workspace / "inbox").mkdir()
    (workspace / "inbox" / "AcmeSync-weekly.mp4").write_bytes(b"<video bytes>")
    run_folder = workspace / "output" / "AcmeSync-weekly"
    run_folder.mkdir(parents=True)
    done = subprocess.run([sys.executable, str(config.PLAYBOOK_ROOT / "main.py"), "run",
                           "--video", str(old_workspace / "inbox" / "AcmeSync-weekly.mp4"),
                           "--output", str(old_workspace / "output" / "AcmeSync-weekly"), "--dry-run"],
                          cwd=config.PLAYBOOK_ROOT, capture_output=True, text=True, timeout=300,
                          env={**os.environ, "I2K_WORKSPACE": str(workspace)})
    console = done.stdout + done.stderr
    harness.check("the run started, exit 0", done.returncode == 0 and "does not exist" not in console, console[-2000:])
    harness.check("and wrote into the run folder under the folder it is now", any(run_folder.iterdir()),
                  str(sorted(path.name for path in run_folder.iterdir())))
    harness.check("and made no folder of the old name", not old_workspace.exists(), str(old_workspace))
