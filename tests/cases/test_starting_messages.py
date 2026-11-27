"""The starting_messages of phase 4's conversation, and the arithmetic that decides when it is cut back to it. REQUIREMENTS.md R12.

The starting_messages is what a follow-up needs and nothing else, rebuilt by the program from disk: never a summary a model
wrote. Its parts go in order from the one that changes least to the one that changes most, so a host that reuses an
unchanged prefix reuses as much as it can. A rebuild after a cut and a rebuild on resume must be the same bytes.
"""

from prompts import write_messages
from support import harness

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from models.starting_messages_spec import StartingMessagesSpec
from utils import starting_messages_utils

STARTING_MESSAGES = StartingMessagesSpec(system_text="You write the document of one meeting.",
                              plan_text="Working title: Refunds\nAgenda:\n1. Limits (done)\n2. Booking (writing)",
                              notes=["The refund rule is explained in T02.1; later sections link to it."],
                              done_lines=['[T01.1, step 1, "Refund limits": accepted]'],
                              topic_parts_text="Refunds are split into at most three parts.",
                              follow_up_text="Section T02.2, step 2: write it from entries e41 to e47.",
                              findings_text="")


def test_the_starting_messages_go_from_what_changes_least_to_what_changes_most():
    messages = starting_messages_utils.build_starting_messages(STARTING_MESSAGES)
    text = "\n".join(str(message.content) for message in messages)
    harness.check("the instruction is the system message, first", isinstance(messages[0], SystemMessage)
                  and messages[0].content == STARTING_MESSAGES.system_text)
    order = [text.index(part) for part in (STARTING_MESSAGES.plan_text, STARTING_MESSAGES.notes[0], STARTING_MESSAGES.done_lines[0], STARTING_MESSAGES.topic_parts_text,
                                           STARTING_MESSAGES.follow_up_text)]
    harness.check("then plan, notes, accepted lines, the topic's parts, the follow-up", order == sorted(order), str(order))
    harness.check("the follow-up is the last message, on its own", messages[-1].content.endswith(STARTING_MESSAGES.follow_up_text))


def test_a_retry_carries_its_findings_and_a_first_attempt_does_not():
    first = "\n".join(str(message.content) for message in starting_messages_utils.build_starting_messages(STARTING_MESSAGES))
    retry_starting_messages = StartingMessagesSpec(**{**STARTING_MESSAGES.__dict__, "findings_text": "SECTION_REPEATS: e44 is said in T01.1"})
    retry = starting_messages_utils.build_starting_messages(retry_starting_messages)
    harness.check("a first attempt names no finding", "SECTION_REPEATS" not in first)
    harness.check("a retry's last message holds the findings after the follow-up",
                  retry[-1].content.index("SECTION_REPEATS") > retry[-1].content.index(STARTING_MESSAGES.follow_up_text))


def test_the_same_starting_messages_rebuild_to_the_same_bytes_and_ids():
    once = starting_messages_utils.build_starting_messages(STARTING_MESSAGES)
    again = starting_messages_utils.build_starting_messages(STARTING_MESSAGES)
    harness.check("same content", [message.content for message in once] == [message.content for message in again])
    harness.check("same ids, so a resumed prompt and a cut prompt compare equal",
                  [message.id for message in once] == [message.id for message in again] and all(m.id for m in once))


def test_empty_parts_leave_no_heading_behind():
    bare = StartingMessagesSpec(system_text="S", plan_text="P", notes=[], done_lines=[], topic_parts_text="",
                                 follow_up_text="F", findings_text="")
    text = "\n".join(str(message.content) for message in starting_messages_utils.build_starting_messages(bare))
    harness.check("no notes heading without notes", "notes" not in text.lower(), text)
    harness.check("no accepted-lines heading without lines", "accepted" not in text.lower(), text)


def test_the_estimate_counts_text_frames_and_tools():
    messages = [SystemMessage(content="x" * 350), HumanMessage(content=[{"type": "text", "text": "y" * 70},
                                                                        {"type": "image_url", "image_url": {"url": "d"}}]),
                ToolMessage(content="z" * 35, tool_call_id="c1")]
    estimate = starting_messages_utils.estimate_prompt_tokens(messages, chars_per_token=3.5, frame_tokens=1000,
                                                               tools_tokens=500)
    harness.check("100 + 20 + 10 text tokens, one frame, the tools", estimate == 100 + 20 + 10 + 1000 + 500,
                  str(estimate))


def test_each_warning_share_fires_once_and_in_order():
    harness.check("below 60% nothing", starting_messages_utils.decide_warning_share(59_000, 100_000, set()) is None)
    harness.check("at 60% the first share", starting_messages_utils.decide_warning_share(60_000, 100_000, set()) == 0.6)
    harness.check("the first share is not warned of twice",
                  starting_messages_utils.decide_warning_share(70_000, 100_000, {0.6}) is None)
    harness.check("at 80% the second", starting_messages_utils.decide_warning_share(85_000, 100_000, {0.6}) == 0.8)
    harness.check("jumping past both fires the louder one",
                  starting_messages_utils.decide_warning_share(90_000, 100_000, set()) == 0.8)


def test_the_warning_says_what_it_is_not():
    text = write_messages.render_warning(85_000, 100_000)
    harness.check("it gives the count and the size_limit", "85,000" in text and "100,000" in text, text)
    harness.check("it names note and says the program compacts", "compacts" in text and "note" in text, text)
    harness.check("it says it is not a request to finish", "not a request to finish" in text, text)


def test_the_trigger_never_exceeds_the_window_less_the_answer():
    harness.check("the configured size_limit when it fits",
                  starting_messages_utils.resolve_size_limit(120_000, 163_840, 32_768) == 120_000)
    harness.check("clamped to the window minus the answer's limit",
                  starting_messages_utils.resolve_size_limit(150_000, 163_840, 32_768) == 131_072)
    harness.check("never below 1", starting_messages_utils.resolve_size_limit(10, 1_000, 5_000) == 1)


def test_a_count_near_the_window_is_a_suspected_cut_and_a_low_one_is_not():
    harness.check("at the window less the answer, the host may have cut the start",
                  starting_messages_utils.is_prompt_truncation_suspected(131_072, 163_840, 32_768))
    harness.check("a count under the estimate is only an overestimate, never a cut",
                  not starting_messages_utils.is_prompt_truncation_suspected(40_000, 163_840, 32_768))


def test_a_jump_past_both_shares_is_warned_of_once():
    warned: set[float] = set()
    first = starting_messages_utils.decide_warning_share(85_000, 100_000, warned)
    warned.add(first)
    second = starting_messages_utils.decide_warning_share(86_000, 100_000, warned)
    harness.check("warned at 80% once, never again at 60%", first == 0.8 and second is None, f"{first} {second}")
