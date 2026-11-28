"""DEFECTS 726, on made data: a frame's notes whose diagram has box labels written with a UML stereotype
(`PAC[<<Component>> personAuthorizationCheck]`). Mermaid draws them, and FRAME_DIAGRAM_SYNTAX refused them as "a
bracket is not closed", so phase 3 stopped. Through the real parser, the program's cleaning of a frame's record and
the real syntax check."""

from support import harness
from services import chain_dispatch_service
from utils import frame_markdown_utils
from utils import mermaid_text_utils

_REPLY = """# Frame 7 · slide

## Take aways
- "the check runs first" → the first box: the permission check runs before the products are read.
- Take aways: three components run one after another.

## Facts
- A permission check runs before the product list is read.

## Diagram
```mermaid
flowchart LR
    PAC[<<Component>> permissionCheck]
    RP[<<Component>> readProducts]
    PAC --> RP
```

## Screen
- slide: white background, the whole frame
  - box "Component permissionCheck": left
  - box "Component readProducts": right

## Speech visual alignment
The speaker names the permission check while the slide shows its box.

## What happens
The presenter shows the order of the components.

## Builds on
(none)

## Tags
components, permission check
"""


def test_a_stereotype_in_a_box_label_draws_and_is_not_refused_defect():
    # FRAME_DIAGRAM_SYNTAX refused the diagram: "a bracket is not closed"
    record = frame_markdown_utils.parse_answer(_REPLY, False)["frames"][0]
    cleaned = chain_dispatch_service._clean_frame_record(record)
    diagram = mermaid_text_utils.strip_fence(cleaned["diagram"])
    problems = mermaid_text_utils.find_syntax_problems(diagram)
    harness.check("the diagram has no syntax problem", problems == [], str(problems))
    harness.check("the boxes keep their stereotype", "PAC[<<Component>> permissionCheck]" in diagram, diagram)
