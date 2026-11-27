"""The system message of phase 4's conversation. REQUIREMENTS.md R12.

One job, stated once: `write.md` holds the conversation's own rules, and the rules of each kind of follow-up are
taken from the instruction the calls path uses for that kind, so no rule exists in two copies. Their "answer once"
lines give way to the submit tool that answers that follow-up.
"""

import re
from pathlib import Path

from support import fixtures as fx
from support import harness

from models.attempt_request import AttemptRequest
from models.run_errors import PromptBuildError
from prompts import document_messages
from prompts import write_messages
from services import run_paths_service
from services.ignore_list_service import IgnoreList


def _build_text() -> str:
    return write_messages.build_write_system_text("knowledge", [], True)


def test_there_is_one_job_and_every_follow_up_has_its_section():
    text = _build_text()
    harness.check("one 'Your job'", text.count("Your job") == 1, str(text.count("Your job")))
    for tool in ("submit_executive_summary_draft", "submit_agenda", "submit_draft_headings", "submit_table_of_contents",
                 "submit_chapter", "submit_final"):
        harness.check(f"{tool} is named", f"`{tool}`" in text)
    harness.check("no follow-up is told to answer once through some function",
                  "Answer once, through the function you were given" not in text)


def test_the_rules_come_from_the_kind_instructions_themselves():
    text = _build_text()
    harness.check("the table of contents' start-of-each-chapter rule", "`first_entry_id`" in text
                  and "The first chapter starts at the first entry" in text)
    harness.check("the section's no-meta-narration rule", "No meta-narration" in text)
    harness.check("the summary's conclusion-first rule", "main conclusion" in text)
    harness.check("the agenda's event and speakers", "`event_name`" in text and "`speakers`" in text)
    harness.check("the title rule, and not the title call's own job", "A claim, not a label" in text
                  and "You are handed every part's title and every topic" not in text)
    harness.check("placeholders are filled", "{{" not in text, text[text.find("{{"):text.find("{{") + 60])


def test_text_about_something_else_than_the_heading_before_starts_its_own_heading_defect():
    # A PDF's disclaimer after its glossary had no heading of the document's and was written under the last term.
    # The draft headings' instruction now says such text starts a heading of its own, however short
    text = " ".join(_build_text().split())
    harness.check("the draft headings are told such text starts its own heading",
                  "Text about something else than the heading before it starts a heading of its own" in text,
                  text[:200])


def test_a_note_saying_how_a_figure_looks_is_cited_never_written_defect():
    # A chapter left a tier chart out and wrote its blocks' colours and shades as a sentence of the document.
    # The writer is now told a note of how a figure or a screen looks is a note about the screen, cited, never written
    text = " ".join(_build_text().split())
    harness.check("the writer is told a figure's looks are a note about the screen",
                  "how a figure or a screen looks: its colours, shades, shapes, sizes and the order its parts stand in"
                  in text, text[:200])


def test_a_needed_screenshot_is_placed_or_replaced_by_a_complete_diagram():
    text = " ".join(_build_text().split())
    harness.check("needed means the frame must be accounted for",
                  "Every frame marked `needed` must be accounted for" in text, text)
    harness.check("a diagram replacement preserves the screenshot's visual information",
                  "every component, relationship, boundary, label and visual distinction" in text, text)
    harness.check("a simplified diagram is used together with the screenshot under its own small heading",
                  "#### Simplified view" in text and "place both" in text.casefold(), text)


def test_frame_and_speech_ids_are_never_written_as_one_range():
    text = " ".join(_build_text().split())
    harness.check("mixed id kinds are comma-separated, never joined as a range",
                  "write `[F<frame>.<note>, S<line>]`, never `[F<frame>.<note>-S<line>]`" in text, text)
    harness.check("a citation closes once",
                  "Close the citation with exactly one `]`" in text, text)


def test_the_draft_opens_the_conversation():
    """R12: one session, one step, one worker. No separate step looks for what was said and never noted: the draft
    opens the conversation, and the speech reaches the writer as entries."""

    text = _build_text()
    order = text[text.index("# How the conversation goes"):text.index("# Reading more of the meeting")]
    first = re.search(r"^1\. (.+)$", order, re.MULTILINE)
    harness.check("the first step of the order is the executive summary draft",
                  first is not None and first.group(1).startswith("The executive summary draft"), order)
    harness.check("no step names the enrichment", "never noted" not in order, order)


def test_a_follow_up_carries_its_data_and_never_the_instruction_again():
    paths = fx.make_paths()
    run_paths_service.ensure_run_paths(paths)
    request = AttemptRequest(job_kind="parts", attempt=1, repository=paths.repository, module=paths.module, paths=paths,
                             ignore=IgnoreList(), inputs={"slug": "parts", "moments": [
                                 {"number": 1, "start": 0.0, "screenKind": "slide", "title": "Billing review",
                                  "facts": "Refunds are split into at most three parts.", "speech": "Welcome."}]})
    text = write_messages.build_follow_up_text(request)
    harness.check("the data", "Refunds are split into at most three parts." in text, text)
    harness.check("never the instruction", "# What a part is" not in text and "Your job" not in text, text)


