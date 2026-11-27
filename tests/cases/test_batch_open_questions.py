"""Every batch is asked what it leaves open, then to answer it, and ends as one file (the owner, 2026-09-28).

After a batch's notes, its account and its second look, the same worker is asked twice more: what would a reader who
did not attend still not know, then which of those it can answer from what it was shown. The questions still open and
the answers found go into the batch's one file, into `03_batch_summaries.json`, and from there into `03_notes.md`. The
batch is one file at the end: its frames' notes and its batch, with the frame files and the working files removed.
"""

from langchain_core.messages import AIMessage

from support import fakes
from support import frame_batch_fixtures
from support import fixtures as fx
from support import harness
from services import run_resume_service
from services import frame_records_service

from services import batch_summaries_service
from services import record_file_service
from utils import frame_markdown_utils
from utils import meeting_notes_utils


BATCH_SLUG = "frames-000-001"
RETRY_QUESTION = "Who owns the retry budget?"
TENANT_QUESTION = "Which tenant is meant by the yellow one?"
NOTHING_TO_CHANGE = fakes.second_pass(is_nothing_to_change=True, additions=[])


def _batch_message(open_questions: list[str],
                     answered: list[str] | None = None) -> AIMessage:
    lines = ["# Batch", "", "## Open questions", *([f"- {question}" for question in open_questions] or ["(none)"])]
    if answered is not None:
        lines += ["", "## Answered questions", *([f"- {entry}" for entry in answered] or ["(none)"])]

    return AIMessage(content="\n".join(lines) + "\n")


def _run_batch(*follow_ups: AIMessage):
    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1})
    ctx = frame_batch_fixtures.run_two_frame_batch(group, NOTHING_TO_CHANGE, *follow_ups)

    return ctx


def _read_batch_file(ctx) -> dict:
    """The batch's one summary file: the account, the mood, the open questions and the answers found."""

    text = record_file_service.build_record_path(ctx.paths, f"{BATCH_SLUG}-batch-summary").read_text(encoding="utf-8")

    return frame_markdown_utils.parse_answer(text, is_addition=False)


def test_the_questions_are_found_then_answered_and_land_in_the_batch_file():
    answers = _batch_message([TENANT_QUESTION], [f"{RETRY_QUESTION} → Only Jonas, said in frame 1."])
    ctx = _run_batch(_batch_message([RETRY_QUESTION, TENANT_QUESTION]), answers)
    harness.check("the batch, its critique, the repair, the questions, then the answers", ctx.model.call_count == 5,
                  f"calls={ctx.model.call_count}")
    parsed = _read_batch_file(ctx)
    harness.check("the file holds the questions still open", parsed["batch"]["open_questions"] == [TENANT_QUESTION],
                  str(parsed["batch"]))
    harness.check("and the answered ones, each with its answer after an arrow",
                  parsed["batch"]["answered_questions"] == [f"{RETRY_QUESTION} → Only Jonas, said in frame 1."],
                  str(parsed["batch"]))
    batch = batch_summaries_service.read_batch_summaries(ctx.paths)[0]
    harness.check("`03_batch_summaries.json` keeps both lists for the notes file",
                  batch["openQuestions"] == [TENANT_QUESTION] and len(batch["answeredQuestions"]) == 1, str(batch))


def test_a_question_the_answers_step_sets_aside_never_stays_open_defect():
    # Defect: questions about a screen detail the talk never raises ("what does the status line mean") stayed open
    # and reached the document's open points, since the answers step could only answer or leave open (DEFECTS 486,
    # 580). Fix: a third section, "## Not a question", takes them with why, and they leave the open questions
    aside = "What does the status line \"Synced 4s ago\" mean?"
    answers = AIMessage(content="\n".join([
        "# Batch", "", "## Open questions", "(none)", "", "## Answered questions",
        f"- {RETRY_QUESTION} → Only Jonas, said in frame 1.", "", "## Not a question",
        f"- {aside} → a detail of the screen the talk never raises"]) + "\n")
    ctx = _run_batch(_batch_message([RETRY_QUESTION, aside]), answers)
    batch = batch_summaries_service.read_batch_summaries(ctx.paths)[0]
    harness.check("the answers are accepted, the set-aside question counted", ctx.model.call_count == 5,
                  f"calls={ctx.model.call_count}")
    harness.check("no question is left open, the answer kept",
                  batch["openQuestions"] == [] and len(batch["answeredQuestions"]) == 1, str(batch))


