"""The page's tree of instructions: each workflow, its phases, each step its worker is
sent, and the files that step is built from (its own instruction, the shared guidelines it includes, the documents
`context_map.json` adds, its compaction texts), each with whether a person's edit is in force.
"""

from __future__ import annotations

import config
from models import instruction_catalogue_vocabulary as vocabulary
from prompts import compaction_prompt_loader
from prompts import system_prompt_builder
from services import instruction_store_service
from services import worker_view_service

__all__ = ["build_catalogue", "describe_file"]

_INJECTED_KINDS = ("base", "contracts", "templates")  # the folders `context_map.json` adds documents from
# A `when` rule on the medium adds its documents only to the workflow of that medium; a rule on any other field (the
# ingesting profile, a preparation step) adds them wherever the step is sent
_MEDIUM_WORKFLOWS = {medium: vocabulary.PDF for medium in ("pdf", "pdf-figure")}
# The fixed compaction files, as the page names them
_COMPACTION_TITLES = {
    "conversation/tail-line.md": "Compaction: the line under a handoff when the last turns were kept",
    "conversation/write-shorter.md": "Compaction: asking again for a shorter summary",
    "cut-thinking/handoff.md": "Compaction of a cut thinking: what the next attempt opens with",
    "cut-thinking/finish.md": "Compaction of a cut thinking: the answer written from its summary",
    "cut-thinking/finish-mistakes.md": "Compaction of a cut thinking: a critique's rules from the mistakes found",
}


def _find_instruction_file(spec: dict,
                           workflow: str) -> str:
    """The instruction file a step's worker in `workflow` is built on: the step's own, or its workflow's."""

    name = spec.get(system_prompt_builder.WORKFLOWS_KEY, {}).get(workflow, spec["instruction"])

    return f"instructions/{name}"


def _list_shared_files(file_id: str) -> list[str]:
    """The shared guidelines the text in force of `file_id` includes, in the order it names them."""

    names = system_prompt_builder.SHARED.findall(instruction_store_service.read_active(file_id))

    return [f"instructions/_shared/{name}.md" for name in dict.fromkeys(names)]


def _list_injected_files(spec: dict,
                         workflow: str) -> list[str]:
    """The documents `context_map.json` adds to a step's prompt in `workflow`: always, or by a `when` rule."""

    files = [f"{kind}/{name}" for kind in _INJECTED_KINDS for name in spec.get(kind, [])]
    for rule in spec.get("when", []):
        rule_workflows = {_MEDIUM_WORKFLOWS.get(value, "") for value in rule["in"]} if rule["field"] == "medium" else set()
        if rule_workflows and workflow not in rule_workflows:
            continue
        files += [f"{kind}/{name}" for kind in _INJECTED_KINDS for name in rule.get(kind, [])]

    return list(dict.fromkeys(files))


def _list_compaction_files(step: str) -> list[str]:
    """The compaction texts a step's worker is sent when its work outgrows its room."""

    workers = [worker for worker, owner in vocabulary.COMPACTION_WORKER_STEPS.items() if owner == step]
    files = [f"{compaction_prompt_loader.CONVERSATION_DIR}/{worker}/{name}.md" for worker in workers
             for name in ("summary", "handoff")]
    if workers:
        files += [f"{compaction_prompt_loader.CONVERSATION_DIR}/{name}" for name in vocabulary.CONVERSATION_SHARED_FILES]
    if step in compaction_prompt_loader.CUT_THINKING_STEPS:
        files.append(f"{compaction_prompt_loader.CUT_THINKING_DIR}/{step}.md")
        files += [f"{compaction_prompt_loader.CUT_THINKING_DIR}/{name}" for name in vocabulary.CUT_THINKING_SHARED_FILES]
    if step == "critique":
        files.append(f"{compaction_prompt_loader.CUT_THINKING_DIR}/finish-mistakes.md")

    return files


def _list_step_files(step: str,
                     spec: dict,
                     workflow: str) -> list[str]:
    """Every file a step's worker in `workflow` is built from, its own instruction first."""

    instruction = _find_instruction_file(spec, workflow)
    shared = _list_shared_files(instruction)
    nested = [file_id for shared_id in shared for file_id in _list_shared_files(shared_id)]

    return list(dict.fromkeys([instruction, *shared, *nested, *_list_injected_files(spec, workflow),
                               *_list_compaction_files(step)]))


