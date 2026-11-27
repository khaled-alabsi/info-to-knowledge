"""An outline indented by any consistent width is stored with two spaces a level (the owner's rule: fix the cause of a repeated rejection).

Three frames of one run came back with `## Screen` indented four spaces a level and were rejected twice each for it, once
nearly blocking the phase. The meaning was right; only the width was off, so the answer is folded to two spaces a level
where it is stored, and the outline check keeps judging what is left.
"""

from support import fakes
from support import frame_batch_fixtures
from support import harness

from utils import frame_markdown_utils as fm
from services import frame_records_service


WIDE = '- page: white\n    - header: black bar\n        - logo "Acme": top left\n    - body: centre\n        - title "Home": large'
NARROW = '- page: white\n  - header: black bar\n    - logo "Acme": top left\n  - body: centre\n    - title "Home": large'


def test_a_four_space_outline_is_folded_to_two():
    folded = fm.normalise_screen_indentation(WIDE)
    harness.check("four spaces a level become two", folded == NARROW, folded)
    harness.check("and the outline check finds nothing wrong with it", fm.read_screen_parts(folded).problems == [],
                  str(fm.read_screen_parts(folded).problems))


def test_an_outline_already_at_two_spaces_is_left_alone_and_a_first_indented_part_is_not_a_child():
    harness.check("two spaces a level: unchanged", fm.normalise_screen_indentation(NARROW) == NARROW)
    harness.check("a first part that is indented starts the outline at the left",
                  fm.normalise_screen_indentation("    - page: white\n        - title \"Home\": large")
                  == '- page: white\n  - title "Home": large')
    harness.check("a line that is not a part is left for the check to name",
                  fm.normalise_screen_indentation("- page: white\nplain text") == "- page: white\nplain text")


def test_a_wide_outline_in_a_group_answer_is_stored_folded_and_accepted():
    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0, "screen": WIDE}, {**frame_batch_fixtures.FRAME, "frame": 1})
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(group), count=2)
    state = frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)
    stored = next(entry for entry in frame_records_service.read_frame_records(ctx.paths) if entry["slug"] == "frame-000")
    harness.check("both frames are written from the first call", sorted(state["written"]) == ["frame-000", "frame-001"],
                  str(state["written"]))
    harness.check("the wide outline is stored with two spaces a level", stored["screen"] == NARROW, stored["screen"])
