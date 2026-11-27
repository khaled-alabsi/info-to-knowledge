# <!-- sync: twin/cases.test_layer_boundaries v1 -->
"""The dependency direction, asserted rather than described.

    cli -> graph -> nodes -> services -> agent_client

Each arrow is one-way. The rule that matters is the last one: a node that runs an agent itself has
skipped the layer deciding WHICH model and WHICH tools that role gets, and the decision then has
nowhere to live but the node. The user-facing symptom is a ten-argument call at every dispatch site
and two modules both called "the worker".
"""

from __future__ import annotations

import ast
from pathlib import Path

from support import harness

SRC = Path(__file__).resolve().parent.parent.parent / "src"

# A signature past this many parameters has stopped being readable at its call sites, and the fix is
# a request object naming the thing being described - not a longer signature. Measured before the
# split: `run_worker` took 10, `build_toolset` took 13.
MAX_PARAMETERS = 5


def _sources(subdir: str) -> list[Path]:
    directory = SRC / subdir

    return [p for p in directory.rglob("*.py")
            if p.name != "__init__.py" and "__pycache__" not in p.parts] if directory.is_dir() else []


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)

    return found


def test_the_agent_client_never_imports_the_playbook():
    """What makes it reusable rather than merely relocated."""

    # Its docstring claimed a test asserted this. There was no such test, and the layer had drifted:
    # a `ports.py` full of Protocols with zero importers, duplicating the validator report the
    # playbook already had. A claimed invariant nobody checks is not an invariant.

    layers = {p.name for p in SRC.iterdir()
              if p.is_dir() and p.name not in ("agent_client", "__pycache__")}
    layers |= {p.stem for p in SRC.glob("*.py")}

    for path in _sources("agent_client"):
        offenders = {m for m in _imported_modules(path) if m.split(".")[0] in layers}
        harness.check(f"agent_client/{path.relative_to(SRC / 'agent_client')} imports no playbook layer",
              not offenders, f"{offenders} - the client must run with none of them present")


def test_only_services_run_an_agent():
    """Nodes dispatch through a service; they never build or run an agent themselves."""

    runners = ("agent_client.agent_runner", "agent_client.tools")
    for path in _sources("graph") + _sources("cli"):
        offenders = {m for m in _imported_modules(path) if m.startswith(runners)}
        harness.check(f"{path.relative_to(SRC)} does not run an agent directly", not offenders,
              f"{offenders} - go through services/worker_dispatch")


def test_no_public_function_takes_more_than_five_parameters():
    """Past five, a call site is a wall of keywords and the reader cannot tell which are required."""

    # The fix is always a request object that NAMES what is being described - `AgentRunRequest` is the
    # agent, `AgentCallbacks` is how the caller watches it, `ToolsetSpec` is one role's capability set
    # - never a longer signature or a `**kwargs` that hides it.

    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            args = node.args
            total = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
            # `self` is not a parameter the caller passes.
            if args.args and args.args[0].arg in ("self", "cls"):
                total -= 1
            harness.check(f"{path.relative_to(SRC)}:{node.name} takes <= {MAX_PARAMETERS} parameters",
                  total <= MAX_PARAMETERS, f"{node.name} takes {total}")


def test_no_public_function_uses_a_bare_keyword_only_marker():
    """A `*` in a signature is a symptom, not a design."""

    # It was reached for to make ten positional arguments survivable at the call site. With a request
    # object there is nothing to disambiguate, so the marker has no work left to do - and it reads as
    # noise in a two-parameter signature.

    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            bare_marker = node.args.kwonlyargs and node.args.vararg is None
            harness.check(f"{path.relative_to(SRC)}:{node.name} has no bare `*` marker", not bare_marker,
                  "group the arguments into a request object instead")


# --- the import order the PROGRAM uses, which no in-suite import can reproduce -------------------
# A circular import is resolved by whoever imports first, and in this suite that is always the module
# under test. Measured 2026-09-13 on a generated playbook: splitting a toolset service out of the
# worker dispatcher left the toolset importing the job carrier back from it. Every test imported the
# dispatcher before anything imported `cli`, so the cycle never closed and 38,000 tests passed; the
# real entry point imports the session service first and died on `cannot import name ... from
# partially initialized module`. A fresh interpreter is the only place the program's own order can
# be observed - a top-level `import cli` in a test file cannot do it, because by the time the file
# runs the cycle has already been resolved by somebody else.


def test_THE_ENTRY_POINT_IMPORTS_IN_A_FRESH_INTERPRETER():
    """`main.py` imports cleanly with nothing else loaded first."""

    import subprocess       # noqa: PLC0415
    import sys              # noqa: PLC0415

    root = SRC.parent
    done = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import cli"],
                          cwd=root, capture_output=True, text=True, timeout=120)
    harness.check("the program's own import order works", done.returncode == 0,
                  (done.stderr or done.stdout).strip()[-600:])
