"""One compaction prompt for each worker, and a compaction that is smart (the owner, 2026-09-29)."""

import dataclasses
import json
import re
import shutil
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from support import fakes
from support import harness

import config
from models.compaction_request import CompactionRequest
from prompts import compaction_prompt_loader
from services import compaction_service
from services import write_conversation_service

from cases import test_write_follow_up_loop as loop

WORKERS = sorted(set(compaction_prompt_loader.WORKER_OF_SUBMIT_TOOL.values()))
# A summary far over its allowance, every line its own
LONG_SUMMARY = "".join(f"Heading {index}: the Gateway retries batch {index * 7} for AcmeSync. " for index in range(450))


def _build_request(worker: str = "executive_summary_draft",
                   size_limit_tokens: int = 120_000,
                   tokens: int = 50_000,
                   tools_tokens: int = 0):
    session = loop._build_session()
    spec = loop.DRAFT
    return CompactionRequest(worker=worker, session=session, spec=spec, findings_text="", attempts=1, calls=2, max_calls=6,
                             tokens=tokens, size_limit_tokens=size_limit_tokens, tail_start=len(session.messages), tools_tokens=tools_tokens)


def test_every_worker_has_a_prompt_and_a_handoff_and_both_render():
    harness.check("the 7 workers of the conversation", WORKERS == sorted(["executive_summary_draft", "parts", "draft_headings", "chapter",
                                                                          "summary", "review_critique", "review_resolution"]), str(WORKERS))
    for worker in WORKERS:
        prompt = compaction_prompt_loader.read_summary_prompt(worker).replace("{allowance}", "1,500").format(messages="THE HISTORY")
        harness.check(f"{worker}: the prompt renders with its allowance and history and holds no placeholder left",
                      "1,500" in prompt and "THE HISTORY" in prompt and "{" not in prompt and "}" not in prompt, prompt[-300:])
        harness.check(f"{worker}: the prompt names its own sections", prompt.count("\n## ") >= 3, prompt[:200])
        handoff = compaction_service.build_handoff(worker, {"attempt": "2"}, "MY SUMMARY", {})
        harness.check(f"{worker}: the handoff holds a measured block, the summary and a check",
                      "<measured>" in handoff and "MY SUMMARY" in handoff and "<check>" in handoff and "{" not in handoff, handoff[:200])
    raised = False
    try:
        compaction_service.pick_worker("submit_section")
    except KeyError:
        raised = True
    harness.check("a submit tool with no worker raises: no generic prompt exists", raised)


def test_every_compaction_text_is_read_from_its_own_file():
    # Each conversation worker has a summary prompt and a handoff file, each phase 3 follow-up a cut-thinking file, and
    # nothing else lies in compaction/: a file no worker reads is a text nobody is sent, and the page would offer it
    root = config.AGENT_CONTEXT_DIR / compaction_prompt_loader.COMPACTION_DIR
    expected = {f"conversation/{worker}/{name}.md" for worker in WORKERS for name in ("summary", "handoff")}
    expected |= {f"cut-thinking/{step}.md" for step in compaction_prompt_loader.CUT_THINKING_STEPS}
    expected |= {"conversation/tail-line.md", "conversation/write-shorter.md", "cut-thinking/handoff.md",
                 "cut-thinking/finish.md", "cut-thinking/finish-mistakes.md"}
    on_disk = {path.relative_to(root).as_posix() for path in root.rglob("*.md") if path.name != "README.md"}
    harness.check("every compaction file on disk is read, and every text read has its file", on_disk == expected,
                  f"not read: {sorted(on_disk - expected)}; missing: {sorted(expected - on_disk)}")


