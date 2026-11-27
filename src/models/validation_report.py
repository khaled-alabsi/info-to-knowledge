# <!-- sync: seed/models.validation_report v1 -->
"""The decision on one artifact, and the findings behind it.

Everything that produces one of these judges FILES, never a worker's own account of what it did. A
worker's self-report, its stdout and its exit code are all recorded in the logs and none of them
contribute to a decision: exit 1 with the work complete and exit 0 with nothing written have both
been observed.

Each check has a stable code. The code is what goes into the retry prompt, so a second attempt is
told precisely what was wrong instead of being asked to try harder.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Finding", "ValidationReport"]


@dataclass
class Finding:
    code: str
    detail: str
    # target  - invalidates only the item under test
    # shared  - invalidates the whole attempt, because a shared file was damaged
    scope: str = "target"
    # What to DO about it, for the retry and repair prompts. `detail` says what is wrong; a worker
    # given only that goes and works out how to fix it - re-reading contracts, opening another
    # item's output to copy the shape - and spends its budget rediscovering something the check
    # already knew. Observed: a repair attempt whose only job was to write a graph fragment opened
    # a different chain's fragment "to understand the format", which the task file already gave it.
    fix: str = ""

    def as_dict(self) -> dict:
        record = {"code": self.code, "detail": self.detail, "scope": self.scope}
        if self.fix:
            record["fix"] = self.fix

        return record


@dataclass
class ValidationReport:
    subject: str
    findings: list[Finding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    # What is worth telling the operator and is NOT worth another model call: a style slip in a field no reader ever
    # sees. A note never makes `ok` false, so the record stands.
    notes: list[Finding] = field(default_factory=list)

    def add(self,
            code: str,
            detail: str,
            scope: str = 'target',
            fix: str = '') -> None:
        self.findings.append(Finding(code, detail, scope, fix))

    def merge(self,
              other: "ValidationReport") -> None:
        """The findings and the checks of `other` added to this report's; its notes are not."""

        self.findings += other.findings
        self.checks_run += other.checks_run

    def note(self,
             code: str,
             detail: str,
             scope: str = 'target',
             fix: str = '') -> None:
        self.notes.append(Finding(code, detail, scope, fix))

    def mark_ran(self,
                 code: str) -> None:
        self.checks_run.append(code)

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def has_shared_damage(self) -> bool:
        return any(f.scope == "shared" for f in self.findings)

    def render_hint(self,
                    limit: int = 3) -> str:
        """One line per failed check, for the retry prompt. Truncated - a wall of text is ignored."""

        return "; ".join(f"{f.code} - {f.detail}" for f in self.findings[:limit])

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "ok": self.ok,
            "checksRun": self.checks_run,
            "findings": [f.as_dict() for f in self.findings],
            "notes": [f.as_dict() for f in self.notes],
        }

    def render_summary(self) -> str:
        if self.ok:
            return f"{len(self.checks_run)} checks passed"
        if not self.checks_run:
            # Nothing ran and something was found: the machinery failed before the first check, so
            # the detail - which names the exception - is the whole story.
            return "no check completed: " + "; ".join(f"{f.code} - {f.detail}" for f in self.findings)

        return f"{len(self.findings)} of {len(self.checks_run)} checks failed: " + ", ".join(
            f.code for f in self.findings
        )
