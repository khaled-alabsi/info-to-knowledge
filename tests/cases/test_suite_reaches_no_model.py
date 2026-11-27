"""The suite never reaches a model host: phase 2's model filters run only in a test that turns them on and fakes the
model. This machine's settings turn them on, and every case file that ran phase 2 without asking called the real
redundancy model, which LM Studio then loaded beside a run (DEFECTS 82)."""

from __future__ import annotations

from support import harness

import config


def test_phase_2s_model_filters_are_off_unless_a_test_turns_them_on():
    frames = config.settings.frames
    harness.check("the redundancy filter and the similarity recheck are off in the suite",
                  not frames.is_redundancy_filter_on and not frames.is_similarity_recheck_on,
                  f"{frames.is_redundancy_filter_on} {frames.is_similarity_recheck_on}")
