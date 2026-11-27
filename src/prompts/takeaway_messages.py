"""Phase 4's one call of a takeaway run: every transcript line, with its id, time and
speaker, and the request to write what the recording settled and what it left open.

The SYSTEM text is data, in `agent-context/instructions/takeaway.md`. Only the human text below is a template.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate

from models.attempt_request import AttemptRequest
from prompts import system_prompt_builder
from utils import clock_utils
from utils import finding_text_utils
from utils import speech_markdown_utils

__all__ = ["build_takeaway_messages"]

TAKEAWAY_PHASE = "takeaway"

TAKEAWAY_MESSAGE = PromptTemplate.from_template("""\
## Write the takeaway of this recording

The recording is {RECORDING}, {LENGTH} long, {COUNT} lines from {FIRST} to {LAST}.{TAGS}

{LINES}

Answer once, in markdown: `# <title>`, then `## Agreed` and `## Not settled`, every line ending with the transcript
lines it rests on.{previous_findings}""")


def _render_line(line: dict) -> str:
    speaker = f" {line['speaker']}:" if line.get("speaker") else ""

    return f"{line['id']} [{clock_utils.render_clock(line['start'])}]{speaker} {line['text']}"


def build_takeaway_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message of `takeaway.md`, and every transcript line with its id, time and speaker."""

    lines = request.inputs.get("lines") or []
    tags = request.inputs.get("tags") or []
    tags_line = (f"\nThe owner tagged it: {', '.join(tags)}. Read the tags as a hint of what the recording is."
                 if tags else "")
    human_text = TAKEAWAY_MESSAGE.format(
        RECORDING=request.inputs.get("recording") or "the recording",
        **speech_markdown_utils.describe_transcript_span(lines),
        TAGS=tags_line, LINES="\n".join(_render_line(line) for line in lines),
        previous_findings=finding_text_utils.render_retry_section(request.findings))

    return [SystemMessage(content=system_prompt_builder.build_system_prompt(TAKEAWAY_PHASE)),
            HumanMessage(content=[{"type": "text", "text": human_text}])]