def test_a_batch_ends_as_one_file_per_frame_and_one_summary():
    ctx = _run_batch(_batch_message([RETRY_QUESTION]), _batch_message([], [f"{RETRY_QUESTION} → Jonas."]))
    summary = _read_batch_file(ctx)
    harness.check("the summary holds the account and the mood", summary["batch"]["batch_summary"]
                  and summary["batch"]["atmosphere"], str(summary["batch"]))
    harness.check("and the batch's frames are not in it", summary["frames"] == [], str(summary["frames"]))
    for slug in ("frame-000", "frame-001"):
        text = record_file_service.build_record_path(ctx.paths, slug).read_text(encoding="utf-8")
        harness.check(f"{slug}.md is kept, holding its own notes", text.startswith(f"# Frame {int(slug[-3:])}"), text[:60])
    leftovers = [f"{BATCH_SLUG}-second-pass", f"{BATCH_SLUG}-open-questions", f"{BATCH_SLUG}-answers", BATCH_SLUG]
    present = [slug for slug in leftovers if record_file_service.build_record_path(ctx.paths, slug).exists()]
    harness.check("no working file and no merged batch file is left", present == [], str(present))
    accepted = {entry["slug"] for entry in frame_records_service.read_frame_records(ctx.paths)}
    harness.check("the accepted notes are still in `02_frames.json`", accepted == {"frame-000", "frame-001"}, str(accepted))


def test_a_batch_with_nothing_open_is_not_asked_to_answer():
    ctx = _run_batch(_batch_message([]))
    harness.check("the batch, its critique, the repair and the questions, and no answering call",
                  ctx.model.call_count == 4,
                  f"calls={ctx.model.call_count}")
    batch = _read_batch_file(ctx)["batch"]
    harness.check("both lists are empty in its file", batch["open_questions"] == []
                  and batch["answered_questions"] == [], str(batch))


def test_a_batch_whose_answers_do_not_pass_is_blocked_and_phase_3_stops():
    """The owner: "there are no continue after block". An answers call that loses a question, or an open-questions
    call that cannot be read, blocks the whole batch, so a resume asks it again."""

    lost_one = _batch_message([], [f"{RETRY_QUESTION} → Jonas."])
    ctx = _run_batch(_batch_message([RETRY_QUESTION, TENANT_QUESTION]), lost_one)
    statuses = [ctx.store.get_item(slug)["status"] for slug in ("frame-000", "frame-001")]
    harness.check("an answer that loses a question blocks both frames of the batch", statuses == ["blocked", "blocked"],
                  str(statuses))
    harness.check("and the reason names the answers call",
                  f"{BATCH_SLUG}-answers" in ctx.store.get_item("frame-000").get("blockedReason", ""),
                  str(ctx.store.get_item("frame-000")))
    harness.check("a blocked batch leaves no notes in the frames file, so phase 3 never reads as finished",
                  not any(record["slug"] in ("frame-000", "frame-001")
                          for record in frame_records_service.read_frame_records(ctx.paths))
                  and 3 not in run_resume_service.list_finished_phase_numbers(ctx.paths),
                  str([record["slug"] for record in frame_records_service.read_frame_records(ctx.paths)]))
    ctx = _run_batch(AIMessage(content="# Frame 0\n\n## What changed\nnothing\n"))
    statuses = [ctx.store.get_item(slug)["status"] for slug in ("frame-000", "frame-001")]
    harness.check("an open-questions answer that cannot be read blocks the batch too", statuses == ["blocked", "blocked"]
                  and f"{BATCH_SLUG}-open-questions" in ctx.store.get_item("frame-001").get("blockedReason", ""),
                  str(ctx.store.get_item("frame-001")))