def test_an_edited_compaction_file_reaches_the_next_compaction():
    # The compaction prompts were Python strings, fixed until the next release; as files read at each call, an edit
    # reaches the next compaction of a running run, as an instruction file's does
    original_root = config.AGENT_CONTEXT_DIR
    with tempfile.TemporaryDirectory() as folder:
        copy_root = Path(folder) / "agent-context"
        shutil.copytree(src=original_root, dst=copy_root)
        summary_path = copy_root / compaction_prompt_loader.COMPACTION_DIR / "conversation" / "chapter" / "summary.md"
        summary_path.write_text(summary_path.read_text(encoding="utf-8") + "\nKeep the Gateway's retry count.\n",
                                encoding="utf-8")
        config.AGENT_CONTEXT_DIR = copy_root
        try:
            prompt = compaction_prompt_loader.read_summary_prompt("chapter")
        finally:
            config.AGENT_CONTEXT_DIR = original_root
    harness.check("the next read holds the edit", "Keep the Gateway's retry count." in prompt, prompt[-200:])
    harness.check("the shipped file is unchanged",
                  "Keep the Gateway's retry count." not in compaction_prompt_loader.read_summary_prompt("chapter"))


def test_the_handoff_names_the_attempt_limit_of_the_follow_up_being_compacted():
    request = _build_request("chapter")
    own = dataclasses.replace(request, spec=dataclasses.replace(request.spec, max_attempts=1))
    measured = compaction_service.compute_measured_values(own)
    harness.check("a follow-up with its own attempt limit is told that one, not the conversation's",
                  measured["max_attempts"] == "1", str(measured))
    harness.check("one with none is told the conversation's",
                  compaction_service.compute_measured_values(request)["max_attempts"] == str(request.session.limits.max_attempts),
                  str(compaction_service.compute_measured_values(request)))


def test_the_measured_block_lists_the_reads_the_log_holds_and_says_none_when_there_are_none():
    request = _build_request()
    empty = compaction_service.compute_measured_values(request)
    harness.check("no read in the log gives none", empty["reads_list_or_none"] == "none", str(empty))
    rows = [{"followUp": "executive-summary-draft", "calls": [{"name": "find", "args": {"words": "refund"}},
                                                             {"name": "read_moments", "args": {"start": 1}}]},
            {"followUp": "another", "calls": [{"name": "list_tags", "args": {}}]}]
    request.session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    request.session.conversation_log_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    measured = compaction_service.compute_measured_values(request)
    harness.check("the reads of this follow-up only, in order, from the log",
                  measured["reads_list_or_none"].count(";") == 1 and measured["reads_list_or_none"].startswith("find ")
                  and "list_tags" not in measured["reads_list_or_none"], str(measured))
    harness.check("the attempt, the calls and the size are the program's numbers",
                  measured["attempt"] == "2" and measured["calls"] == "2" and measured["max_calls"] == "6"
                  and measured["tokens"] == "50,000" and measured["size_limit"] == "120,000", str(measured))


def test_the_allowance_is_worked_out_from_the_room_left_and_capped_by_the_worker():
    roomy = compaction_service.compute_allowance(_build_request(size_limit_tokens=120_000), "measured")
    harness.check("with a large size limit the worker's own cap is the allowance",
                  roomy == compaction_service.WORKER_CAPS["executive_summary_draft"], str(roomy))
    tight = compaction_service.compute_allowance(_build_request(size_limit_tokens=8_000, tools_tokens=3_600), "measured")
    harness.check("with a small size limit the room left decides, and it is under the minimum", tight < compaction_service.MIN_ALLOWANCE,
                  str(tight))
    chapter = compaction_service.compute_allowance(_build_request("chapter", size_limit_tokens=120_000), "measured")
    harness.check("the chapter worker may write the most", chapter == compaction_service.WORKER_CAPS["chapter"] and chapter > roomy)


