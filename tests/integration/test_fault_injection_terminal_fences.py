"""38c3-fault-injection's chapter 7 (2026-11-05): a section of three terminal outputs, each a ```text block citing a
terminal frame, was refused DOCUMENT_CODE_FENCE_NOT_CLOSED on five checks running, though every block was closed: the
step keeping terminal fences as code read each closing "```" as an opening fence and renamed it "```text" (DEFECTS
654). Made data in the chapter's shape (its draft is not kept as test media), through the real chapter cleaning, the
real terminal-fence step, the real split and block reader and the real open-fence check: no finding."""

from support import harness

from models.validation_report import ValidationReport
from utils import section_markdown_utils
from validators import document_validator

DRAFT = """### Live demo (T07.1)

The exploit script connects to the device and waits [F45.1, F45.2]. The connection output is:
```text
~/Projects/Gateway
> python sploit.py
Trying to connect.
```
[F45.3]

If the connection stalls, a recording is replayed [F47.1]. The replay output shows:
```text
> asciinema play backup.cast
::: Replaying session from backup.cast
```
[F47.3]

The script talks to the device on handle 17 [F49.1].

```text
0000fff0 (Handle: 17): Vendor specific: 02
Sending payload
```
[F49.4]

### Firmware (T07.2)

The firmware sits in OTP memory [F54.1].
"""


def test_a_section_of_terminal_blocks_draws_no_open_fence_finding_defect():
    # Defect: closing fences renamed as openers made every terminal block look open. Fix: only openers are renamed
    markdown = section_markdown_utils.keep_terminal_fences(section_markdown_utils.clean_chapter_markdown(DRAFT),
                                                           {45, 47, 49})
    section = section_markdown_utils.split_chapter_markdown(markdown)[0]
    blocks = section_markdown_utils.parse_section(section)
    report = ValidationReport("heading-013-1")
    document_validator.check_code_fences(report, "heading-013-1", "\n\n".join(block.get("text", "") for block in blocks))
    harness.check("no open-fence finding", "DOCUMENT_CODE_FENCE_NOT_CLOSED" not in harness.codes(report),
                  str([finding.detail for finding in report.findings]))
    harness.check("three closed code blocks, each cited",
                  [block["entry_ids"] for block in blocks if block["kind"] == "code"] == [["F45.3"], ["F47.3"], ["F49.4"]],
                  str(blocks))