def test_the_notes_file_prints_the_whole_batch_summary_after_its_last_frame():
    integration = {"entries": [], "unreadSpeech": [], "contradictions": [],
                   "frames": [{"index": 31, "timestamp": 10.0, "screenKind": "slide"},
                              {"index": 32, "timestamp": 15.0, "screenKind": "slide"},
                              {"index": 37, "timestamp": 20.0, "screenKind": "slide"}],
                   "batchSummaries": [{"frames": [31, 32], "start": 10.0, "end": 20.0, "source": "batch",
                                       "summary": "The presenter opens the billing page and explains the refund limit.",
                                       "atmosphere": "Plain, nobody speaks but the presenter.",
                                       "openQuestions": [TENANT_QUESTION],
                                       "answeredQuestions": [f"{RETRY_QUESTION} → Jonas."]}]}
    sections = [text for _, text in meeting_notes_utils.list_note_sections(integration)]
    position = next((index for index, text in enumerate(sections) if "· the batch" in text), -1)
    harness.check("the section names the batch's frames", position >= 0 and "F31 to F32" in sections[position],
                  str(sections))
    if position < 0:
        return
    harness.check("it holds the story and the mood",
                  "Story of the batch: The presenter opens the billing page" in sections[position]
                  and "Atmosphere: Plain, nobody speaks but the presenter." in sections[position],
                  sections[position])
    harness.check("and what is open and what was answered",
                  f"Open: {TENANT_QUESTION}" in sections[position] and f"Answered: {RETRY_QUESTION} → Jonas." in sections[position],
                  sections[position])
    harness.check("it comes after frame 32 and before frame 37",
                  "## F32" in sections[position - 1] and "## F37" in sections[position + 1], str(sections))


def test_a_bulleted_none_is_no_entry():
    text = "# Batch\n\n## Open questions\n- What is RP?\n\n## Answered questions\n- (none)\n"
    batch = frame_markdown_utils.parse_answer(text, is_addition=False)["batch"]
    harness.check("the empty mark written as a bullet leaves the section empty",
                  batch["open_questions"] == ["What is RP?"] and batch["answered_questions"] == [], str(batch))


def test_an_answer_with_a_line_more_than_asked_keeps_the_batch():
    """Eleven accepted frames were blocked because the answers follow-up returned two lines for one question
    (2026-10-07). A line more is the worker splitting a question; a line fewer is a question nobody answered."""

    two_lines = _batch_message([TENANT_QUESTION], [f"{RETRY_QUESTION} → Jonas."])
    ctx = _run_batch(_batch_message([RETRY_QUESTION]), two_lines)
    statuses = [ctx.store.get_item(slug)["status"] for slug in ("frame-000", "frame-001")]
    harness.check("one question answered in two lines keeps both frames", statuses == ["written", "written"],
                  str(statuses))


def test_an_answer_that_only_repeats_its_question_stays_open_defect():
    # An answer to "why does the speech say 60 where the screen reads 1-63?" repeated both numbers and said nothing of
    # why, and stood as answered. An answer adding at most two words beyond its question now goes back among the open
    why = "Why does the speaker say 40 seats while the Gateway form allows 1-45?"
    restated = f"{why} → The speaker (frame 1) mentions 40 seats, while the form shows 1-45."
    answers = _batch_message([], [restated, f"{RETRY_QUESTION} → Only Jonas may change it, said in frame 1."])
    ctx = _run_batch(_batch_message([why, RETRY_QUESTION]), answers)
    parsed = _read_batch_file(ctx)
    harness.check("the restated answer is open again, the real one stays answered",
                  parsed["batch"]["open_questions"] == [why]
                  and parsed["batch"]["answered_questions"] == [f"{RETRY_QUESTION} → Only Jonas may change it, said in frame 1."],
                  str(parsed["batch"]))