def test_long_and_empty_summaries_are_asked_again_and_named():
    request = _build_request()
    request.session.model.script.extend([fakes.says(""), fakes.says("")])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("two empty answers leave no summary", (summary, outcome) == ("", "empty"), str((summary, outcome)))
    # A long summary says something new on every line; one word 4,000 times is a loop, which the stream now ends
    long_text = LONG_SUMMARY
    request = _build_request()
    request.session.model.script.extend([fakes.says(long_text), fakes.says("short one")])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("a summary over its allowance is asked again and the shorter one is kept",
                  summary == "short one" and outcome == "retried", str((summary[:20], outcome)))
    request = _build_request()
    request.session.model.script.extend([fakes.says(long_text), fakes.says(long_text)])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("a second long answer is kept and named over the allowance", outcome == "over allowance", outcome)
    request = _build_request()
    request.session.model.script.extend([fakes.says('<call:read_notes>{"chapter": 9}</call>'), fakes.says("the plan so far")])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("a reply that is only a tool call written as text is no summary: it is asked again on the short history",
                  (summary, outcome) == ("the plan so far", "retried"), str((summary, outcome)))
    request = _build_request()
    # A chapter of 136,099 tokens was compacted onto the history's own rendering of one call, and judged ok
    request.session.model.script.extend([fakes.says('called look {"frame": "frame-006"}'), fakes.says("the plan so far")])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("a reply that only copies a call line of the history is no summary either",
                  (summary, outcome) == ("the plan so far", "retried"), str((summary, outcome)))
    request = _build_request()
    request.session.model.script.extend([fakes.says('called look {"frame": "frame-006"}\nThe cuts so far: cut-001.'),
                                         fakes.says("unused")])
    summary, _ = compaction_service.write_summary(request, 1_500)
    harness.check("a summary that names a call among its own lines is kept", summary.startswith("called look"), summary)
    request = _build_request()
    request.session.model.script.extend([fakes.says('{"name": "read_notes", "arguments": {"chapter": 9}}'),
                                         fakes.says("<tool_call>read_notes</tool_call>")])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("and two such replies leave no summary, as two empty ones do", (summary, outcome) == ("", "empty"),
                  str((summary, outcome)))


def test_a_compaction_summary_is_streamed_seen_and_ended_when_it_loops_defect():
    # Phase 4's compaction ran about 27 minutes unstreamed: nothing on disk while it ran, no loop check to end it, and its
    # first reply gave no summary (DEFECTS 366). It is streamed like every other call now
    from langchain_core.messages import AIMessageChunk

    from cases import test_phase_nodes

    request = _build_request()
    passage = "DONE: the Gateway section is drafted with cut-003 placed; NEXT: submit it. " * 6
    pieces = [AIMessageChunk(content="SUMMARY\n")] + [AIMessageChunk(content=passage) for _ in range(120)]
    pieces.append(AIMessageChunk(content="never reached", response_metadata={"finish_reason": "stop"}))
    request.session.model = test_phase_nodes._StreamingModel(pieces=pieces)
    summary, _ = compaction_service.write_summary(request, 1_500)
    live = request.session.conversation_log_path.with_name(compaction_service.LIVE_SUMMARY_NAME)
    harness.check("the looping summary is ended before the stream's end", "never reached" not in summary, summary[-200:])
    harness.check("and kept once, its repeats dropped", 0 < summary.count("NEXT: submit it.") < 10, str(summary.count("NEXT")))
    harness.check("its text so far was on disk while it streamed", live.is_file(), str(live))


def test_the_history_carries_the_thinking_of_the_last_three_turns_only_and_clipped():
    messages = [HumanMessage(content="ask")] + [
        AIMessage(content=f"answer {number}", additional_kwargs={"reasoning_content": "T" * 10_000}) for number in range(5)]
    history = compaction_service.render_history(messages)
    harness.check("3 of 5 turns show their thinking", history.count("YOUR THINKING:") == 3, str(history.count("YOUR THINKING:")))
    harness.check("each is clipped to 6,000 characters", "T" * 6_001 not in history and "T" * 6_000 in history)
    longer = [HumanMessage(content="ask")] + [AIMessage(content=f"answer {number}") for number in range(8)]
    short = compaction_service.render_history(longer, is_short=True)
    harness.check("a short history is the last 6 turns", "answer 1" not in short and "answer 2" in short
                  and "answer 7" in short, short[:120])


