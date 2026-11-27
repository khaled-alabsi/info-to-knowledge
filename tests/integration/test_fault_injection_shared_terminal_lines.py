"""38c3-fault-injection's chapter 7 (2026-11-05): the demo section quotes three terminal outputs of one exploit script,
each opening with the same prompt and banner ("> python sploit.py", "Trying to connect. Pray the demo gods NOW!").
Every edit writing one of them was refused as writing again what another block says, so the writer could not merge
or extend the outputs (DEFECTS 656). Made data with the outputs' real lines (the slides' public terminal text, the
draft not kept as media), through the real block reader and edit tool: an extended output is applied, and only the
same output written twice is refused."""

from support import harness

from utils import section_markdown_utils

BANNER = "~/Projects/Chipolo\n> python sploit.py\nTrying to connect. Pray the demo gods NOW!"
DRAFT = f"""### Live demo execution and exploit triggering (T07.1)

The exploit script initiates a BLE connection and waits for it [F45.1, F45.2].

```text
{BANNER}
```
[F45.3]

The script then sends the payload over handle 17 [F49.1].

```text
{BANNER}
0000fff0-0000-1000-8000-00805f9b34fb (Handle: 17): Vendor specific: 02
Sending payload
```
[F49.4]

The overwritten callback is triggered [F53.3].

```text
{BANNER}
Overflowing pointer
Trigger vuln
```
[F53.5]
"""


def test_the_demo_s_terminal_outputs_can_be_edited_defect():
    # Defect: outputs sharing their opening lines refused every edit. Fix: a code block is a repeat only when the same
    extended = f"```text\n{BANNER}\n0000fff0-0000-1000-8000-00805f9b34fb (Handle: 17): Vendor specific: 02\n" \
               "Random: 28c1f370\nToken: fc46b7f5280b\nSending payload\n```\n[F49.4]"
    edited, errors = section_markdown_utils.apply_block_edits(
        DRAFT, [{"action": "replace", "block": 5, "markdown": extended}])
    harness.check("the payload output, extended, is applied", errors == [] and "Token: fc46b7f5280b" in edited,
                  str(errors))
    _, errors = section_markdown_utils.apply_block_edits(
        DRAFT, [{"action": "add_after", "block": 7, "markdown": f"```text\n{BANNER}\n```\n[F47.3]"}])
    harness.check("the connection output written a second time is refused", bool(errors), str(errors))
