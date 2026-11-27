"""The messages of phase 4's conversation. REQUIREMENTS.md R12.

The system message is ONE instruction: `write.md`, the conversation's own rules, then one section per kind of
follow-up taken from the very instruction the calls path sends for that kind. So each rule has one copy, and the
calls path and the conversation cannot drift apart. What changes on the way in: a section's "Your job" becomes what
that follow-up is, headings move one level down, "answer once through the function you were given" names the submit
tool, and the title call contributes only its rules, since its own job (titling from a list of topics) does not
happen in the conversation: `write.md` says what the table of contents is, the one follow-up that sorts every entry
of the document into topics.

A follow-up message is the data message the calls path builds for the same kind, with its closing line naming the
submit tool. It never carries the instruction again.
"""

from __future__ import annotations

import re

from langchain_core.messages import BaseMessage

from models import ingesting_profile_vocabulary
from models.attempt_request import AttemptRequest
from models.run_errors import PromptBuildError
from prompts import document_messages
import config
from prompts import system_prompt_builder

__all__ = ["build_write_system_text", "build_follow_up_text", "render_warning", "render_tag_ask", "SUBMIT_TOOL_BY_KIND"]

WRITE_PHASE = "write"
SUBMIT_TOOL_BY_KIND = {"executive_summary_draft": "submit_executive_summary_draft",
                       "parts": "submit_agenda", "draft_headings": "submit_draft_headings",
                       "table_of_contents": "submit_table_of_contents", "chapter": "submit_chapter",
                       "summary": "submit_final"}
# (heading, the context map's phase key, its submit tool, whether only its "What to return" and rules are kept)
_KIND_SECTIONS = (("the agenda", "parts", "submit_agenda", False),
                  ("the draft headings of a chapter", "draft_headings", "submit_draft_headings", False),
                  ("the table of contents", "table_of_contents", "submit_table_of_contents", False),
                  ("a chapter", "section", "submit_chapter", False),
                  ("the final title, executive summary and agenda", "summary", "submit_final", False),
                  ("the final title", "contents", "submit_final", True))
# The heading a rules-only section starts from
_RETURN_HEADING = "# What to return"
# The heading of a section's job, which becomes what the follow-up is
_JOB_HEADING = "# Your job"
_FOLLOW_UP_HEADING = "# What this follow-up is"
_ANSWER_ONCE = re.compile(r"Answer\s+once,\s+through\s+the\s+function\s+you\s+were\s+given\.")
_ANSWER_THROUGH = re.compile(r"Answer\s+through\s+the\s+function\s+you\s+were\s+given,\s+once\.")
# job kind -> the follow-up's data text; the ask it ends with is rewritten to name the submit tool
_TEXT_BUILDERS = {"chapter": document_messages.build_chapter_text,
                  "draft_headings": document_messages.build_draft_headings_text,
                  "table_of_contents": document_messages.build_table_of_contents_text,
                  "parts": lambda request: _read_text(document_messages.build_parts_messages(request)[-1]),
                  "summary": lambda request: _read_text(document_messages.build_summary_messages(request)[-1])
                  + render_tag_ask()}
_DRAFT_ASK = ("Write the working title and the executive summary draft of this {source}. When the title and every "
              "sentence are decided, answer; never check them a second time.")


# The final answer's tags: the inventory is every tag the run folders hold, so one
# subject is tagged one way across runs
_TAG_ASK = ("\n\nTags: give `tags` up to {limit} short tags naming the subjects this recording treats: its topics, the "
            "systems, products or projects it is about. Never its form (a lecture, a talk, a meeting, a screen "
            "recording, a video or a PDF) or the event it was given at: the program tags those itself.")


def render_tag_ask() -> str:
    """The final follow-up's lines asking for its tags. Other runs' tags are not listed: shown them, a call with no
    thinking copied the most used ones onto unrelated runs, and each copy raised their count for the next; the program
    writes a near copy of an existing tag in that tag's spelling (`run_tags_service.match_inventory_tags`)."""

    return _TAG_ASK.format(limit=config.settings.tags.worker_tags)


