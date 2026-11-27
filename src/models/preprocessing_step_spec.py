"""One preparation step an ingesting profile declares for phase 3 of an audio recording, as read from
`agent-context/preprocessing/<profile>.json`.

A step reads either the whole speech or each item an earlier step found, and its reply holds, for every item, the
sections it names. Carriers only: the files are read by `services/preprocessing_step_service.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PreprocessingStepSpec", "PreprocessingPlanSpec", "SPEECH_INPUT", "EACH_INPUT_PREFIX"]

# A step's input: the whole speech, or `each:<step id>`, one call per item that earlier step found
SPEECH_INPUT = "speech"
EACH_INPUT_PREFIX = "each:"


@dataclass(frozen=True)
class PreprocessingStepSpec:
    step_id: str
    # How the console, the page and the request name the step: "Each item in detail"
    title: str
    # `speech`, or `each:<an earlier step's id>`
    input: str
    # The `###` sections every item of the reply holds, in this order
    sections: tuple[str, ...]

    @property
    def detailed_step_id(self) -> str | None:
        """The earlier step whose items this step goes through one call each; None for a step over the whole speech."""

        return self.input[len(EACH_INPUT_PREFIX):] if self.input.startswith(EACH_INPUT_PREFIX) else None


@dataclass(frozen=True)
class PreprocessingPlanSpec:
    profile: str
    # Below this many paragraphs the speech is short enough for phase 4 alone, and no step runs. Not measured: set
    # from the plan's "long meeting" and "long lecture", to be tuned on real runs
    min_paragraphs: int = 0
    steps: tuple[PreprocessingStepSpec, ...] = ()
