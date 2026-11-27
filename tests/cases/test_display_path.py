"""The console shows paths from the repository root, never the absolute path."""

from pathlib import Path

from support import harness

import config
from utils import display_path_utils


def test_a_path_inside_the_repository_is_shown_from_its_root():
    shown = display_path_utils.render_path(config.WORKSPACE_ROOT / "output" / "run" / "console.log")
    harness.check("from the repository root", shown == "output/run/console.log", shown)


def test_a_path_outside_the_repository_but_in_home_keeps_the_tilde():
    shown = display_path_utils.render_path(Path.home() / "Downloads" / "meeting.mp4")
    harness.check("home becomes ~", shown == "~/Downloads/meeting.mp4", shown)
