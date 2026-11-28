"""A published document's flowchart whose arrows were labelled as numbered steps ("2. generate embedding"): Mermaid read
each label as a markdown list and drew "Unsupported markdown: list" in its place, on a public talk's document from both
the program and a skill run. Made data: an invented document holding such a diagram, mended the way every document's
diagrams are."""

from support import harness

from utils import mermaid_text_utils

DOCUMENT = """# AcmeSync search

The search runs in four steps. [F12, S0041]

```mermaid
flowchart LR
  U["User searches"] --> B["Gateway"]
  B -->|"2. ask the model"| M["Model"]
  M -->|3) return the vector| B
  B -->|"- run the query"| D["Database"]
  D --> L[1. Results listed]
```

The gateway keeps 1.5 GB of vectors in memory. [S0042]
"""


def test_a_document_diagram_with_list_shaped_labels_is_mended_to_draw_defect():
    # Each label opening like a list item reached the reader as an error; now it is written "(2) ask the model", and a
    # number inside a label ("1.5 GB") or the text around the diagram stays as it was
    mended = mermaid_text_utils.repair_fenced_blocks(DOCUMENT)
    harness.check("a numbered arrow label is written (2)", '|"(2) ask the model"|' in mended, mended)
    harness.check("a label numbered with a bracket is written (3)", '"(3) return the vector"' in mended, mended)
    harness.check("a bulleted arrow label loses its dash", '|"run the query"|' in mended, mended)
    harness.check("a numbered box label is written (1)", 'L["(1) Results listed"]' in mended, mended)
    harness.check("the text outside the diagram stays", "1.5 GB of vectors" in mended, mended)
    block = mended.split("```mermaid\n", 1)[1].split("```", 1)[0]
    harness.check("the mended diagram passes the check", not mermaid_text_utils.find_syntax_problems(block), block)
