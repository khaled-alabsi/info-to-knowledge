"""A chapter follow-up driven through the real conversation loop and the real `look` and `cut` tools (DEFECTS 363):
release test 1's writer (froscon 2024 talk 3147, chapter 1, frame 10) called `look` and `cut` on one frame in one
turn, so the cut's rectangle was chosen before the frame's picture reached it, and the cut kept the slide's margin and
cut through its lines. A cut asked in the turn of its frame's first look now waits for the next turn."""

import dataclasses
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from support import fakes
from support import harness
from cases import test_illustration_actions as action_cases
from cases import test_write_follow_up_loop as loop_cases
from services import write_conversation_service


def _calls_in_one_turn(*turns: AIMessage) -> AIMessage:
    return AIMessage(content="", tool_calls=[call for turn in turns for call in turn.tool_calls])


def test_a_cut_beside_its_frame_s_first_look_is_made_only_in_the_next_turn_defect():
    # The cut beside its look in one turn guessed its rectangle; it is now named blind and asked again next turn
    spec = dataclasses.replace(loop_cases.CHAPTER, slug="chapter-01-text-2", position="chapter 1",
                               inputs={"sections": [{"slug": "heading-001-1", "entryIds": ["F0.1"],
                                                     "heading": "Early terminals and their escape codes",
                                                     "topicId": "T01.1"}]})
    rect = {"frame": "frame-000", "rect": [10, 10, 400, 300]}
    session = loop_cases._build_session(
        _calls_in_one_turn(fakes.tool_call("look", frame="frame-000"), fakes.tool_call("cut", **rect)),
        fakes.tool_call("cut", **rect),
        fakes.tool_call("submit_chapter", markdown=loop_cases.CHAPTER_MARKDOWN))
    # The frame tools on a recording rendered for the test, as the cut tool's own tests build them
    paths, video = action_cases._build_run(Path(tempfile.mkdtemp()))
    session.reading = dataclasses.replace(session.reading, illustration=action_cases._build_context(paths, video))
    write_conversation_service.run_follow_up(session, spec)
    results = [str(message.content) for message in session.messages if isinstance(message, ToolMessage)]
    cuts = session.reading_state.image_store.get("cuts") or {}
    harness.check("the cut in the look's turn is not made, and says so",
                  any("in this same turn" in text for text in results), str([text[:160] for text in results]))
    harness.check("the next turn's cut is made, once", len(cuts) == 1, str(list(cuts)))
