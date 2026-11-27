"""The calls phase 3 makes to prepare an audio recording's notes, one step at a time:
what each call is handed, whether it is done, and how far each step
is. Read by phase 3's node, which runs the calls, and by the page, which counts them.
"""

from __future__ import annotations

from typing import Any

from models.preprocessing_step_spec import PreprocessingStepSpec
from models.run_paths import RunPaths
from services import prepared_file_service
from services import preprocessing_step_service
from services import speech_file_service
from utils import prepared_markdown_utils
from validators import prepared_validator

__all__ = ["list_step_calls", "is_call_done", "describe_progress"]


def _render_earlier(paths: RunPaths,
                    steps: tuple[PreprocessingStepSpec, ...],
                    before: int) -> str:
    """Every file the steps before step `before` wrote, in order, for a later step over the whole speech."""

    texts = []
    for step in steps[:before]:
        files = ([prepared_file_service.build_prepared_path(paths, step.step_id, None)] if step.detailed_step_id is None
                 else prepared_file_service.list_step_paths(paths, step.step_id))
        texts += [path.read_text(encoding="utf-8").strip() for path in files if path.is_file()]

    return "\n\n".join(texts)


def list_step_calls(paths: RunPaths,
                    steps: tuple[PreprocessingStepSpec, ...],
                    at: int,
                    paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The calls of step `at`, each `{"item_number", "item_title", "item_count", "paragraphs", "earlier"}`: one over
    the whole speech, or one per item of the step it goes through, each with only that item's paragraphs and what that
    step wrote about it (none while that step has written nothing)."""

    step = steps[at]
    if step.detailed_step_id is None:
        return [{"item_number": None, "item_title": None, "item_count": None, "paragraphs": paragraphs,
                 "earlier": _render_earlier(paths, steps, at)}]
    detailed = next(earlier for earlier in steps if earlier.step_id == step.detailed_step_id)
    items = (prepared_file_service.read_prepared(paths, detailed.step_id, None) or {"items": []})["items"]
    calls = []
    for number, item in enumerate(items, start=1):
        cited: list[str] = []
        for section in item["sections"]:
            found, _unknown = prepared_markdown_utils.resolve_cited_paragraphs(section["cited"], paragraphs)
            cited += [paragraph_id for paragraph_id in found if paragraph_id not in cited]
        calls.append({"item_number": number, "item_title": item["title"], "item_count": len(items),
                      "paragraphs": [paragraph for paragraph in paragraphs if paragraph["first"] in cited],
                      "earlier": prepared_markdown_utils.render_prepared(detailed.title, {"items": [item]})})

    return calls


def is_call_done(paths: RunPaths,
                 steps: tuple[PreprocessingStepSpec, ...],
                 step: PreprocessingStepSpec,
                 call: dict[str, Any]) -> bool:
    """Whether a call of `step` (one of `steps`) has its file on disk and passing its check: such a call is never asked
    again."""

    is_every_paragraph_due = step.step_id in preprocessing_step_service.list_detailed_step_ids(steps)

    return prepared_validator.validate_prepared_record(paths, preprocessing_step_service.describe_steps((step,))[0],
                                                       call["item_number"], call["paragraphs"],
                                                       is_every_paragraph_due).ok


def describe_progress(paths: RunPaths) -> list[dict[str, Any]]:
    """Each step the run's phase 3 prepares, `{"title", "done", "total"}`: its calls done and how many it makes, `total`
    None while the step it goes through has not listed its items. Empty for a run that prepares nothing."""

    speech = speech_file_service.read_speech(paths)
    steps = preprocessing_step_service.list_due_steps(paths)
    if speech is None or not steps:
        return []
    progress = []
    for at, step in enumerate(steps):
        calls = list_step_calls(paths, steps, at, speech["paragraphs"])
        done = sum(is_call_done(paths, steps, step, call) for call in calls)
        is_total_known = step.detailed_step_id is None or bool(calls)
        progress.append({"title": step.title, "done": done, "total": len(calls) if is_total_known else None})

    return progress
