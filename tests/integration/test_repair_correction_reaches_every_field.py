"""A repair answer, read by phase 3's real answer reader and applied by the real `apply_addition`, that corrects a
run-together misread in one outline line by its id (DEFECTS 483): the word was fixed in the outline and stayed in the
frame's markdown block and narration, which have no ids for the repair to name. Made data: an invented frame's notes
and an answer in the shape the repair writes."""

from support import harness
from utils import addition_utils, frame_markdown_utils

_RECORD = {
    "frame": 33,
    "screen": ('- slide: white, the whole frame\n  - title "Partnerretries": top left\n'
               '  - list: middle\n    - item "two retries a call"'),
    "facts": ["The Partnerretries slide sets two retries a call."],
    "take_aways": ['"two retries, then the partner is paused" → title "Partnerretries": the limit'],
    "open_questions": [],
    "third_person_description": "A slide titled Partnerretries lists the retry limit.",
    "as_markdown": "# Partnerretries\n\n- two retries a call",
}

_ANSWER = """# Frame 33

## What changed
The title is two words on the slide.

## Edits
- replace O33.2: title "Partner retries": top left
"""


def test_a_repair_s_outline_correction_reaches_the_markdown_and_the_narration_defect():
    # the misread stayed in the markdown block and the narration after the repair fixed the outline
    answer = frame_markdown_utils.parse_answer(_ANSWER, is_addition=True)
    harness.check("the answer reads", not answer["errors"], str(answer["errors"]))
    changed, _, refusals = addition_utils.apply_addition(dict(_RECORD), answer["frames"][0])
    harness.check("the outline is corrected", 'title "Partner retries"' in changed["screen"], changed["screen"])
    harness.check("the markdown block is corrected", changed["as_markdown"].startswith("# Partner retries"),
                  changed["as_markdown"])
    harness.check("the narration is corrected", "titled Partner retries" in changed["third_person_description"],
                  changed["third_person_description"])
    harness.check("no field keeps the misread",
                  not any("Partnerretries" in str(changed.get(field)) for field in
                          ("screen", "facts", "take_aways", "third_person_description", "as_markdown")),
                  str({field: changed.get(field) for field in ("facts", "take_aways")}))
    harness.check("nothing refused", refusals == [], str(refusals))
