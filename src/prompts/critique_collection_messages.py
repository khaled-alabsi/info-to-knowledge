"""Phase 6's one call: the groups the program made, worded as one rule each.

The model is asked for WORDING, never for grouping or counting: which rules are the same rule and how often each was
seen are already decided (`services/critique_collection_service.py`). It may drop a group, and that is the one
judgement it has: a group that turns out to be one meeting's content rather than an anti-pattern is left out of the
rule set.

The SYSTEM text is data, in `agent-context/instructions/critique_collection.md`. Only the human text below is a
template.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import PromptTemplate

from models import output_contract
from models.attempt_request import AttemptRequest
from prompts import system_prompt_builder
from schemas import critique_record
from utils import finding_text_utils

__all__ = ["build_critique_collection_messages"]

CRITIQUE_COLLECTION_MESSAGE = PromptTemplate.from_template("""\
## Word this run's anti-patterns as rules

Every batch of this run criticised its own notes. Below is each anti-pattern they found, already grouped: the rules
that say the same thing are one group, and the counts are the program's, not yours.

{GROUPS}

For each group, write ONE rule for any meeting: a name, what it is, and how to avoid it. Use every wording of the
group - they are the same mistake seen by different batches, and the clearest rule covers all of them.

Leave a group OUT when it is not an anti-pattern at all: one meeting's content, a one-off, or advice that would not
help a worker on a different recording. Dropping a group is a real answer; say nothing about it.

Groups are made from names that read alike, so one mistake told in other words can stand as several groups. Word such
groups as ONE rule whose heading names every number, "## Groups <n>, <m>: <name>": the program adds their counts.

Answer once, in markdown: `# Critique`, then one "## Group <n>: <name>" section per rule you keep, <n> the number of
the group it words (or every number of the groups it joins), with its "{WHAT_IT_IS}" and "{HOW_TO_AVOID}" lines. The number is how the program tells which group
a rule words; the name is yours. Write no frame number and no word of any meeting: this file is read straight into the
instructions every later run is given.{previous_findings}""")


def _render_groups(groups: list[dict]) -> str:
    """One block per group: its name, how often it was seen, and every wording the batches gave it."""

    blocks = []
    for position, group in enumerate(groups, start=1):
        batches, frames = len(group.get("batches") or []), int(group.get("frames") or 0)
        lines = [f"### Group {position}: {group.get('name')}",
                 f"Seen in {batches} batch{'es' if batches != 1 else ''}, {frames} "
                 f"frame{'s' if frames != 1 else ''}."]
        for number, wording in enumerate(group.get("wordings") or [], start=1):
            lines.append(f"Wording {number}:")
            lines += [f"  {label}: {wording.get(field) or ''}" for field, label in critique_record.RULE_LINES.items()]
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def build_critique_collection_messages(request: AttemptRequest) -> list[BaseMessage]:
    """The system message of `critique_collection.md`, and the groups the program made, with every wording each was
    given."""

    groups = request.inputs.get("groups") or []
    human_text = CRITIQUE_COLLECTION_MESSAGE.format(
        GROUPS=_render_groups(groups),
        WHAT_IT_IS=critique_record.RULE_LINES["what_it_is"],
        HOW_TO_AVOID=critique_record.RULE_LINES["how_to_avoid"],
        previous_findings=finding_text_utils.render_retry_section(request.findings))

    return [SystemMessage(content=system_prompt_builder.build_system_prompt(output_contract.CRITIQUE_COLLECTION_PHASE)),
            HumanMessage(content=[{"type": "text", "text": human_text}])]
