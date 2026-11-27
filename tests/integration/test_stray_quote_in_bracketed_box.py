"""DEFECTS 716, on made data: a frame's notes whose diagram has a box label holding brackets and a stray quote
(`Q["Queue\\n(Accounts [Topic"], Cards [Topic])"]`). FRAME_DIAGRAM_SYNTAX refused it as a quote left open and the
batch was blocked under zero repair. Through the real parser, the program's cleaning of a frame's record and the real
syntax check."""

from support import harness
from services import chain_dispatch_service
from utils import frame_markdown_utils
from utils import mermaid_text_utils

_REPLY = """# Frame 7 · slide

## Take aways
- "events go to the queue" → the arrow to the queue box: every change is published as an event.
- Take aways: the hub publishes its changes to one queue with two topics.

## Facts
- The hub publishes account and card changes to a queue with one topic each.

## Diagram
```mermaid
flowchart LR
    App["App"] --> Hub["Hub"]
    Hub --> Q["Queue\\n(Accounts [Topic"], Cards [Topic])"]
```

## Screen
- slide: white background, the whole frame
  - box "Hub": left
  - box "Queue (Accounts [Topic], Cards [Topic])": right

## Speech visual alignment
The speaker says the events go to the queue while the slide shows its arrow.

## What happens
The presenter shows how the hub publishes changes.

## Builds on
(none)

## Tags
event queue, hub
"""


def test_a_stray_quote_in_a_bracketed_box_label_is_mended_and_draws_defect():
    # FRAME_DIAGRAM_SYNTAX refused the diagram: "a label's quote is not closed"
    record = frame_markdown_utils.parse_answer(_REPLY, False)["frames"][0]
    cleaned = chain_dispatch_service._clean_frame_record(record)
    diagram = mermaid_text_utils.strip_fence(cleaned["diagram"])
    problems = mermaid_text_utils.find_syntax_problems(diagram)
    harness.check("the cleaned diagram has no syntax problem", problems == [], str(problems))
    harness.check("the label keeps its brackets and loses only the stray quote",
                  'Q["Queue\\n(Accounts [Topic], Cards [Topic])"]' in diagram, diagram)
