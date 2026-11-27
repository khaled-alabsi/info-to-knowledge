"""The kinds of report `report_problem` accepts, and the tool's name. One list, taught to the worker in
`agent-context/instructions/_shared/worker-feedback.md` and checked against it by the suite.

A fixed list, because a free-text kind is spelled differently by every worker and nothing can be counted. Each kind
is something the worker can see from inside its own follow-up, not a developer's name for the defect.
"""

from __future__ import annotations

__all__ = ["FEEDBACK_TOOL", "FEEDBACK_KINDS"]

FEEDBACK_TOOL = "report_problem"

FEEDBACK_KINDS: dict[str, str] = {
    "wrong_rejection":     "a check rejected an answer that does what the instructions ask, or its finding misreads "
                           "your answer",
    "unclear_rejection":   "a rejection did not say what would be accepted, so your correction was a guess",
    "unclear_instruction": "an instruction can be read two ways, or you had to read it twice",
    "wrong_instruction":   "an instruction says something that is not true of what you found",
    "missing_instruction": "you had to work something out yourself that should have been written down",
    "contradiction":       "two instructions, or an instruction and a check, disagree",
    "missing_tool":        "the work needs something no tool in your list can do",
    "insufficient_tool":   "a tool exists but cannot do what you need: a limit, a shape, an argument it does not take",
    "deadlock":            "there was no way forward: every route was refused, or two rules cannot both be followed",
    "wasted_effort":       "you spent calls on work that turned out not to be wanted",
    "other":               "anything worth saying that none of the kinds above fits",
}
