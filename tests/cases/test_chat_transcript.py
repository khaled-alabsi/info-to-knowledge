# <!-- sync: seed/cases.test_chat_transcript v1 -->
"""`transcript.md` - the attempt as a readable chat log, rewritten while the attempt is running.

The point of the file is being readable DURING a run, so the test that matters is not "it renders"
but "it renders again on the next step". A transcript written once at the end is the same file
`messages.jsonl` already was.
"""

import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from support import harness

from services.attempt_log_service import AttemptLog
from utils import chat_transcript_utils


def _thinking_turn():
    return AIMessage(content="Found three action points.",
                     additional_kwargs={"reasoning_content": "The controller has three mappings."},
                     tool_calls=[{"name": "read_file", "args": {"path": "A.java"}, "id": "c1"}])


def test_transcript_shows_thinking_response_and_tool_call():
    text = chat_transcript_utils.render_transcript([HumanMessage(content="Enumerate them."),
                                                    _thinking_turn()])
    for label, needle in (("thinking", "The controller has three mappings."),
                          ("response", "Found three action points."),
                          ("tool call", "read_file"),
                          ("the request", "Enumerate them.")):
        harness.check(f"transcript shows the {label}", needle in text, text[:400])


def test_transcript_clips_a_huge_tool_result_but_keeps_the_head():
    text = chat_transcript_utils.render_transcript(
        [ToolMessage(content="X" * 40000, tool_call_id="c1", name="read_file")])
    harness.check("huge tool result is clipped", len(text) < 4000, str(len(text)))
    harness.check("clip says where the full text is", "messages.jsonl" in text, text[-200:])


def test_transcript_survives_content_that_contains_a_code_fence():
    text = chat_transcript_utils.render_transcript(
        [ToolMessage(content="```java\nclass A {}\n```", tool_call_id="c1", name="read_file")])
    harness.check("fence is escaped by a longer fence", "````" in text, text)


def test_transcript_renders_content_blocks_not_their_repr():
    text = chat_transcript_utils.render_transcript(
        [AIMessage(content=[{"type": "reasoning", "reasoning": "weighing it up"},
                            {"type": "text", "text": "the answer"}])])
    harness.check("block content is rendered as text", "weighing it up" in text and
                  "the answer" in text and "'type':" not in text, text)


def test_transcript_is_rewritten_on_every_step():
    log = AttemptLog(Path(tempfile.mkdtemp()))
    log.write_messages([HumanMessage(content="Enumerate them.")])
    first = (log.directory / "transcript.md").read_text(encoding="utf-8")
    log.write_messages([HumanMessage(content="Enumerate them."), _thinking_turn()])
    second = (log.directory / "transcript.md").read_text(encoding="utf-8")
    harness.check("a mid-attempt transcript exists", "Enumerate them." in first, first[:200])
    harness.check("the next step rewrites it",
                  "The controller has three mappings." in second and len(second) > len(first),
                  second[:400])
