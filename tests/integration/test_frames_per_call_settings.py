"""The settings a run reads, through the real loader, from this repository's own default settings files: at two frames a
call release test 1's follow-ups took 3.4 times the reading (DEFECTS 18); the next run sends four, one step of two."""

from pathlib import Path

from support import harness

import config

ROOT = Path(__file__).resolve().parents[2]


def test_a_run_reads_four_frames_a_call_from_the_default_settings_defect():
    # The default settings held 2
    merged = config.read_settings_files(ROOT / "playbook.default.json", ROOT / "playbook.local.json")
    settings = config.Settings(**{key: value for key, value in merged.items() if not key.startswith("_")})
    harness.check("the run's frames a call is 4", settings.frames_processing.frames_per_call == 4,
                  str(settings.frames_processing.frames_per_call))
