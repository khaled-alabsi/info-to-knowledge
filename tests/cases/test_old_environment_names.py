"""The command's first name was `v2k`: a setup that still sets `V2K_WORKSPACE` or `V2K_PROFILE` is read as the `I2K_` one,
and the `I2K_` one wins when both are set."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from support import harness


def _seen_by_a_process(environment: dict[str, str]) -> list[str]:
    """The workspace folder and the profile variable's value a fresh Python process reads from `config`, with
    `environment` added and the other names removed."""

    src = Path(__file__).resolve().parents[2] / "src"
    base = {key: value for key, value in os.environ.items() if not key.startswith(("V2K_", "I2K_"))}
    code = "import os, config; print(config.WORKSPACE_ROOT); print(os.environ.get(config.PROFILE_ENV_VAR, ''))"
    done = subprocess.run([sys.executable, "-c", code], cwd=src, env={**base, **environment, "PYTHONPATH": str(src)},
                          capture_output=True, text=True, check=True)

    return done.stdout.strip().splitlines()[-2:]


def test_the_old_v2k_names_are_read_and_the_new_names_win():
    with tempfile.TemporaryDirectory() as old_folder, tempfile.TemporaryDirectory() as new_folder:
        workspace, profile = _seen_by_a_process({"V2K_WORKSPACE": old_folder, "V2K_PROFILE": "strict"})
        harness.check("V2K_WORKSPACE moves the workspace when I2K_WORKSPACE is not set",
                      Path(workspace) == Path(old_folder).resolve(), workspace)
        harness.check("V2K_PROFILE names the profile when I2K_PROFILE is not set", profile == "strict", profile)
        workspace, profile = _seen_by_a_process({"V2K_WORKSPACE": old_folder, "I2K_WORKSPACE": new_folder,
                                                 "V2K_PROFILE": "strict", "I2K_PROFILE": "container"})
        harness.check("I2K_WORKSPACE wins over V2K_WORKSPACE", Path(workspace) == Path(new_folder).resolve(), workspace)
        harness.check("I2K_PROFILE wins over V2K_PROFILE", profile == "container", profile)
