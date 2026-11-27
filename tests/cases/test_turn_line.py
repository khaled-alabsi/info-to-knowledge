"""One turn of a conversation as one console line: what the agent did, then the conversation's size, and how close
the cut is only when it is near (the owner, 2026-09-28)."""

from support import harness

from utils import turn_line_utils


def test_a_turn_line_says_what_the_agent_did_then_the_size():
    line = turn_line_utils.render_turn_line([{"name": "read_notes", "args": {"chapter": 2, "chunk": 3}}], 69_617, 120_000)
    harness.check("the read, its chapter and chunk, then the size", line == "  read_notes chapter 2 chunk 3 · 69,617 tok", line)


def test_the_size_limit_is_named_only_when_it_is_near():
    line = turn_line_utils.render_turn_line([{"name": "submit_draft_headings", "args": {"purpose": "x", "headings": []}}],
                                                109_000, 120_000)
    harness.check("near the size limit, its share; a submit names no payload",
                  line == "  submit_draft_headings · 109,000 tok · 90% of the 120,000 size limit", line)


def test_several_calls_and_no_call():
    line = turn_line_utils.render_turn_line([{"name": "read_notes", "args": {"chapter": 1, "chunk": 1}},
                                                 {"name": "read_notes", "args": {"chapter": 1, "chunk": 2}}], 5_000, None)
    harness.check("every call of the turn, the tool named once", line == "  read_notes chapter 1 chunk 1, chapter 1 chunk 2 · 5,000 tok", line)
    masks = [{"name": "mask", "args": {"cut": cut, "rect": [0, 0, 5, 5]}} for cut in ("cut-046", "cut-047", "cut-047", "cut-040")]
    line = turn_line_utils.render_turn_line(masks, 21_357, None)
    harness.check("each call names what it worked on, and a repeat is counted (the owner, 2026-09-30: 'mask; mask; mask; "
                  "mask' is not a nice log)", line == "  mask cut-046, cut-047 ×2, cut-040 · 21,357 tok", line)
    harness.check("a turn with no call says so",
                  turn_line_utils.render_turn_line([], 5_000, None) == "  answered with no tool call · 5,000 tok")
