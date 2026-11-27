"""A Balanced video run whose speed was kept before the Workflow speed menu had an open-questions setting (DEFECTS 721):
its batches still asked their open questions and their answers, about 13 minutes a batch. Through the real start of a
run (the kept speed laid over the settings) and the real batch loop: one call, the batch's notes, and no follow-up."""

import json
from pathlib import Path

import config as app_config
from support import fakes
from support import fixtures as fx
from support import frame_batch_fixtures
from support import harness

from models import output_contract
from services import run_speed_service


def test_a_balanced_run_kept_before_the_setting_asks_no_open_questions_defect():
    # Balanced ran the open questions and answers steps on a video; now its batch makes only its own call
    old_balanced = {key: value for key, value in app_config.settings.speed_presets["balanced"].items()
                    if key != "framesProcessing.isOpenQuestionsOn"}
    group = fakes.frame_notes(batch_summary=frame_batch_fixtures.BATCH_SUMMARY, atmosphere=frame_batch_fixtures.ATMOSPHERE,
                              frames=[{**frame_batch_fixtures.RECORD, "frame": 0}, {**frame_batch_fixtures.RECORD, "frame": 1}])
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group, fakes.nothing_open()), count=2)
    folder = Path(ctx.paths.output_root)
    (folder / output_contract.RUN_SPEED_FILE).write_text(
        json.dumps({"preset": "balanced", "values": old_balanced, "name": "balanced"}), encoding="utf-8")
    saved = app_config.settings
    try:
        run_speed_service.apply_chosen_speed(folder)
        with fx.critique_switches(is_critique_on=False, is_critique_repair_on=False):
            state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    finally:
        app_config.settings = saved
    harness.check("both frames are written", sorted(state["written"]) == ["frame-000", "frame-001"], str(state["written"]))
    harness.check("the batch makes its one call and no open questions or answers", ctx.model.call_count == 1,
                  str(ctx.model.call_count))
