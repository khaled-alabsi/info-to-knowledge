"""The suite runs every case file in one process: a test that changes the process's settings gives them back, so no
case file sees another's settings."""

from __future__ import annotations

from support import harness

import config
import run_tests
from services import run_speed_service


def test_a_test_that_lays_the_default_speed_over_the_settings_leaves_the_next_test_the_settings_it_had_defect():
    # Defect: a case that started a new video run in the suite's process laid Balanced over the settings, the review
    # went off for every case file after it, and 22 review checks failed only when the whole suite ran. Fix: the runner
    # gives each test back the settings, the profile and the worker set it found.
    was_on = config.settings.review.is_on
    profile_name, worker_set_name = config.PROFILE_NAME, config.WORKER_SET_NAME

    def start_a_new_video_run():
        speed = run_speed_service.resolve_speed(run_speed_service.DEFAULT_PRESET, {})
        config.apply_speed_values(speed["values"])
        config.settings.review.is_on = not was_on
        config.WORKER_SET_NAME = "Gateway-box"

    run_tests.run_case(start_a_new_video_run, "lays the default speed")
    harness.check("the review switch is as it was before the test", config.settings.review.is_on == was_on,
                  str(config.settings.review.is_on))
    harness.check("the profile and the worker set are as they were",
                  (config.PROFILE_NAME, config.WORKER_SET_NAME) == (profile_name, worker_set_name), config.WORKER_SET_NAME)


def test_a_test_that_crashes_is_one_failing_check_and_still_gives_the_settings_back_defect():
    # Defect: the settings were given back only by each test itself, so a test that crashed halfway left its change.
    # Fix: the runner gives them back whatever the test did.
    was_on = config.settings.review.is_on
    failed_before = len(harness.FAILED)

    def crash_after_a_change():
        config.settings.review.is_on = not was_on
        raise RuntimeError("halfway")

    run_tests.run_case(crash_after_a_change, "crashes halfway")
    crashed = harness.FAILED[failed_before:]
    del harness.FAILED[failed_before:]
    harness.check("the crash is recorded as one failing check by its name",
                  [name for name, _ in crashed] == ["crashes halfway"], str(crashed))
    harness.check("and the review switch is given back", config.settings.review.is_on == was_on)
