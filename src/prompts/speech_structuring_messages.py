"""Phase 2's one call for an audio recording: the whole transcript, line by line, turned
into paragraphs of clean speech, each one speaker's turn on one point. When no tag of the run names an ingesting
profile, the same call picks one from the list.

The SYSTEM text is data, in `agent-context/instructions/speech_structuring.md`. Only the human text below is a template.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate

from models import ingesting_profile_vocabulary
from models.attempt_request import AttemptRequest
from prompts import system_prompt_builder
from utils import clock_utils
from utils import finding_text_utils
from utils import speech_markdown_utils

__all__ = ["build_speech_structuring_messages"]

SPEECH_STRUCTURING_PHASE = "speech_structuring"

SPEECH_MESSAGE = PromptTemplate.from_template("""\
## Turn this transcript into paragraphs of clean speech

The recording is {RECORDING}, {LENGTH} long, {COUNT} lines from {FIRST} to {LAST}.{TAGS}

{LINES}

Answer once, in markdown: `# Speech`, then one "## S<first>-S<last> · Speaker <letter>" paragraph after another, from
{FIRST} to {LAST}, every line in exactly one paragraph.{PROFILE_ASK}{previous_findings}""")

PROFILE_CHOICE_TEXT = """

Also pick the ingesting profile the document of this recording will be written in: the one that fits what the
recording is. Write it as one line `Profile: <name>` right under `# Speech`, before the first paragraph. The profiles:

{PROFILES}"""


def _render_line(line: dict) -> str:
    return f"{line['id']} [{clock_utils.render_clock(line['start'])}] {line['text']}"


def _render_turn(turn: dict) -> str:
    """One speaker's turn: its header, with the ids it covers and the speaker, then its lines."""

    header = f"Turn {speech_markdown_utils.render_span(turn['first'], turn['last'])} · {turn['speaker']}"

    return "\n".join([header, *(_render_line(line) for line in turn["lines"])])


def _render_lines(lines: list[dict]) -> str:
    """The lines one by one, or, when phase 1 told the speakers apart, grouped under each speaker's turn: the program
    decides where a speaker's turn starts and ends, so the call never has to."""

    turns = speech_markdown_utils.list_speaker_turns(lines)
    if not turns:
        return "\n".join(_render_line(line) for line in lines)

    return "\n\n".join(_render_turn(turn) for turn in turns)


def _render_profiles() -> str:
    return "\n".join(f"- `{name}`: {description}"
                     for name, description in ingesting_profile_vocabulary.PROFILE_DESCRIPTIONS.items()
                     if name in ingesting_profile_vocabulary.MODEL_PICKABLE_PROFILES)


def build_speech_structuring_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message of `speech_structuring.md`, and every transcript line with its id and time."""

    lines = request.inputs.get("lines") or []
    tags = request.inputs.get("tags") or []
    tags_line = (f"\nThe owner tagged it: {', '.join(tags)}. Read the tags as a hint of what the recording is."
                 if tags else "")
    human_text = SPEECH_MESSAGE.format(
        RECORDING=request.inputs.get("recording") or "the recording",
        **speech_markdown_utils.describe_transcript_span(lines),
        TAGS=tags_line, LINES=_render_lines(lines),
        PROFILE_ASK=PROFILE_CHOICE_TEXT.format(PROFILES=_render_profiles()) if request.inputs.get("is_profile_asked") else "",
        previous_findings=finding_text_utils.render_retry_section(request.findings))

    return [SystemMessage(content=system_prompt_builder.build_system_prompt(SPEECH_STRUCTURING_PHASE)),
            HumanMessage(content=[{"type": "text", "text": human_text}])]
