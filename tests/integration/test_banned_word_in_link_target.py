"""A chapter's table of contents linked to a heading whose anchor held "frame" (a slug such as "#capacity-time-frame"),
and the document check refused the whole chapter three times, stopping the run. Made-up chapter text, run through the
real document check: a link's target and a heading's anchor pass, the same word in prose is still refused."""

from support import harness
from models.validation_report import ValidationReport
from validators import document_validator

CHAPTER = """## Planning at AcmeSync

- [Capacity and time](#acmesync-capacity-time-frame)
- [Gateway rollout](#gateway-rollout)

### Capacity and time {#acmesync-capacity-time-frame}

Jonas plans the Gateway capacity for each quarter. [T0012]

### Gateway rollout

The rollout starts once the capacity plan is agreed. [T0015]
"""


def _banned_findings(text: str) -> list:
    report = ValidationReport(subject="chapter-02")
    document_validator.check_text(report, "chapter 2", text)
    return [finding.detail for finding in report.findings if finding.code == "DOCUMENT_BANNED_PHRASE"]


def test_a_banned_word_in_a_link_target_and_an_anchor_passes_the_document_check_defect():
    # The check read "frame" inside "](#...-time-frame)" and "{#...-time-frame}" as text, and the chapter was refused
    findings = _banned_findings(CHAPTER)
    harness.check("the link target and the heading anchor pass", findings == [], str(findings)[:300])
    harness.check("the word in prose is still refused",
                  _banned_findings(CHAPTER.replace("Jonas plans", "The frame shows Jonas planning")) != [])
