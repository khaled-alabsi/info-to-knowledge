"""`report_problem`: the worker's own channel to the people watching the run.

A worker is the only one who sees, while it happens, that a check rejected a right answer or that an instruction can be
read two ways. The only other trace is a rejection line, which says what was refused and never what the worker was
trying to do. The tool writes each report into `worker-feedback.md` in the run folder, one entry per problem, and a
problem reported again raises its count instead of adding an entry.
"""

import re
import tempfile
from pathlib import Path

from support import harness

from models import output_contract
from models import worker_feedback_vocabulary
from prompts import system_prompt_builder
from schemas import feedback_actions
from services import worker_feedback_service
from utils import feedback_report_utils

_SHARED = Path("agent-context/instructions/_shared/worker-feedback.md")


def _report(path: Path,
            kind: str,
            summary: str,
            detail: str = "",
            where: str = "finalize · chapter-02-text · attempt 2") -> str:
    return worker_feedback_service.record_report(path, kind=kind, summary=summary, detail=detail, where=where).reply


def test_a_report_lands_as_one_entry_that_says_where_it_came_from():
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    reply = _report(path, "wrong_rejection", "the check wants a heading the table of contents never listed",
                    "I wrote every listed heading. The finding names a heading that is not in the list.")
    entries = worker_feedback_service.read_reports(path)
    harness.check("the worker is told it is on record", "on record" in reply and not reply.startswith("Refused"), reply)
    harness.check("exactly one entry", len(entries) == 1, str(entries))
    entry = entries[0]
    harness.check("it keeps the kind, the summary and the detail", entry.kind == "wrong_rejection"
                  and entry.summary.startswith("the check wants a heading") and "not in the list" in entry.detail,
                  str(entry))
    harness.check("it says where in the run it was sent", entry.where == "finalize · chapter-02-text · attempt 2",
                  str(entry))
    harness.check("it carries the time", bool(entry.when), str(entry))
    harness.check("the file is markdown a person reads", path.read_text(encoding="utf-8").startswith("# Worker feedback"))


def test_the_same_problem_reported_again_raises_its_count_and_adds_no_entry():
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    _report(path, "unclear_instruction", "two rules about frames disagree")
    second = _report(path, "unclear_instruction", "two rules about frames disagree", "other words this time")
    entries = worker_feedback_service.read_reports(path)
    harness.check("one entry", len(entries) == 1, str(entries))
    harness.check("counted twice", entries[0].times == 2, str(entries))
    harness.check("the worker is told not to report it again", "already reported" in second.lower(), second)


def test_a_second_problem_is_a_second_entry_and_the_first_survives():
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    _report(path, "missing_tool", "first")
    _report(path, "contradiction", "second")
    entries = worker_feedback_service.read_reports(path)
    harness.check("both entries, in order", [entry.summary for entry in entries] == ["first", "second"], str(entries))


def test_an_unknown_kind_is_refused_and_the_refusal_lists_every_kind():
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    reply = _report(path, "its_broken", "x")
    harness.check("refused", reply.startswith("Refused"), reply)
    harness.check("the refusal lists every kind", all(kind in reply for kind in worker_feedback_vocabulary.FEEDBACK_KINDS),
                  reply)
    harness.check("nothing written", not path.exists())


def test_an_empty_summary_is_refused():
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    reply = _report(path, "other", "   ")
    harness.check("refused", reply.startswith("Refused"), reply)
    harness.check("nothing written", not path.exists())


def test_a_detail_holding_markdown_headings_comes_back_whole():
    """A worker may quote an instruction with its own `##` lines; the file must not read them as new entries."""

    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    detail = "The instruction says:\n## A chapter\nWrite every heading.\n\nBut the check wants more."
    _report(path, "wrong_instruction", "a heading line inside the detail", detail)
    _report(path, "other", "next one")
    entries = worker_feedback_service.read_reports(path)
    harness.check("two entries, not three", len(entries) == 2, str(entries))
    harness.check("the detail comes back as it was sent", entries[0].detail == detail, repr(entries[0].detail))


def test_the_console_shows_the_whole_report():
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    lines = worker_feedback_service.record_report(path, "wrong_rejection", "the summary", "line one\nline two",
                                                  "finalize").console_lines
    text = "\n".join(lines)
    harness.check("the kind and the summary on the first line", "WORKER FEEDBACK" in lines[0]
                  and "wrong_rejection" in lines[0] and "the summary" in lines[0], text)
    harness.check("every line of the detail, uncut", "line one" in text and "line two" in text, text)


def test_the_instruction_teaches_exactly_the_kinds_the_tool_accepts():
    text = _SHARED.read_text(encoding="utf-8")
    taught = set(re.findall(r"^- `(\w+)`: ", text, flags=re.MULTILINE))
    kinds = set(worker_feedback_vocabulary.FEEDBACK_KINDS)
    harness.check("every accepted kind is taught", kinds <= taught, str(kinds - taught))
    harness.check("no kind is taught that the tool refuses", taught <= kinds, str(taught - kinds))
    harness.check("it names the tool", worker_feedback_vocabulary.FEEDBACK_TOOL in text)


def test_both_conversation_phases_are_sent_the_instruction():
    for phase in ("write", "review"):
        prompt = system_prompt_builder.build_system_prompt(phase)
        harness.check(f"{phase}: the rendered prompt teaches report_problem",
                      "report_problem" in prompt and "wrong_rejection" in prompt, prompt[-600:])