def test_a_streamed_turn_is_the_model_s_own_and_a_written_tool_result_is_no_summary_defect():
    # A chapter's summary call wrote the dialogue on, a look and a cut-010 nobody made, and was judged ok (DEFECTS 632):
    # its history labelled the model's streamed turns "AIMESSAGECHUNK:" with no thinking, and the check never saw tool
    # results only the program writes. A streamed turn is now "YOU:" with its thinking, and such a reply is asked again
    from langchain_core.messages import AIMessageChunk

    messages = [HumanMessage(content="ask")] + [
        AIMessageChunk(content=f"answer {number}", additional_kwargs={"reasoning_content": "planned"}) for number in range(8)]
    history = compaction_service.render_history(messages)
    harness.check("a streamed turn is the model's own, with its thinking", "YOU: answer 7" in history
                  and "AIMESSAGECHUNK" not in history and history.count("YOUR THINKING:") == 3, history[-300:])
    short = compaction_service.render_history(messages, is_short=True)
    harness.check("and the short history is shortened to its last turns", "answer 1" not in short and "answer 7" in short,
                  short[:120])
    request = _build_request()
    request.session.model.script.extend([fakes.says('look {"frame": "frame-057"}\nTOOL RESULT: [r30] frame-057: 552 x 576 '
                                                    'pixels.\n\n### Heading (T04.1)\nText [F50.1].'),
                                         fakes.says("### Heading (T04.1)\nText [F50.1].")])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("a reply holding a tool result is no summary: asked again", (summary, outcome)
                  == ("### Heading (T04.1)\nText [F50.1].", "retried"), str((summary, outcome)))


def test_the_same_state_gives_the_same_handoff_bytes():
    request = _build_request()
    first = compaction_service.build_handoff("chapter", compaction_service.compute_measured_values(_build_request("chapter")), "S", {})
    second = compaction_service.build_handoff("chapter", compaction_service.compute_measured_values(_build_request("chapter")), "S", {})
    harness.check("two builds from the same state are equal", first == second)


def test_a_read_that_would_overflow_is_preceded_by_a_compaction_and_one_that_fits_is_not():
    import dataclasses

    request = _build_request()
    session = request.session
    session.reading = dataclasses.replace(session.reading, point_starts=[0.0], note_sections=[(1.0, "x" * 105_000)])  # about 30,000 tokens
    session.messages = [HumanMessage(content="y" * 315_000)]                                                          # about 90,000 tokens
    overflowing = write_conversation_service._is_read_overflowing(session, "read_notes", {"chapter": 1}, 0, 120_000)
    harness.check("90,000 tokens plus a read of 30,000 passes 120,000: compact first", overflowing)
    session.messages = [HumanMessage(content="y" * 140_000)]                                                          # about 40,000 tokens
    harness.check("40,000 plus 30,000 fits: no compaction for a read it has not made",
                  not write_conversation_service._is_read_overflowing(session, "read_notes", {"chapter": 1}, 0, 120_000))
    harness.check("a read with no chapter has no size known before it is made",
                  not write_conversation_service._is_read_overflowing(session, "read_notes", {}, 0, 120_000))
    session.messages = [HumanMessage(content="y" * 315_000)]
    harness.check("a chapter sent as 1.0 or as text is the same chapter: compact first",
                  write_conversation_service._is_read_overflowing(session, "read_notes", {"chapter": 1.0}, 0, 120_000)
                  and write_conversation_service._is_read_overflowing(session, "read_notes", {"chapter": "1"}, 0, 120_000))