def test_the_table_of_contents_is_one_follow_up_for_the_whole_document():
    """R12, the owner: "its one stage", "one request". The agent is told the table of contents sorts every entry of the
    document at once, topics per agenda step, a subject keeping its title, and that the program checks the count."""

    text = _build_text()
    order = text[text.index("# How the conversation goes"):text.index("# Reading more of the meeting")]
    harness.check("the order names one table of contents, and no outline per step",
                  "table of contents" in order.casefold() and "one outline per step" not in order.casefold(), order)
    harness.check("the table of contents has its own section, with its submit tool",
                  "# The table of contents" in text and "`submit_table_of_contents`" in text)
    section = text[text.index("# The table of contents"):]
    harness.check("it says every entry comes at once, one chapter per agenda point, and a subject keeps one title",
                  "every entry" in section and "one chapter per agenda point" in section and "same title" in section,
                  section[:1200])
    harness.check("it says the program orders the starts and fills in the entries, so there is nothing to count",
                  "fills in the entries" in " ".join(section.split()),
                  section[:1200])
    harness.check("the batch outline's own job is not sent to the conversation",
                  "one batch of a video established" not in text)


def test_speech_is_written_up_in_the_worker_s_words_never_pasted():
    text = " ".join(_build_text().split())
    harness.check("the instruction says a speech entry is written up as what it establishes, never pasted",
                  "never paste the transcript's words" in text, text[-2000:])


def test_leaving_a_frame_out_is_a_tool_call_never_a_line_of_text():
    text = " ".join(_build_text().split())
    harness.check("the instruction says leave_out is called, never written into the text",
                  "never write a `> **Leave out:**` line" in text, text[-1500:])


def test_a_batch_account_with_no_frames_is_rendered_without_a_frame_range():
    rendered = document_messages.render_batches([{"start": 0.0, "end": 60.0, "summary": "The talk opens."}], False)
    harness.check("the account is shown with its times and no frame range",
                  "0:00 to 1:00: The talk opens." in rendered and "frame" not in rendered.split("order it happened")[-1],
                  rendered)


def test_a_follow_up_of_an_unknown_job_kind_raises_a_named_error():
    request = AttemptRequest(job_kind="section", attempt=1, repository=Path("."), module="x", paths=None,
                             ignore=IgnoreList(), inputs={})
    try:
        write_messages.build_follow_up_text(request)
        outcome = "built a text"
    except PromptBuildError as exc:
        outcome = f"named: {exc}"
    except KeyError as exc:
        outcome = f"bare KeyError {exc}"
    harness.check("the error names the job kind", outcome.startswith("named:") and "section" in outcome, outcome)


def test_a_section_with_no_job_heading_is_refused_by_name():
    try:
        write_messages._rewrite_section("# Something else\n\ntext", "submit_chapter", False)
        outcome = "rewritten"
    except PromptBuildError as exc:
        outcome = f"named: {exc}"
    harness.check("the missing job heading is named", outcome.startswith("named:") and "# Your job" in outcome, outcome)


def test_every_kind_with_a_submit_tool_has_a_text_builder_but_the_draft_summary():
    harness.check("the two tables of job kinds stay in step",
                  set(write_messages._TEXT_BUILDERS) == set(write_messages.SUBMIT_TOOL_BY_KIND) - {"executive_summary_draft"})


def test_a_pdfs_batch_accounts_are_what_its_figures_show_never_how_a_meeting_went():
    """A text-only PDF's batches are its figures read together: told "how the meeting went" and "what the presenter
    set out to show", the summary's worker guessed the document was a meeting's walkthrough."""

    batches = [{"start": 0.0, "end": 60.0, "frames": [1, 2], "summary": "Figure 1 draws the cycle."}]
    for rendered in (document_messages.render_batches(batches, True),
                     document_messages.render_batches_for_agenda(batches, True)):
        harness.check("a PDF's accounts are its figures'", "figures" in rendered and "meeting" not in rendered
                      and "presenter" not in rendered, rendered)
    harness.check("a video's are as before", "How the meeting went" in document_messages.render_batches(batches, False))
    request = AttemptRequest(job_kind="executive_summary_draft", attempt=1, repository=Path("."), module="x", paths=None,
                             ignore=IgnoreList(), inputs={"moments": [], "batches": batches, "isPdf": True})
    text = write_messages.build_follow_up_text(request)
    harness.check("the summary's moments are the document's", "moments of the document" in text
                  and "meeting" not in text, text)


def test_the_writer_is_told_an_unclear_number_is_written_as_said_and_open_defect():
    # a chapter turned a spoken "1,4 degrees in 2026" it found unclear into "similar breaches by 2026"; the writer is
    # now told such a number is written as said, with its doubt, as an open point, never as a vaguer claim
    text = _build_text()
    harness.check("an unclear number is written as said, as an open point",
                  "A number that seems wrong or unclear" in text and "never replaced by a" in text)
    harness.check("and never replaced by a vaguer claim", "vaguer claim" in text)