def _name_submit_tool(closing_line: re.Pattern,
                      tool: str,
                      text: str) -> str:
    """`text` with its closing "Answer ..." line naming `tool`; raises when the line is no longer there, since a
    follow-up that never names its tool cannot be answered."""

    named, count = closing_line.subn(f"Answer through `{tool}`.", text)
    if not count:
        raise PromptBuildError(f"the text for `{tool}` has no closing line matching {closing_line.pattern!r}")

    return named


def _rewrite_section(text: str,
                     tool: str,
                     is_rules_only: bool) -> str:
    for heading in ([_RETURN_HEADING] if is_rules_only else []) + [_JOB_HEADING]:
        if heading not in text:
            raise PromptBuildError(f"the instruction rewritten for `{tool}` has no {heading!r} heading")
    if is_rules_only:
        text = text[text.index(_RETURN_HEADING):]
    text = text.replace(_JOB_HEADING, _FOLLOW_UP_HEADING, 1)
    text = re.sub(r"^# ", "## ", text, flags=re.MULTILINE)

    return _name_submit_tool(_ANSWER_ONCE, tool, text)


def _read_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content

    return "\n".join(str(part.get("text", "")) for part in message.content if isinstance(part, dict)
                     and part.get("type") == "text")


def build_write_system_text(ingesting_profile: str,
                            tags: list[str],
                            has_frames: bool,
                            is_pdf: bool = False) -> str:
    """`write.md` with the run's ingesting profile's file, then each kind of follow-up's section, adapted from the calls
    path's own instruction, then what the recording itself is (its tags, and whether it has frames)."""

    workflow = "pdf" if is_pdf else "video" if has_frames else "audio"
    sections = [system_prompt_builder.build_system_prompt(
        WRITE_PHASE, {ingesting_profile_vocabulary.PROMPT_FIELD: ingesting_profile,
                      system_prompt_builder.WORKFLOW_FIELD: workflow})]
    for heading, phase, tool, is_rules_only in _KIND_SECTIONS:
        section_text = system_prompt_builder.build_system_prompt(phase, {system_prompt_builder.WORKFLOW_FIELD: workflow})
        body = _rewrite_section(section_text, tool, is_rules_only)
        sections.append(f"# When the follow-up asks for {heading} (`{tool}`)\n\n{body}")
    recording = system_prompt_builder.render_recording_section(tags, has_frames, is_pdf)

    return "\n\n".join([*sections, recording] if recording else sections)


def build_follow_up_text(request: AttemptRequest) -> str:
    """The follow-up message for `request`'s kind: the calls path's data message, naming the submit tool. The draft
    summary, which the calls path has no call for, is its data and its ask."""

    if request.job_kind not in SUBMIT_TOOL_BY_KIND:
        raise PromptBuildError(f"no follow-up text for job kind {request.job_kind!r}")

    tool = SUBMIT_TOOL_BY_KIND[request.job_kind]
    if request.job_kind == "executive_summary_draft":
        moments = request.inputs.get("moments") or []
        is_pdf = bool(request.inputs.get("isPdf"))
        source = "document" if is_pdf else "meeting"

        return (f"These are the {len(moments)} moments of the {source}, in the order they happened:\n\n"
                f"{document_messages.render_moments(moments)}"
                f"{document_messages.render_batches(request.inputs.get('batches') or [], is_pdf)}\n\n"
                f"{_DRAFT_ASK.format(source=source)} Answer through `{tool}`.")

    return _name_submit_tool(_ANSWER_THROUGH, tool, _TEXT_BUILDERS[request.job_kind](request))


def render_warning(count: int,
                   size_limit: int) -> str:
    """The worker's warning that the conversation holds `count` tokens and is compacted at `size_limit`."""

    return (f"Context check: this conversation holds about {count:,} tokens; at {size_limit:,} the program compacts it: "
            f"you write a summary of your work so far, and the conversation restarts from the plan, your notes, the "
            f"follow-ups answered, this follow-up and your summary. The program writes into the new conversation what it "
            f"counted (your reads, your frame looks and cuts, your last answer); your summary must hold your drafted text and your "
            f"decisions. Use `note` for any decision later steps must keep. This is not a request to finish: carry on with the follow-up.")
