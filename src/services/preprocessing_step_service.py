"""The preparation steps phase 3 runs for an audio recording, read from `agent-context/preprocessing/<profile>.json`. A profile with no file has no step, and its audio run skips phase 3
as before; one whose speech has fewer paragraphs than its `minParagraphs` skips it too.
"""

from __future__ import annotations

import json
from pathlib import Path

import config
from models.pipeline_errors import PreprocessingDeclarationError
from models.preprocessing_step_spec import EACH_INPUT_PREFIX, SPEECH_INPUT, PreprocessingPlanSpec, PreprocessingStepSpec
from models.run_paths import RunPaths
from services import ingesting_profile_service
from services import speech_file_service

__all__ = ["DECLARATIONS_DIR_NAME", "read_plan", "select_due_steps", "list_due_steps", "list_detailed_step_ids",
           "describe_steps"]

DECLARATIONS_DIR_NAME = "preprocessing"


def _read_step(source: Path,
               declared: dict,
               earlier: list[str]) -> PreprocessingStepSpec:
    """One step of the file, refused by name when it cannot be followed. `earlier` holds the ids of the earlier steps
    over the whole speech: only their items are listed in one file a later step can go through."""

    step_id = str(declared.get("id") or "").strip()
    step_input = str(declared.get("input") or "").strip()
    if not step_id:
        raise PreprocessingDeclarationError(f"{source.name}: a step has no id")
    if not isinstance(declared.get("sections", []), list):
        raise PreprocessingDeclarationError(f"{source.name}: step {step_id!r}: `sections` is a list of section names")
    sections = tuple(str(section).strip() for section in declared.get("sections") or [] if str(section).strip())
    step = PreprocessingStepSpec(step_id=step_id, title=str(declared.get("title") or step_id), input=step_input,
                                 sections=sections)
    if step_input != SPEECH_INPUT and step.detailed_step_id not in earlier:
        raise PreprocessingDeclarationError(f"{source.name}: step {step_id!r} reads {step_input!r}; the input is "
                                            f"{SPEECH_INPUT!r} or {EACH_INPUT_PREFIX}<the id of an earlier step over the whole "
                                            f"speech>")
    if not sections:
        raise PreprocessingDeclarationError(f"{source.name}: step {step_id!r} names no section")

    return step


def read_plan(profile: str,
              root: Path | None = None) -> PreprocessingPlanSpec:
    """The profile's steps, in order; no step when the profile has no file. Raises PreprocessingDeclarationError for a
    file that cannot be followed."""

    source = (root or config.AGENT_CONTEXT_DIR) / DECLARATIONS_DIR_NAME / f"{profile}.json"
    if not source.is_file():
        return PreprocessingPlanSpec(profile=profile)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PreprocessingDeclarationError(f"{source.name}: {error}") from error
    if not isinstance(data, dict):
        raise PreprocessingDeclarationError(f"{source.name}: the file holds no object with a `steps` list")
    steps: list[PreprocessingStepSpec] = []
    for declared in data.get("steps") or []:
        if not isinstance(declared, dict):
            raise PreprocessingDeclarationError(f"{source.name}: a step is not an object: {declared!r}")
        if str(declared.get("id") or "").strip() in [step.step_id for step in steps]:
            raise PreprocessingDeclarationError(f"{source.name}: the step id {declared.get('id')!r} twice")
        steps.append(_read_step(source, declared, [step.step_id for step in steps if step.detailed_step_id is None]))

    try:
        min_paragraphs = int(data.get("minParagraphs") or 0)
    except (TypeError, ValueError) as error:
        raise PreprocessingDeclarationError(f"{source.name}: `minParagraphs` is a whole number") from error

    return PreprocessingPlanSpec(profile=profile, min_paragraphs=min_paragraphs, steps=tuple(steps))


def select_due_steps(profile: str,
                     paragraph_count: int) -> tuple[PreprocessingStepSpec, ...]:
    """The steps `profile` runs on a speech of `paragraph_count` paragraphs: none below its `minParagraphs`."""

    plan = read_plan(profile)

    return plan.steps if paragraph_count >= plan.min_paragraphs else ()


def list_due_steps(paths: RunPaths) -> tuple[PreprocessingStepSpec, ...]:
    """The steps this run's phase 3 runs, for its profile and its `02_speech.md`; none for a video's run or a profile
    with no step."""

    speech = speech_file_service.read_speech(paths)
    if speech is None:
        return ()

    return select_due_steps(ingesting_profile_service.resolve_profile(paths.output_root), len(speech["paragraphs"]))


def list_detailed_step_ids(steps: tuple[PreprocessingStepSpec, ...]) -> set[str]:
    """The steps a later step goes through item by item: their items are what the later step details."""

    return {step.detailed_step_id for step in steps if step.detailed_step_id}


def describe_steps(steps: tuple[PreprocessingStepSpec, ...]) -> list[dict]:
    """Each step as `{"id", "title", "input", "sections"}`: the form the step's check and the page take."""

    return [{"id": step.step_id, "title": step.title, "input": step.input, "sections": list(step.sections)}
            for step in steps]