def test_the_tool_takes_the_fields_the_report_is_written_from():
    fields = set(feedback_actions.ReportProblem.model_fields)
    harness.check("kind, summary and detail", fields == {"kind", "summary", "detail"}, str(fields))
    harness.check("only the detail is optional", not feedback_actions.ReportProblem.model_fields["detail"].is_required()
                  and feedback_actions.ReportProblem.model_fields["summary"].is_required())


def test_a_refused_report_is_never_printed_and_a_kept_one_prints_as_the_file_keeps_it():
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    harness.check("an unknown kind or an empty summary prints nothing",
                  worker_feedback_service.record_report(path, "bogus", "a summary", "", "finalize").console_lines == []
                  and worker_feedback_service.record_report(path, "other", "  ", "", "finalize").console_lines == [])
    lines = worker_feedback_service.record_report(path, "Wrong_Rejection", " the   summary ", "", "finalize").console_lines
    harness.check("the kind as the file stores it", lines and "WORKER FEEDBACK wrong_rejection: the summary" in lines[0],
                  str(lines))


def test_the_feedback_file_says_attempt_never_answer_defect():
    # The file's opening line said "a check that rejected a right answer", and "answer" is the name of a phase 3 step
    # the owner reads it as; it now says "a right attempt"
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    _report(path, "wrong_rejection", "the check counts a label twice")
    opening = path.read_text(encoding="utf-8").split("\n## ")[0]
    harness.check("the opening names a right attempt", "a right attempt" in opening, opening)
    harness.check("no answer in the opening", "answer" not in opening.lower(), opening)


def test_a_report_that_says_nothing_is_not_recorded_defect():
    # A batch's reply carried a `# Feedback` of summary "None" and detail "No feedback was generated", and the file
    # kept it as a problem to judge; a report whose summary says there is nothing to report is now no report
    reply = ("# Frame 3 · slide\n\n## Facts\n- x\n\n# Feedback\n\n## Kind\nother\n\n## Summary\nNone\n\n"
             "## Detail\nNo feedback was generated for this batch.\n")
    from utils import frame_markdown_utils
    harness.check("the reader drops it", frame_markdown_utils.read_feedback(reply) == [],
                  str(frame_markdown_utils.read_feedback(reply)))
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    for empty in ("None", "none.", "N/A", "Nothing to report", "No feedback"):
        reply_text = _report(path, "other", empty)
        harness.check(f"the tool refuses {empty!r}", reply_text.startswith("Refused:"), reply_text)
    harness.check("no file written", not path.exists())
    harness.check("a summary naming a problem is no empty report",
                  not feedback_report_utils.is_empty_report("No ids were accepted for frame 4"))
    harness.check("a real report still stands",
                  not _report(path, "wrong_rejection", "None of the ids were accepted").startswith("Refused:"))


def test_an_empty_report_stored_before_the_refusal_is_never_read_defect():
    # Defect: a report "None", stored before empty reports were refused and copied with its run into a branch, stood on
    # the page as a problem a worker reported. Fix: reading the file passes over an entry that names no problem.
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    path.write_text("# Worker feedback\n\nintro\n\n## other · None\n\n- When: <when>\n- Where: frames-001-005\n- Times: 1\n\n"
                    "> No feedback.\n\n## wrong_rejection · The outline check counted a button twice\n\n- When: <when>\n"
                    "- Where: frames-006-010\n- Times: 2\n", encoding="utf-8")
    entries = worker_feedback_service.read_reports(path)
    harness.check("only the report naming a problem is read",
                  [entry.summary for entry in entries] == ["The outline check counted a button twice"], str(entries))


def test_a_report_answered_at_its_count_is_closed_and_one_raised_since_is_open_defect():
    # Defect: a report judged and fixed hours before stood on the page as unread, and nothing made the watcher answer
    # it. Fix: an answers file beside the reports; a report is open until an answer covers its count.
    path = Path(tempfile.mkdtemp()) / "worker-feedback.md"
    path.write_text("# Worker feedback\n\nintro\n\n"
                    "## wrong_rejection · the repeat check refuses a fixed line two code blocks share\n\n"
                    "- When: 2026-01-01 05:51:22\n- Where: finalize · chapter-02-text · attempt 1\n- Times: 1\n\n"
                    "## unclear_instruction · the draft length rule can be read two ways\n\n"
                    "- When: 2026-01-01 06:10:05\n- Where: finalize · executive-summary-draft · attempt 1\n- Times: 1\n",
                    encoding="utf-8")
    whens = [entry.when for entry in worker_feedback_service.read_reports(path)]
    answers = path.parent / output_contract.WORKER_FEEDBACK_ANSWERS_FILE
    harness.check("with no answer both reports are open", len(worker_feedback_service.list_open_reports(path)) == 2)
    answers.write_text(f"# Answers\n\n- {whens[0]} · times 1 · right: the check is fixed\nnot an answer line\n",
                       encoding="utf-8")
    open_summaries = [entry.summary for entry in worker_feedback_service.list_open_reports(path)]
    harness.check("the answered report is closed, the other open",
                  open_summaries == ["the draft length rule can be read two ways"], str(open_summaries))
    _report(path, "wrong_rejection", "the repeat check refuses a fixed line two code blocks share")
    harness.check("reported again after its answer, it is open again",
                  len(worker_feedback_service.list_open_reports(path)) == 2)
    harness.check("the answer is read by the report's time",
                  worker_feedback_service.read_answers(answers)[whens[0]].text == "right: the check is fixed")