def test_a_compaction_keeps_the_last_turns_word_for_word_and_summarises_only_what_is_before_them():
    session = loop._build_session(fakes.says("## DRAFT\nA refund is split in three. SETTLED\n## NEXT\nSubmit."))
    write_conversation_service._open_follow_up(session, loop.DRAFT)
    turns = [AIMessage(content="OLD TURN", tool_calls=[{"name": "find", "args": {"words": "refund"}, "id": "c1"}]),
             ToolMessage(content="OLD RESULT " + "x" * 50_000, tool_call_id="c1"),                  # about 14,000 tokens
             AIMessage(content="NEW TURN", tool_calls=[{"name": "find", "args": {"words": "split"}, "id": "c2"}]),
             ToolMessage(content="NEW RESULT", tool_call_id="c2")]
    for message in turns:
        write_conversation_service._append(session, message)
    reason = write_conversation_service._write_compaction(session, loop.DRAFT, "", 0, 2, 6, 20_000, 120_000, 0)
    harness.check("compacted", reason == "", reason)
    ask = loop._texts(session.model.calls[0])
    harness.check("the summary is written from the turns before the tail only", "OLD RESULT" in ask and "NEW RESULT" not in ask, ask[-300:])
    kept = [message.content for message in session.messages[-2:]]
    harness.check("the last turn follows the handoff word for word, its call with its result", kept == ["NEW TURN", "NEW RESULT"]
                  and "<summary>" in session.messages[-3].content, str(kept))
    harness.check("the turn past the 10,000 tokens is gone", "OLD RESULT" not in loop._texts(session.messages))
    harness.check("a later compaction takes its tail from the kept turn on",
                  session.turns_start == len(session.messages) - 2, str(session.turns_start))


def test_the_tail_opens_on_a_reply_and_holds_at_most_its_tokens():
    messages = [HumanMessage(content="ask"), AIMessage(content="a"), ToolMessage(content="r" * 70_000, tool_call_id="c"),
                AIMessage(content="b"), ToolMessage(content="short", tool_call_id="d")]
    start = compaction_service.find_tail_start(messages, turns_start=1, chars_per_token=3.5, frame_tokens=1_000)
    harness.check("the 20,000-token result is past the tail, and the tail opens on the reply after it", start == 3, str(start))
    harness.check(f"a last turn over {compaction_service.TAIL_TOKENS:,} tokens leaves no tail",
                  compaction_service.find_tail_start(messages[:3], turns_start=1, chars_per_token=3.5, frame_tokens=1_000) == 3)
    harness.check("the tail never reaches before where this follow-up's turns begin",
                  compaction_service.find_tail_start(messages, turns_start=4, chars_per_token=3.5, frame_tokens=1_000) == 5)


