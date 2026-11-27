"""The whole run's graph gets its own step budget, never the one attempt's.

The outer graph takes one step per frame plus one per phase, so its steps grow with the recording. The
per-attempt `recursion_limit` bounds one model attempt's inner loop and stays small. When the run was
handed the attempt's 80, every recording cut into more than about 75 frames stopped with a
GraphRecursionError before its first model call: measured on an 87-minute meeting cut into 321 frames.
"""

import ast
from pathlib import Path

from support import harness

import config

RUN_COMMAND = Path(__file__).resolve().parents[2] / "src" / "cli" / "run_command.py"


def _find_graph_invoke_limits() -> list[str]:
    """The source text of every `recursion_limit` value handed to a `graph.invoke(...)` in the run command."""

    tree = ast.parse(RUN_COMMAND.read_text(encoding="utf-8"))
    limits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "invoke":
            for argument in node.args:
                if isinstance(argument, ast.Dict):
                    for key, value in zip(argument.keys, argument.values):
                        if isinstance(key, ast.Constant) and key.value == "recursion_limit":
                            limits.append(ast.unparse(value))

    return limits


def test_the_run_step_budget_covers_the_largest_frame_cap():
    budgets = config.Settings().budgets
    harness.check("budgets.run_steps exists and covers 1000 frames plus every phase",
                  getattr(budgets, "run_steps", 0) >= 1000 + 10, str(budgets))
    harness.check("and it binds from the local file's camelCase key",
                  config.Settings(budgets={"runSteps": 5000}).budgets.run_steps == 5000)


def test_the_run_graph_is_given_the_run_step_budget_not_the_attempt_one():
    limits = _find_graph_invoke_limits()
    harness.check("the run command invokes its graph once, with a step limit", len(limits) == 1, str(limits))
    harness.check("that limit is the run's own budget", all("run_steps" in limit for limit in limits), str(limits))
