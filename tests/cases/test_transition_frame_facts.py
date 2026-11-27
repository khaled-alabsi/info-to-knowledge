"""A frame almost nothing was said over may have no facts (the owner's rule: on-screen content nobody discusses is not facts).

A loading screen, a window switch or a cut-off word or two teaches nothing, and the worker rightly wrote `(none)` under
`## Facts`; the check that wants facts from a screen with three or more quoted texts rejected it twice on the same
finding, which blocks the phase. So the check counts the words spoken while the frame was shown (from the frames file):
at most eight is a transition, and more is a moment that must teach something.
"""

from support import harness
from support import frame_batch_fixtures

from services import phase_file_service
from services import record_file_service
from validators import item_validator


TEXTY_SCREEN = '- page: white\n  - title "Loading": centre\n  - label "Please wait": below the title\n  - label "Archer": top left'


def _codes_for_empty_facts(spoken: str) -> set[str]:
    ctx = frame_batch_fixtures.load_run_context(count=1)
    frames_number = 2
    frames_file = phase_file_service.read_phase_file(ctx.paths, frames_number)
    frames_file["items"][0]["transcriptCurrent"] = spoken
    phase_file_service.write_phase_file(ctx.paths, frames_number, frames_file)
    record_file_service.write_frame_record(ctx.paths, "frame-000", {**frame_batch_fixtures.FRAME, "frame": 0, "facts": [],
                                                                  "screen": TEXTY_SCREEN})

    return harness.codes(item_validator.validate_record(ctx.paths, "frame-000"))


def test_a_frame_with_almost_nothing_said_may_have_no_facts():
    harness.check("nothing spoken: empty facts are accepted", "FRAME_FACTS_MISSING" not in _codes_for_empty_facts(""))
    harness.check("a cut-off fragment of four words is accepted too",
                  "FRAME_FACTS_MISSING" not in _codes_for_empty_facts("Let's maybe do a."))


def test_a_frame_with_real_speech_still_needs_facts():
    spoken = "The proxy sends the token to the provider and the provider checks it against the tenant."
    harness.check("about fifteen words spoken: empty facts are rejected", "FRAME_FACTS_MISSING" in _codes_for_empty_facts(spoken))
