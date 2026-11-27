"""A chapter follow-up driven through the real conversation loop and the real `look` tool (DEFECTS 232): after a resume,
a public froscon 2024 talk's chapter 2 follow-up was answered with chapter 3's, then chapter 4's heading, the worker
reasoning from its own memory of the run. Each frame tool's result now names what the follow-up asks."""

import dataclasses

from langchain_core.messages import ToolMessage

from support import fakes
from support import harness
from cases import test_write_follow_up_loop as loop_cases
from services import write_conversation_service


def test_the_asked_heading_is_restated_after_each_frame_tool_defect():
    # The heading the run's follow-up asked for, on made notes
    spec = dataclasses.replace(loop_cases.CHAPTER, slug="chapter-02-text-2", position="chapter 2",
                               inputs={"sections": [{"slug": "heading-005-1", "entryIds": ["F0.1"],
                                                     "heading": "Schematic walkthrough and configuration jumpers",
                                                     "topicId": "T02.3"}]})
    session = loop_cases._build_session(fakes.tool_call("look", frame="frame-000"), fakes.tool_call("look", frame="frame-000"),
                                        fakes.tool_call("submit_chapter", markdown=loop_cases.CHAPTER_MARKDOWN))
    write_conversation_service.run_follow_up(session, spec)
    results = [str(message.content) for message in session.messages if isinstance(message, ToolMessage)]
    line = "This follow-up asks for chapter 2 and only these headings: ### Schematic walkthrough and configuration jumpers (T02.3)"
    harness.check("every frame tool's result restates it", sum(line in text for text in results) >= 2,
                  str([text[-160:] for text in results]))
