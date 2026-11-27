"""Phase 3's calls for an audio recording whose ingesting profile declares preparation steps: one call
per step over the whole speech, or per item of an earlier step, each handed its paragraphs and what the earlier steps wrote.

The SYSTEM text is data: `agent-context/instructions/preprocessing.md`, with the step's own file from `base/` that
`context_map.json` gives the item field `preprocessing_step` (`<profile>/<step id>`). Only the human text below is a
template.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate

from models.attempt_request import AttemptRequest
from prompts import system_prompt_builder
from utils import finding_text_utils
from utils import speech_markdown_utils

__all__ = ["PREPROCESSING_PHASE", "PROMPT_FIELD", "build_preprocessing_messages"]

PREPROCESSING_PHASE = "preprocessing"
# The item field `context_map.json`'s rules read to give the call its step's file
PROMPT_FIELD = "preprocessing_step"

PREPARE_MESSAGE = PromptTemplate.from_template("""\
## {STEP_TITLE}: step {STEP_NUMBER} of {STEP_COUNT}

The recording is {RECORDING}.{TAGS}

{SCOPE}

Every item holds these sections, in this order: {SECTIONS}.{COVERAGE}
{EARLIER}
# The paragraphs

{PARAGRAPHS}

Answer once, in markdown: `# {STEP_TITLE}`, then {ITEMS}, each with its sections and its paragraph ids.\
{previous_findings}""")


def _render_paragraphs(paragraphs: list[dict]) -> str:
    rendered = []
    for paragraph in paragraphs:
        span = speech_markdown_utils.render_span(paragraph["first"], paragraph["last"])
        rendered.append(f"## {span} · {paragraph['speaker']}\n{paragraph['text']}")

    return "\n\n".join(rendered)


def build_preprocessing_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message of `preprocessing.md` with the step's own file, and the call's paragraphs."""

    inputs = request.inputs
    step = inputs["step"]
    tags = inputs.get("tags") or []
    item_title = inputs.get("item_title")
    if item_title:
        scope = (f'This request is about one item, item {inputs["item_number"]} of {inputs["item_count"]} of the '
                 f'earlier step: "{item_title}". Its paragraphs are below; write it out as one item.')
        items = f'one "## {item_title}" item'
    else:
        scope = "This request reads the whole speech."
        items = 'one "## <item>" heading per item'
    earlier = str(inputs.get("earlier") or "").strip()
    human_text = PREPARE_MESSAGE.format(
        STEP_TITLE=step["title"], STEP_NUMBER=inputs["step_number"], STEP_COUNT=inputs["step_count"],
        RECORDING=inputs.get("recording") or "the recording",
        TAGS=f"\nThe owner tagged it: {', '.join(tags)}." if tags else "",
        SCOPE=scope, SECTIONS=", ".join(f"### {name}" for name in step["sections"]),
        COVERAGE=("\nEvery paragraph below belongs to at least one item: cite each one under the item it is about."
                  if inputs.get("is_every_paragraph_due") else ""),
        EARLIER=f"\n# What the earlier steps wrote\n\n{earlier}\n" if earlier else "",
        PARAGRAPHS=_render_paragraphs(inputs.get("paragraphs") or []), ITEMS=items,
        previous_findings=finding_text_utils.render_retry_section(request.findings))
    item = {PROMPT_FIELD: f"{inputs['profile']}/{step['id']}"}

    return [SystemMessage(content=system_prompt_builder.build_system_prompt(PREPROCESSING_PHASE, item)),
            HumanMessage(content=[{"type": "text", "text": human_text}])]