def test_the_handoff_measures_the_conversation_as_it_is_after_the_compaction():
    session = loop._build_session(fakes.says("## DRAFT\nA refund is split in three. SETTLED\n## NEXT\nSubmit."))
    write_conversation_service._open_follow_up(session, loop.DRAFT)
    turns = [AIMessage(content="OLD TURN", tool_calls=[{"name": "find", "args": {"words": "refund"}, "id": "c1"}]),
             ToolMessage(content="OLD RESULT " + "x" * 50_000, tool_call_id="c1")]
    for message in turns:
        write_conversation_service._append(session, message)
    reason = write_conversation_service._write_compaction(session, loop.DRAFT, "", 0, 2, 6, 141_875, 120_000, 0)
    handoff = next(message.content for message in reversed(session.messages) if "<measured>" in str(message.content))
    harness.check("compacted", reason == "", reason)
    harness.check("the handoff never gives the size from before the compaction", "141,875" not in handoff, handoff[:600])
    size = int(re.search(r"([\d,]+) of 120,000 tokens", handoff).group(1).replace(",", ""))
    # The fixture's starting messages and tail come to a few thousand tokens: far under the size limit of 120,000
    harness.check("it gives the compacted size, a fraction of the size limit", 0 < size < 120_000 // 6, str(size))


def test_a_shorter_summary_that_is_only_a_tool_call_does_not_replace_the_long_one():
    request = _build_request()
    long_text = LONG_SUMMARY
    request.session.model.script.extend([fakes.says(long_text), fakes.says('<call:read_notes>{"chapter": 9}</call>')])
    summary, outcome = compaction_service.write_summary(request, 1_500)
    harness.check("the long summary stays, named over its allowance", summary == long_text.strip() and outcome == "over allowance",
                  str((summary[:30], outcome)))


def test_the_reads_of_a_follow_up_are_its_reading_tools_only():
    request = _build_request()
    rows = [{"followUp": "executive-summary-draft", "calls": [
        {"name": "read_notes", "args": {"chapter": 2}}, {"name": "check_chapter", "args": {"markdown": "A whole draft."}},
        {"name": "cut", "args": {"frame": "frame-3"}}, {"name": "report_problem", "args": {"kind": "x"}},
        {"name": "submit_chapter", "args": {"markdown": "x"}}]}]
    request.session.conversation_log_path.parent.mkdir(parents=True, exist_ok=True)
    request.session.conversation_log_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    reads = compaction_service.compute_measured_values(request)["reads_list_or_none"]
    harness.check("only read_notes is a read", reads.startswith("read_notes ") and ";" not in reads, reads)


def test_the_chapter_check_names_a_cut_set_aside_as_not_in_use():
    from services import document_file_service
    request = _build_request(worker="chapter")
    document_file_service.write_image_store(request.session.ctx.paths, {"cuts": {
        "cut-001": {"frame": "frame-1", "file": "a.png"}, "cut-002": {"frame": "frame-2", "file": "b.png", "discarded": "undone"}}})
    checks = compaction_service.check_summary(request, "I placed cut-001 and cut-002.")
    harness.check("an undone cut is named as no cut in use", checks["unknown_cuts_or_none"] == "cut-002", str(checks))


def test_the_summary_check_says_a_missing_heading_is_not_written():
    checks = compaction_service.check_summary(_build_request(worker="summary"), "Some words before.\n\n## AGENDA SO FAR\n1. One\n")
    harness.check("no TITLE AND SUMMARY heading: not written, never a count of other text",
                  checks["summary_words"] == "not written" and checks["agenda_lines"] == "1", str(checks))


def test_a_frame_named_in_a_chapter_summary_is_no_unknown_entry_id():
    checks = compaction_service.check_summary(_build_request(worker="chapter"), "I looked at F12 and placed nothing yet.")
    harness.check("F12 is a frame, not an entry id", checks["unknown_ids_or_none"] == "none", str(checks))


def test_a_history_message_whose_content_is_a_list_of_strings_keeps_its_words():
    text = compaction_service.render_history([HumanMessage(content=["the refund rule is in chapter two"])])
    harness.check("a plain string part is read", "the refund rule is in chapter two" in text, text)


def test_a_summary_asked_shorter_after_an_empty_one_is_asked_on_the_short_history_again():
    request = _build_request()
    for turn in range(8):
        request.session.messages.extend([HumanMessage(content=f"ask {turn}"), AIMessage(content=f"turn-{turn}")])
    request = dataclasses.replace(request, tail_start=len(request.session.messages))
    request.session.model.script.extend([fakes.says(""), fakes.says(LONG_SUMMARY), fakes.says("short one")])
    compaction_service.write_summary(request, 1_500)
    last_prompt = str(request.session.model.calls[-1][-1].content)
    harness.check("the shortening keeps to the short history that gave the summary", "turn-0" not in last_prompt,
                  last_prompt[:300])


def test_a_chapter_summary_names_only_cuts_that_were_made_defect():
    # A chapter's compaction summary twice wrote frame lines with cut ids never made, asked for "the frame lines as you
    # would write them". Now its prompt says a frame line names only a cut `cut` returned, a frame not cut yet "to cut"
    prompt = " ".join(compaction_prompt_loader.read_summary_prompt("chapter").split())
    harness.check("the chapter summary prompt allows only cut ids the tool returned",
                  "A frame line names only a cut id that `cut` returned to you" in prompt and ": to cut" in prompt, prompt[:200])
