"""38c3-fault-injection's chapter 7 (run of 2026-11-05): the draft's ```assembly block under its second heading was never
closed, so it ran to the draft's end and the blocks written after it had no numbers. The list showed it as
`block 12: "```assembly"`, and the writer sent edits for blocks 14 to 23, refused six times in a row (DEFECTS 653).
Made data in that shape (the draft is kept only in the run's memory), through the real block list and the real edit
tool: the list says the block was never closed and names the heading it holds, and an edit closing it is applied."""

from support import harness

from utils import section_markdown_utils

DRAFT = """### Live demo (T07.1)

The exploit script connects to the device [F45.1].

```text
> python sploit.py
```
[F45.3]

### Firmware extraction (T07.2)

The shellcode dumps memory in steps of 16 bytes [F50.1].

```assembly
.thumb
    NOP
[F50.3]

The firmware is stored in OTP memory and cannot be patched [F54.1].
"""


def test_a_code_block_left_open_is_named_with_what_it_holds_defect():
    # Defect: an open code block hid every block after it, and the writer edited numbers that did not exist. Fix: the
    # block list names the open block and what it holds
    listed = section_markdown_utils.render_numbered_blocks(DRAFT)
    line = next((line for line in listed.splitlines() if '"```assembly"' in line), "")
    harness.check("the open block is named as never closed", "never closed" in line, listed)
    number = int(line.split(":")[0].split()[-1])
    closed = ("```assembly\n.thumb\n    NOP\n```\n[F50.3]\n\n"
              "The firmware is stored in OTP memory and cannot be patched [F54.1].")
    edited, errors = section_markdown_utils.apply_block_edits(DRAFT, [{"action": "replace", "block": number,
                                                                       "markdown": closed}])
    harness.check("an edit replacing it with the code closed is applied", not errors and "```\n[F50.3]" in edited,
                  str(errors) + edited[-300:])