def _find_file_title(file_id: str,
                     step_of_file: dict[str, str]) -> str:
    """How the page names one file: by the step it is the instruction of, or by what it holds."""

    if file_id in step_of_file:
        return vocabulary.STEP_TITLES[step_of_file[file_id]]
    name = file_id.rsplit("/", 1)[-1].removesuffix(".md")
    if file_id.startswith("instructions/_shared/"):
        return f"Shared guideline: {name.replace('_', ' ').replace('-', ' ')}"
    compaction_name = file_id.removeprefix(f"{compaction_prompt_loader.COMPACTION_DIR}/")
    if compaction_name in _COMPACTION_TITLES:
        return _COMPACTION_TITLES[compaction_name]
    parts = compaction_name.split("/")
    if parts[0] == "conversation" and parts[1] in vocabulary.COMPACTION_WORKER_TITLES:
        what = "summary prompt" if name == "summary" else "message after the compaction"
        return f"Compaction of the {vocabulary.COMPACTION_WORKER_TITLES[parts[1]]}: {what}"
    if parts[0] == "cut-thinking":
        return f"Compaction of a cut thinking: {vocabulary.STEP_TITLES.get(name, name).lower()}"

    return f"{file_id.split('/', 1)[0].capitalize()} document: {name.replace('-', ' ')}"


def _find_file_kind(file_id: str) -> str:
    """Which group a file stands in: an instruction, a shared guideline, a document added by a rule, a compaction."""

    if file_id.startswith("instructions/_shared/"):
        return "shared"
    if file_id.startswith(f"{compaction_prompt_loader.COMPACTION_DIR}/"):
        return "compaction"

    return "instruction" if file_id.startswith("instructions/") else "document"


def describe_file(file_id: str) -> dict:
    """One file as the page shows it: its title, its group, its texts and the steps that send it."""

    catalogue = build_catalogue()
    entry = next((file for file in catalogue["files"] if file["id"] == file_id), None)
    described = instruction_store_service.describe(file_id)

    return {**described, **(entry or {"title": file_id, "kind": _find_file_kind(file_id), "used_by": []})}


def build_catalogue() -> dict:
    """Every workflow with its phases and steps, each step's files, and every editable file with its state."""

    specs = system_prompt_builder.list_phase_specifications(config.AGENT_CONTEXT_DIR)
    used_by: dict[str, list[dict]] = {}
    step_of_file: dict[str, str] = {}
    workflows = []
    for workflow, title in vocabulary.WORKFLOW_TITLES.items():
        phases: dict[int, list[dict]] = {}
        for step, senders in vocabulary.STEP_SENDERS.items():
            if workflow not in senders or step not in specs:
                continue
            files = _list_step_files(step, specs[step], workflow)
            step_of_file[files[0]] = step
            for file_id in files:
                used_by.setdefault(file_id, []).append({"workflow": workflow, "step": step})
            phases.setdefault(worker_view_service.INSTRUCTION_PHASE_NUMBERS[step], []).append(
                {"key": step, "title": vocabulary.STEP_TITLES[step], "who": worker_view_service.INSTRUCTION_USED_BY.get(step, ""),
                 "variants": list(senders[workflow]), "files": files})
        # a variant that names a phase otherwise (direct draft's phases 3 and 4) carries its own title
        phase_list = [{"number": number, "title": vocabulary.PHASE_TITLES[workflow][number], "steps": steps,
                       "variant_titles": {variant: titles[number]
                                          for variant, titles in vocabulary.VARIANT_PHASE_TITLES.get(workflow, {}).items()
                                          if number in titles}}
                      for number, steps in sorted(phases.items())]
        variant_list = [{"key": key, "title": name} for key, name in vocabulary.WORKFLOW_VARIANTS[workflow].items()]
        workflows.append({"key": workflow, "title": title, "variants": variant_list, "phases": phase_list})
    edited = set(instruction_store_service.list_edited())
    files = [{"id": file_id, "title": _find_file_title(file_id, step_of_file), "kind": _find_file_kind(file_id),
              "used_by": used_by.get(file_id, []), "is_edited": file_id in edited,
              "is_default_changed": file_id in edited and instruction_store_service.describe(file_id)["is_default_changed"]}
             for file_id in instruction_store_service.list_editable_files()]

    return {"workflows": workflows, "files": files}
