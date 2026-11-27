"""The settings that change how long a video run takes, as the page's Workflow speed menu shows them (the owner: "a
drop-down ... shows parameter that affect the speed so the user can configure them"). Each preset of `speedPresets`
sets them; a run keeps its own in `run-speed.json` (`services/run_speed_service.py`)."""

from __future__ import annotations

__all__ = ["SPEED_SETTINGS", "SPEED_KEYS", "CUSTOM", "BOOLEAN", "NUMBER", "CHOICE"]

# A setting's kind: a switch, a whole number, or one of named values
BOOLEAN = "boolean"
NUMBER = "number"
CHOICE = "choice"
# The name a run's speed takes once one of its values differs from every preset
CUSTOM = "custom"

# Each setting by its playbook name, in the order the menu lists them: what it is in words, what it does, its kind and,
# for a choice, its values with their names
SPEED_SETTINGS: tuple[dict, ...] = (
    {"key": "video.workflow", "label": "Frame reading and writing", "kind": CHOICE,
     "hint": "full notes with the large model and a document written chapter by chapter, or a small model's quick "
             "description of each frame and the document in one call",
     "options": (("quality", "Full notes, chapter by chapter"), ("fast-processing", "Quick descriptions, one call"))},
    {"key": "framesProcessing.framesPerCall", "label": "Frames per call", "kind": NUMBER, "min": 1, "max": 20,
     "hint": "how many frames one call of phase 3 reads: more is fewer calls, each longer"},
    {"key": "framesProcessing.isCritiqueOn", "label": "Critique", "kind": BOOLEAN,
     "hint": "after each batch the model lists what went wrong in its notes"},
    {"key": "framesProcessing.isCritiqueRepairOn", "label": "Fix from the critique", "kind": BOOLEAN,
     "hint": "a call that fixes the notes from the critique's list"},
    {"key": "framesProcessing.isOpenQuestionsOn", "label": "Open questions and answers", "kind": BOOLEAN,
     "hint": "after each batch the model lists what a reader still cannot know, then answers what it can"},
    {"key": "framesProcessing.repairCalls", "label": "Repair rounds", "kind": NUMBER, "min": 0, "max": 10,
     "hint": "how often the frames a batch did not finish are asked again together; at 0 with no single tries, a "
             "refused frame stops the run"},
    {"key": "framesProcessing.singleFrameAttempts", "label": "Single-frame tries", "kind": NUMBER, "min": 0, "max": 10,
     "hint": "how often a frame still refused is asked alone before it stops the run"},
    {"key": "budgets.askWhyAfterRejection", "label": "Ask why after a rejection", "kind": BOOLEAN,
     "hint": "after a rejected answer in phases 4 and 5 the worker first says what was at fault"},
    {"key": "review.isOn", "label": "Review (phase 5)", "kind": BOOLEAN,
     "hint": "a critic reads the document, finds what a reader cannot know yet and fills it from the recording"},
    {"key": "critiqueCollection.isOn", "label": "Critique collection (phase 6)", "kind": BOOLEAN,
     "hint": "the critiques' rules grouped into the run's rule set"},
)
SPEED_KEYS: tuple[str, ...] = tuple(setting["key"] for setting in SPEED_SETTINGS)
