### A Mermaid diagram must draw

A diagram that breaks these rules shows the reader an error in its place, so check each line before you write it.

- Write every label in double quotes, a box's and an arrow's: `Order["Sends invoice (paper)"] -->|"Pays (by card)"| Bill["Billing"]`.
  A bracket, a colon, a slash or a comma is then safe inside it.
- Check a diagram you copy from a note or a figure the same way.

| Never | Write |
|---|---|
| `Info[Info page (optional)]`: a bracket in a box's label ends the label | `Info["Info page (optional)"]` |
| `A -->|Sends invoice (paper)| B`: a bracket in an arrow's label ends the label | `A -->|"Sends invoice (paper)"| B` |
| `Poll -->|returns "OK"| Wait`: a quote mark inside a label | `Poll -->|"returns OK"| Wait`: no quote mark inside the quotes |
| `B -->|"2. Send the query"| M`: a label opening like a list item ("2. ", "1) ", "- ") is drawn as "Unsupported markdown: list" | `B -->|"(2) Send the query"| M` |
| `Check["Page open] --> Next`: a label's quote left open | `Check["Page open"] --> Next` |
| `Q["Queue (Accounts [Topic"], Cards [Topic])"]`: a quote mark inside a label that holds brackets | `Q["Queue (Accounts [Topic], Cards [Topic])"]`: two quote marks only, round the whole label |
| A label or an arrow split over two lines, several statements on one line, or a `<br/>` between statements | One whole statement on each line |
| A sentence in a Mermaid fence | Write it as a sentence, with no fence |
| A label such as `(flowchart TD)`, then the diagram in a second fence, then a note | The diagram alone, starting with its type (`flowchart TD`), with no label, note or second fence |
| `Start(("")) --> Open["openCart"]`: a box with no label | `Start(("Start")) --> Open["openCart"]`: every box has a label |
| `Close --> end["Done"]`: `end` closes a group | `Close --> Done["Done"]`: `end` never names a box |
| `subgraph VOFE` where a box is also `VOFE`: a group with a box's id | `subgraph G_vofe["VOFE"]`: a group's id starts with `G_` and names no box |
| `Old -->|"Migration"| G_target["Target"]` with `subgraph G_target`: a label on a group | `Old -->|"Migration"| Core["Core service"]` inside `subgraph G_target`: a labelled arrow points at a box |
