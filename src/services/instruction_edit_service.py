"""Saving an instruction a person edited on the page, once the program can still build every
worker's message from it; it is refused only when it could not.

An instruction file's edit is built into every prompt that reads it, with the edit read as a draft, and refused when a
build fails that does not fail without it (a `{{SHARED:x}}` naming no guideline, an unknown `{{NAME}}`, a calls-path
file losing the heading the writing conversation reads), when it drops a marker its default holds that the program
fills (`{{INJECT:base}}`, `{{NAME}}`), or when phase 3's file loses the sample answer its copied-line check reads. A
compaction text is refused when it drops a `{placeholder}` its default holds, names one its default does not, or holds
a single brace, which the program cannot fill around.
"""

from __future__ import annotations

import re
import string

import config
from models.instruction_errors import InstructionRefusedError
from models.run_errors import PromptBuildError
from prompts import compaction_prompt_loader
from prompts import system_prompt_builder
from prompts import write_messages
from services import instruction_store_service
from utils import instruction_example_utils

__all__ = ["save_instruction", "reset_instruction", "build_preview"]

# The markers a default holds that the program fills, which an edit must keep; `{{SHARED:x}}` may go: a workflow's own
# file may write its own text in place of the shared guideline
_KEPT_MARKER = re.compile(r"\{\{(?:INJECT:[a-z]+|[A-Z_]+)\}\}")
# The writing conversation's texts, one for each kind of recording, built as a run builds them
_WRITE_VARIANTS = ((True, False), (False, False), (True, True), (False, True))


def _list_fields(text: str) -> set[str]:
    """The `{placeholder}` names of a compaction text; a single brace raises ValueError."""

    return {field.split(".")[0].split("[")[0] for _, field, _, _ in string.Formatter().parse(text) if field}


def _check_compaction_text(file_id: str,
                           text: str) -> list[str]:
    """Why the program could not fill a compaction text, or nothing when it can."""

    try:
        fields = _list_fields(text)
    except ValueError as error:
        return [f"{file_id}: a brace the program cannot fill around ({error}); write a brace of the text itself twice, "
                "{{ or }}"]
    default_fields = _list_fields(instruction_store_service.read_default(file_id))
    missing = sorted(default_fields - fields)
    unknown = sorted(fields - default_fields)
    problems = [f"{file_id}: keep {{{name}}}, which the program fills" for name in missing]

    return problems + [f"{file_id}: {{{name}}} is no placeholder the program fills here" for name in unknown]


def _list_item_variants(spec: dict) -> list[dict]:
    """The items a phase's prompt is built for: for each workflow it has an instruction for (or none), no field, and
    each value every `when` rule of the phase reads."""

    workflows = spec.get(system_prompt_builder.WORKFLOWS_KEY, {})
    workflow_items = [{}, *({system_prompt_builder.WORKFLOW_FIELD: workflow} for workflow in workflows)]
    rule_items = [{}, *({rule["field"]: value} for rule in spec.get("when", []) for value in rule["in"])]

    return [{**workflow_item, **rule_item} for workflow_item in workflow_items for rule_item in rule_items]


def _list_build_errors() -> set[str]:
    """Every error building the prompts that read instruction files gives now, as a run builds them."""

    errors: set[str] = set()
    for phase, spec in system_prompt_builder.list_phase_specifications(config.AGENT_CONTEXT_DIR).items():
        for item in _list_item_variants(spec):
            try:
                system_prompt_builder.build_system_prompt(phase, item)
            # A prompt that cannot be built is the finding this check exists for
            except PromptBuildError as error:
                errors.add(f"{phase}: {error}")
    for has_frames, is_pdf in _WRITE_VARIANTS:
        try:
            write_messages.build_write_system_text(ingesting_profile="knowledge", tags=[], has_frames=has_frames,
                                                   is_pdf=is_pdf)
        except PromptBuildError as error:
            errors.add(f"write: {error}")
    try:
        instruction_example_utils.list_example_lines()
    # Phase 3's copied-line check refuses a file with no sample answer
    except ValueError as error:
        errors.add(str(error))

    return errors


def _check_instruction_text(file_id: str,
                            text: str) -> list[str]:
    """Why a run could not build its prompts with `text` as `file_id`, or nothing when it can."""

    default_markers = set(_KEPT_MARKER.findall(instruction_store_service.read_default(file_id)))
    missing = sorted(default_markers - set(_KEPT_MARKER.findall(text)))
    if missing:
        return [f"{file_id}: keep {marker}, which the program fills" for marker in missing]
    before = _list_build_errors()
    with instruction_store_service.use_drafts({file_id: text}):
        after = _list_build_errors()

    return sorted(after - before)


def save_instruction(file_id: str,
                     text: str,
                     account: str,
                     note: str = "",
                     kind: str = "edit") -> dict:
    """Put a person's text in force for `file_id`, or refuse it with every reason the program could not send it."""

    is_compaction = file_id.startswith(f"{compaction_prompt_loader.COMPACTION_DIR}/")
    problems = _check_compaction_text(file_id, text) if is_compaction else _check_instruction_text(file_id, text)
    if problems:
        raise InstructionRefusedError("\n".join(problems))

    return instruction_store_service.save(file_id, text, account=account, note=note, kind=kind)


def reset_instruction(file_id: str,
                      account: str) -> dict:
    """Put the default back in force for `file_id`; the edit stays a version."""

    return instruction_store_service.reset(file_id, account=account)


def build_preview(step: str,
                  workflow: str,
                  file_id: str = "",
                  text: str = "") -> str:
    """The system prompt `step`'s worker in `workflow` would get now, with `text` in place of `file_id` when one is
    given."""

    drafts = {file_id: text} if file_id else {}
    with instruction_store_service.use_drafts(drafts):
        if step == "write":
            return write_messages.build_write_system_text(ingesting_profile="knowledge", tags=[],
                                                          has_frames=workflow != "audio", is_pdf=workflow == "pdf")
        return system_prompt_builder.build_system_prompt(step, {system_prompt_builder.WORKFLOW_FIELD: workflow})
