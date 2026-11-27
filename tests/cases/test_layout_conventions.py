# <!-- sync: seed/cases.test_layout_conventions v3 -->
"""The layout rules as assertions, so they cannot be violated silently.

Cheapest and most diagnostic test in the suite: a layer violation here explains a class of bug that
otherwise shows up as an import cycle or an untestable node much later.
"""

import ast
import builtins
import json
import sys
import tempfile
from pathlib import Path

from support import harness

SRC = Path(__file__).resolve().parent.parent.parent / "src"
BANNED = {"base.py", "utils.py", "helpers.py", "common.py", "misc.py", "stuff.py"}


def _modules(subdir: str) -> list[Path]:
    directory = SRC / subdir

    return [p for p in directory.rglob("*.py") if p.name != "__init__.py"] if directory.is_dir() else []


def _is_graph_package(module: str) -> bool:
    """True for the graph package, false for `validators.graph_fragment`."""

    # A substring test on ".graph" flags any module whose NAME merely starts with "graph". Match the
    # package path.

    return module == "graph" or module.startswith("graph.")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)

    return found


def _dotted_name(node: ast.expr) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)

    return ".".join(reversed(parts))


def _referenced_names(path: Path) -> set[str]:
    """Every name, attribute and dotted chain the module's CODE refers to - never its docstrings or
    comments. `json.loads(x)` contributes `json`, `loads` and `json.loads`, so a rule may name a
    symbol either way. Parsed because `"read_text" in text` is satisfied by the sentence that
    explains the rule, and by the docstring of the check enforcing it."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
            found.add(_dotted_name(node))
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names)
            if node.module:
                found.add(node.module)
                found.add(node.module.split(".")[0])

    return found


def _called_names(path: Path) -> set[str]:
    """The dotted callee of every call in the module - `open`, `path.read_text`, `Context.current`."""

    tree = ast.parse(path.read_text(encoding="utf-8"))

    return {_dotted_name(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}


IO_MODULES = {"requests", "urllib", "subprocess", "socket", "http"}
IO_METHODS = {"read_text", "write_text", "read_bytes", "write_bytes", "mkdir", "unlink", "iterdir",
              "glob", "rglob", "open"}


def _io_offenders(path: Path) -> set[str]:
    """What a module that must do no I/O actually does: an I/O import, or a call to `open` or a
    filesystem method. A `Path` in a type annotation is not I/O and is not reported."""

    imported = {name.split(".")[0] for name in _imports(path)}
    called = _called_names(path)

    return ((imported & IO_MODULES)
            | {name for name in called if name == "open" or name.rsplit(".", 1)[-1] in IO_METHODS})


def test_no_meaningless_module_names():
    """A name that describes nothing accumulates everything."""

    for path in SRC.rglob("*.py"):
        harness.check(f"{path.name} is not a junk-drawer name", path.name not in BANNED, str(path))


def test_chains_never_import_the_graph():
    """A chain must run with no graph constructed, or every test of it becomes end-to-end."""

    for path in _modules("chains"):
        offenders = {m for m in _imports(path) if _is_graph_package(m)}
        harness.check(f"chains/{path.name} does not import graph", not offenders, str(offenders))


def test_schemas_and_state_do_no_io():
    """A contract module is data. Checked by AST: an I/O import or a filesystem call, never a
    substring - `Path` in an annotation is not I/O."""

    for path in _modules("schemas") + [SRC / "graph" / "graph_state.py"]:
        offenders = _io_offenders(path)
        harness.check(f"{path.name} does no I/O", not offenders, str(offenders))


def test_only_the_chat_model_service_constructs_a_model():
    """services/chat_model_service.py is the single construction point. Anything else is a missed call site later."""

    # Checked by AST, not by grep: a docstring that MENTIONS `init_chat_model` is not a construction,
    # and a text search flags it. Imports and call names are the real signal.
    #
    # SUBCLASSING is not constructing. A provider class named only as a base - the adapter that puts
    # back the fields a client drops - creates no model and adds no call site; it is the type
    # chat_model_service then constructs. Flagging it would push the adapter into the service module
    # and leave two identities in one file.

    providers = {"ChatOllama", "ChatOpenAI", "ChatAnthropic", "init_chat_model"}
    allowed = {SRC / "services" / "chat_model_service.py"}
    for path in SRC.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported, called, subclassed = set(), set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node, ast.ClassDef):
                subclassed.update(b.id for b in node.bases if isinstance(b, ast.Name))
        offenders = (called | (imported - subclassed)) & providers
        harness.check(f"{path.relative_to(SRC)} constructs no model directly", not offenders,
              f"{offenders} - move it into services/chat_model_service.py")


def _node_modules() -> list[Path]:
    """Every module that IS a node: one whose parent directory is called `nodes`."""

    # Depth is not the test. A sub-loop lives in its own folder under `graph/nodes/`, and that folder
    # holds its assembly and its context as well as its own `nodes/` - so "anything under graph/nodes"
    # would demand a node factory from a subgraph's `*_graph.py`. The parent directory is what says
    # "the files in here are workflow steps".

    return [path for path in (SRC / "graph").rglob("*.py")
            if path.parent.name == "nodes" and path.name != "__init__.py"]


def _node_factories(path: Path) -> list[str]:
    """Module-level `build_*_node` definitions - the closure shape this layout no longer allows."""

    # Parsed, never grepped: a module legitimately defines a local `build_messages` closure, and
    # `"def build_" in text` also fires on the docstring of the check itself.

    tree = ast.parse(path.read_text(encoding="utf-8"))

    return [node.name for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("build_") and node.name.endswith("_node")]


def _node_definitions(path: Path) -> list[str]:
    """The node(s) a module declares."""

    # A node is a flat module-level `*_node(state, config)` that reads its context off
    # `config["configurable"]`, which the subgraph binds once with `.with_config`. That keeps context
    # out of module scope - which is the actual rule - without nesting the node inside a factory.

    tree = ast.parse(path.read_text(encoding="utf-8"))

    return [node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
            and node.name.endswith("_node")
            and [a.arg for a in node.args.args][:2] == ["state", "config"]]


def test_no_node_is_nested_in_a_factory():
    """A node is a flat module-level `*_node(state, config)` reading its context off
    `config["configurable"]` - never nested in a `build_*_node` factory, never reaching for an
    ambient `Context.current()` accessor."""

    for path in _node_modules():
        relative = path.relative_to(SRC)
        harness.check(f"{relative} declares a flat node", bool(_node_definitions(path)),
              "expected a module-level *_node(state, config)")
        harness.check(f"{relative} nests no node in a factory", not _node_factories(path),
              "bind context with .with_config, not a closure")
        ambient = {name for name in _called_names(path) if name.rsplit(".", 1)[-1] == "current"}
        harness.check(f"{relative} has no ambient-state accessor", not ambient,
              f"{ambient} - read the context off config['configurable']")


def _value_names(outer: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every name the function uses as a VALUE - passed, stored, returned - never merely called.

    `strip(entry)` uses `strip` as a call and contributes nothing; `set_status=refuse_status` and
    `return handler` both contribute the name. That difference is the whole rule below: a closure
    handed back is a closure with a caller outside; one only called here is a step of this function.
    """

    callees = {node.func for node in ast.walk(outer) if isinstance(node, ast.Call)}

    return {node.id for node in ast.walk(outer)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node not in callees}


def _nodes_directly_inside(outer: ast.AST, kinds: tuple) -> list[ast.AST]:
    """The nodes of `kinds` that `outer` itself holds - descending, but never into a function.

    Stopping at the next function boundary is what attributes a node two levels down to the function
    that actually holds it, so it is reported once rather than once per ancestor.
    """

    found: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(outer))
    while stack:
        node = stack.pop()
        if isinstance(node, kinds):
            found.append(node)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))

    return found


def _nested_definitions(path: Path) -> list[tuple[str, str, int]]:
    """Every function defined inside another function that the enclosing one only ever CALLS.

    Two shapes are legitimate and are not reported: the closure a factory hands back as a value -
    the standard way to give a fixed-signature callback its context - and a callback a decorator
    registers, which is what every `@tool` inside a `build_*_tools` factory is. Everything else is
    invisible to a test and to a grep, and makes the enclosing function the smallest unit anyone
    can exercise.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int]] = []
    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        kept = _value_names(outer)
        for inner in _nodes_directly_inside(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if inner.decorator_list or inner.name in kept:
                continue
            found.append((outer.name, inner.name, inner.lineno))

    return found


def test_no_function_is_defined_inside_another_and_only_called():
    """A nested definition the enclosing function only calls is a testable unit nobody can reach."""

    for path in sorted(SRC.rglob("*.py")):
        for outer, inner, line in _nested_definitions(path):
            harness.check(f"{path.relative_to(SRC)}:{line} {inner} is not nested inside {outer}", False,
                  "extract it to module level - a closure that is not handed back hides a unit")



def _function_level_imports(path: Path) -> list[tuple[str, str, int]]:
    """Every import written inside a function body, with the function that holds it.

    An import here is invisible to every reader and every linter, and it defers an ImportError to
    the first call rather than to load. Where it is there to break an import cycle, the cycle is the
    defect: the two modules are in the wrong layers, and burying the import hides that.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int]] = []
    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in _nodes_directly_inside(outer, (ast.Import, ast.ImportFrom)):
            name = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name

            found.append((outer.name, name or ".", node.lineno))

    return found


def test_no_import_sits_inside_a_function_body():
    """Every dependency is visible at the top of the file, and resolves at load rather than at the
    first call."""

    for path in sorted(SRC.rglob("*.py")):
        for outer, name, line in _function_level_imports(path):
            harness.check(f"{path.relative_to(SRC)}:{line} imports {name} at module level", False,
                  f"move it above {outer} - a cycle it hides is a layering defect to fix")



ENTRY_POINT = SRC.parent / "main.py"


def _imported_module_paths(path: Path,
                           source_root: Path) -> set[Path]:
    """The modules under `src/` that `path` imports, resolved to files.

    A dotted name is tried as a module, as a package, and - for `from a.b import c` - both as
    `a/b/c.py` and as the `a/b.py` that holds a symbol called `c`, because an import statement does
    not say which of the two it is and both are ordinary here.
    """

    candidates: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            candidates.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            candidates.add(node.module)
            candidates.update(f"{node.module}.{alias.name}" for alias in node.names)

    found: set[Path] = set()
    for dotted in candidates:
        parts = dotted.split(".")
        for candidate in (source_root.joinpath(*parts).with_suffix(".py"),
                          source_root.joinpath(*parts, "__init__.py")):
            if candidate.is_file():
                found.add(candidate)

    return found


def _modules_reachable_from_entry_point(source_root: Path,
                                        entry_point: Path) -> set[Path]:
    """Every module under `src/` an import chain from the entry point actually reaches."""

    # PRODUCTION reachability, which is a different question from the one test_module_coverage asks.
    # That one asks whether a TEST imports a module; a module every test imports and no run reaches
    # is dead weight that reads as a layer in use, and nothing else in the suite can see it.

    seen: set[Path] = set()
    stack = [entry_point]
    while stack:
        current = stack.pop()
        for target in _imported_module_paths(current, source_root):
            if target not in seen:
                seen.add(target)
                stack.append(target)

    return seen


def test_every_module_is_reachable_from_the_entry_point():
    """A module no import chain reaches is dead weight that still reads as a layer in use."""

    reachable = _modules_reachable_from_entry_point(SRC, ENTRY_POINT)
    for path in sorted(SRC.rglob("*.py")):
        harness.check(f"{path.relative_to(SRC)} is reachable from {ENTRY_POINT.name}", path in reachable,
              "nothing imports it - wire it up, or delete it")


def _callable_field_arities(tree: ast.Module) -> dict[str, int]:
    """Every field annotated `Callable[[...], R]`, with how many arguments it declares.

    A field annotated `Callable[..., R]` promises nothing about its arity and is not listed.
    """

    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        annotation = node.annotation
        while isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            annotation = annotation.left
        if not isinstance(annotation, ast.Subscript) or _dotted_name(annotation.value) != "Callable":
            continue
        arguments = annotation.slice.elts[0] if isinstance(annotation.slice, ast.Tuple) else None
        if isinstance(arguments, ast.List):
            found[node.target.id] = len(arguments.elts)

    return found


def test_every_callable_field_is_called_with_the_arity_it_declares():
    """A hook annotated for one argument and called with two is a lie a reader trusts.

    Nothing runs a type checker here, so the annotation and the call site drift in silence until the
    hook fires - which is the run nobody was watching.
    """

    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declared = _callable_field_arities(tree)
        if not declared:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            expected = declared.get(node.func.attr)
            if expected is None or any(isinstance(a, ast.Starred) for a in node.args):
                continue
            passed = len(node.args) + len(node.keywords)
            harness.check(f"{path.relative_to(SRC)}:{node.lineno} {node.func.attr}(...) passes "
                  f"{expected} argument(s), as annotated", passed == expected,
                  f"it passes {passed} - the annotation and the call site disagree")


ASSEMBLY_FORBIDDEN = ("ChatPromptTemplate", "with_structured_output", "json.loads", "read_text")


def test_graph_assembly_holds_no_logic():
    """graph_assembly.py is assembly only. Anything else here is a hidden node."""

    referenced = _referenced_names(SRC / "graph" / "graph_assembly.py")
    for bad in ASSEMBLY_FORBIDDEN:
        harness.check(f"graph_assembly.py contains no {bad}", bad not in referenced)


def test_validators_are_pure():
    """No model, no network, no graph import: that is what makes them testable offline."""

    for path in _modules("validators"):
        offenders = {m for m in _imports(path)
                     if _is_graph_package(m) or m.split(".")[0] in ("langchain", "langgraph")}
        harness.check(f"validators/{path.name} is pure", not offenders, str(offenders))


# Layers every playbook has. `src/` is FLAT: these are its subdirectories, and there is no package
# directory between them and the source root.
REQUIRED_LAYERS = {
    "config.py": "settings and the output-contract vocabulary",
    "cli": "one module per command group",
    "graph/graph_assembly.py": "assembly: what happens, in what order",
    "graph/graph_state.py": "what survives between steps",
    "graph/routing.py": "where execution goes next",
    "graph/nodes": "one workflow step each",
    "agent_client": "boot, configure and run an agent with tools",
    "agent_client/tools": "what external capability can be executed",
    "prompts": "what instructions the model receives",
    "services": "side effects: files, state, subprocesses",
    "validators": "what on disk would prove it was actually done",
    "services/chat_model_service.py": "the single model construction point",
}

# Layers a playbook has only if it needs them. A playbook that never asks a model for structured
# data has no chain and no schema, and requiring the folders anyway is how a package ends up with
# a `chains/` nobody calls - which is worse than no folder, because it reads as a layer in use.
CONDITIONAL_LAYERS = {
    "chains": "what we ask the model to do - only if a model is asked for structured data",
    "schemas": "what structured contract we expect back - required exactly when chains/ exists",
}


def test_layout_matches_the_langgraph_best_practice_structure():
    """The layers the guide mandates, each in its documented place."""

    # Encoded as a test because a layout is a claim, and a claim that nothing checks drifts. `graph`
    # owns orchestration, `nodes` own workflow steps, `services` own side effects, `agent_client`
    # owns running the agent, and `state` is the contract connecting them.

    for relative, why in REQUIRED_LAYERS.items():
        harness.check(f"{relative} exists ({why})", (SRC / relative).exists())


def test_chains_and_schemas_are_present_together_or_not_at_all():
    """One without the other is a half-wired layer: a chain with no schema, or a schema nothing asks
    for. Either both, or neither."""

    has_chains = (SRC / "chains").is_dir() and bool(_modules("chains"))
    has_schemas = (SRC / "schemas").is_dir() and bool(_modules("schemas"))
    harness.check("chains/ and schemas/ agree on whether this playbook uses structured output",
          has_chains == has_schemas, f"chains={has_chains} schemas={has_schemas}")


def test_no_source_layer_shadows_an_importable_module():
    """`src/` is on `sys.path`, so every directory in it claims a TOP-LEVEL import name."""

    # `config`, `models`, `tools`, `services` and `graph` are ordinary words, and a dependency that
    # ships a top-level module by the same name would be shadowed process-wide by ours - producing an
    # ImportError inside a library, far from anything that looks related. Cheap to assert, and the
    # only warning anyone would get.

    for path in sorted(SRC.iterdir()):
        if path.name.startswith((".", "_")) or path.name == "__pycache__":
            continue
        name = path.stem if path.suffix == ".py" else path.name
        harness.check(f"src/{path.name} does not shadow a stdlib module",
              name not in sys.stdlib_module_names,
              "rename the layer - a stdlib name here breaks imports process-wide")


def test_no_node_reaches_past_its_service_into_the_agent_client():
    """`cli -> graph -> nodes -> services -> agent_client`, and nodes do not skip the middle."""

    # A node that runs an agent directly has skipped the layer deciding WHICH model and WHICH tools
    # this role gets - and that decision then has nowhere to live but the node. Importing a budget
    # dataclass or an exception type from the client is fine: that is the contract between the two
    # layers, not a call into it.

    runs_an_agent = ("agent_client.agent_runner", "agent_client.tools")
    for path in _modules("graph"):
        offenders = {m for m in _imports(path) if m.startswith(runs_an_agent)}
        harness.check(f"graph/{path.relative_to(SRC / 'graph')} does not run an agent directly",
              not offenders, f"{offenders} - go through services/")


def test_one_node_per_module():
    """A module holding two nodes is two workflow steps sharing a file."""

    # The guide's own example is one file per node - `classify.py`, `retrieve.py`, `generate.py`. A
    # module with three factories in it is where the layer boundary starts to blur.

    for path in _node_modules():
        nodes = _node_definitions(path)
        harness.check(f"{path.relative_to(SRC)} defines exactly one node",
              len(nodes) == 1, f"found {nodes}")


NODE_BODY_MARKERS = ("write_invocation", "claim_attempt_dir", "Snapshot.take", "record_attempt")


def test_a_subgraph_assembly_holds_no_node_bodies():
    """A sub-loop's own `*_graph.py` wires its nodes; it does not implement them."""

    for path in (SRC / "graph" / "nodes").rglob("*_graph.py"):
        referenced = _referenced_names(path)
        relative = path.relative_to(SRC)
        for bad in NODE_BODY_MARKERS:
            harness.check(f"{relative} does not implement {bad}", bad not in referenced,
                  f"node bodies belong in {path.parent.relative_to(SRC)}/nodes/")


# ---------------------------------------------------------------------------------------
# The NAMING scheme. A name is a claim about what a thing is, and an unchecked claim drifts.
# Everything below is AST: a rule checked with `in text` fires on the docstring explaining it.
# ---------------------------------------------------------------------------------------

# --- sync: naming-vocabulary v1 BEGIN - one JSON body, seven carriers; see sync-registry.md ---
NAMING_VOCABULARY = json.loads("""
{
  "verbs": [
    "accept", "add", "answer", "append", "apply", "assemble", "assert", "attach", "bind", "build", "bump",
    "cache", "call", "cancel", "capture", "check", "choose", "claim", "clean", "clear", "clip",
    "close", "collapse", "collect", "compare", "compile", "compute", "configure", "confirm",
    "connect", "convert", "copy", "count", "cover", "create", "decide", "declare", "decode",
    "dedupe", "defer", "delete", "derive", "describe", "detach", "detect", "discard", "discover",
    "dispatch", "display", "drain", "draw", "drop", "dump", "echo", "emit", "encode", "end",
    "enrich", "ensure", "enumerate", "escape", "estimate", "expand", "explain", "export", "extract", "feed",
    "fetch", "fill", "filter", "finalise", "finalize", "find", "finish", "fix", "flag", "flatten",
    "format", "freeze", "gather", "generate", "get", "grant", "group", "handle", "hide", "honour",
    "honor", "ignore", "import", "index", "infer", "inject", "insert", "inspect", "install",
    "invoke", "isolate", "iter", "iterate", "join", "judge", "keep", "kill", "launch", "list",
    "load", "locate", "log", "make", "map", "mark", "match", "merge", "mount", "move", "name",
    "narrow", "new", "normalise", "normalize", "note", "offer", "open", "order", "parse", "pause", "pick",
    "poll", "pop", "prepare", "print", "probe", "process", "produce", "prove", "prune", "publish",
    "pull", "push", "queue", "raise", "read", "rebuild", "recover", "record", "reduce", "refuse",
    "register", "reject", "release", "reload", "remove", "rename", "render", "repair", "replace",
    "report", "request", "require", "reset", "reshape", "resolve", "restore", "resume", "retry", "return",
    "reveal", "rewrite", "roll", "rollback", "route", "run", "save", "scan", "score", "seal",
    "search", "seed", "select", "send", "serialise", "serialize", "set", "setup", "shorten",
    "show", "shrink", "sign", "skip", "snapshot", "sort", "spawn", "split", "stage", "start",
    "stop", "store", "stream", "strip", "submit", "substitute", "suggest", "summarise",
    "summarize", "supply", "suppress", "sweep", "switch", "sync", "synthesise", "synthesize",
    "take", "teach", "tell", "terminate", "throw", "time", "trace", "track", "train", "transform",
    "translate", "trigger", "trim", "truncate", "try", "unescape", "union", "unpack", "unwrap",
    "update", "upgrade", "use", "validate", "verify", "wait", "walk", "warn", "watch", "wire",
    "withhold", "work", "wrap", "write", "yield"
  ],
  "question_prefixes": [
    "is_", "are_", "was_", "were_", "has_", "have_", "had_", "can_", "could_", "should_", "would_",
    "will_", "does_", "did_", "do_", "must_", "may_", "needs_", "wants_", "allows_", "accepts_",
    "knows_", "looks_", "matches_", "contains_", "holds_", "stays_", "survives_", "exists_",
    "any_", "all_"
  ],
  "name_carrying_prefixes": [
    "cmd_", "on_", "to_", "as_", "from_", "test_", "for_", "with_", "without_", "by_", "at_",
    "in_", "of_", "when_", "where_", "why_", "how_", "what_"
  ],
  "exempt_function_names": [
    "main"
  ],
  "api_decorators": [
    "tool", "property", "cached_property", "setter", "getter", "deleter", "before_model",
    "abefore_model", "after_model", "wrap_model_call", "before_agent", "after_agent", "hook",
    "route", "command",
    "app", "task", "fixture", "validator", "field_validator", "model_validator", "overload",
    "singledispatch", "staticmethod", "classmethod"
  ],
  "role_suffixes": [
    "_service", "_client", "_runner", "_tools", "_validator", "_checks", "_middleware", "_utils",
    "_registry", "_builder", "_factory", "_node", "_graph", "_context", "_command", "_commands",
    "_result", "_report", "_state", "_settings", "_identity", "_sandbox", "_policy", "_prompt",
    "_message", "_messages", "_log", "_store", "_errors", "_abstract", "_budget", "_assembly",
    "_loader", "_validation"
  ],
  "carrier_suffixes": [
    "State", "Context", "Store", "Handles", "Result", "Outcome", "Error", "Exception", "Report",
    "Finding", "Item", "Paths", "Identity", "Budget", "Settings", "Spec", "Job", "Request",
    "Registry", "Snapshot", "Log", "List", "Deadline", "Middleware", "Model", "Validation",
    "Compaction", "Callbacks"
  ],
  "banned_kind_words": [
    "Ledger", "Journal", "Bookkeeping", "Manager", "Tracker", "Bag", "Blob", "Data", "Info",
    "Helper", "Util"
  ]
}
""")
# --- sync: naming-vocabulary v1 END ---

# A function name opens with one of these, or asks a question.
VERBS = set(NAMING_VOCABULARY["verbs"])

# A predicate asks a question. These open one.
QUESTION_PREFIXES = tuple(NAMING_VOCABULARY["question_prefixes"])

# A name whose first token is a preposition or a protocol marker is placed by that marker. `cmd_`
# is the CLI dispatch protocol: the suffix IS the command a user types, so renaming it renames the
# interface rather than the code.
NAME_CARRYING_PREFIXES = tuple(NAMING_VOCABULARY["name_carrying_prefixes"])

# The process entry point is named by universal convention, not by this scheme.
EXEMPT_FUNCTION_NAMES = set(NAMING_VOCABULARY["exempt_function_names"])

# A decorator that MAKES the name an external API: `@tool` names what the model calls, `@property`
# names an attribute. Renaming either changes a contract rather than the code.
API_DECORATORS = set(NAMING_VOCABULARY["api_decorators"])

# A module name ends in its role. The layer folder is not an excuse: a traceback prints the
# filename and an import prints the module, and neither shows the folder.
ROLE_SUFFIXES = tuple(NAMING_VOCABULARY["role_suffixes"])

# Placed by position rather than by name.
# Every carrier class names its KIND in its suffix. The suffix is the only part of a type name a
# reader always sees, and it has to answer two questions: when does this die, and may it be
# serialised? A metaphor answers neither. From `Error` on, the entries are not carriers of run data
# but legal class endings in these packages.
CARRIER_SUFFIXES = tuple(NAMING_VOCABULARY["carrier_suffixes"])

# A kind named after a filing cabinet instead of a lifetime. `Registry` is deliberately absent: it
# stays legal for a live key -> object lookup, and is only wrong as a word for a durable record.
BANNED_KIND_WORDS = tuple(NAMING_VOCABULARY["banned_kind_words"])

NAME_EXEMPT_MODULES = {"main.py", "config.py", "routing.py", "__init__.py",
                       "conftest.py", "setup.py", "__main__.py"}


def test_the_naming_vocabulary_block_carries_exactly_its_eight_keys():
    """The bytes between the two `sync: naming-vocabulary` markers are one JSON body, copied
    unchanged into every carrier. A key present in one copy and missing from another is exactly the
    drift the block exists to make impossible, so the shape is asserted where the vocabulary is
    read - and the body has to parse for this module to import at all."""

    keys = ["verbs", "question_prefixes", "name_carrying_prefixes", "exempt_function_names",
            "api_decorators", "role_suffixes", "carrier_suffixes", "banned_kind_words"]
    harness.check("the naming-vocabulary block carries exactly its eight keys",
                  list(NAMING_VOCABULARY) == keys, str(list(NAMING_VOCABULARY)))
    for key in keys:
        values = NAMING_VOCABULARY.get(key, [])
        harness.check(f"the naming-vocabulary block's {key} is a non-empty list of strings",
                      isinstance(values, list) and bool(values)
                      and all(isinstance(value, str) for value in values), repr(values)[:80])


def _source_modules() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name:
            names.add(name)

    return names


def _is_carrier(tree: ast.Module) -> bool:
    """A module of data carriers is named for the carrier - a suffix would repeat the class name.
    A vocabulary of module-level constants is data by the same test: no functions, no behaviour."""

    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    constants = [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                 and _assigned_name(n).isupper()]

    return bool(classes or constants) and not functions


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str:
    target = node.targets[0] if isinstance(node, ast.Assign) else node.target

    return target.id if isinstance(target, ast.Name) else ""


def test_every_module_name_says_what_it_holds():
    """A traceback prints `run_store.py:87` and an import prints `run_store.load(...)`."""

    # Neither shows the folder, so the folder cannot be the thing that carries the role.

    for path in _source_modules():
        if path.name in NAME_EXEMPT_MODULES or path.stem.startswith("test"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _is_carrier(tree):
            continue
        harness.check(f"{path.relative_to(SRC)} ends in its role",
                      path.stem.endswith(ROLE_SUFFIXES),
                      f"one of {', '.join(ROLE_SUFFIXES[:6])}, ... - or split it, if no one name fits")


def test_every_function_name_is_a_verb_phrase_or_a_question():
    """`_existing_work(ctx)` returns a paragraph of text and reads like a flag at the call site."""

    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (node.name.startswith("__") or node.name in EXEMPT_FUNCTION_NAMES
                    or node.name in API_DECORATORS or _decorator_names(node) & API_DECORATORS):
                continue
            bare = node.name.lstrip("_")
            first = bare.split("_")[0]
            harness.check(f"{path.relative_to(SRC)}:{node.lineno} {node.name} says what it does",
                          bare.startswith(QUESTION_PREFIXES)
                          or bare.startswith(NAME_CARRYING_PREFIXES) or first in VERBS,
                          "start it with a verb, or ask a question (is_/has_/can_/should_)")


def test_no_function_is_imported_by_name_from_a_local_module():
    """The layer travels with the call, or it lives in the import block and is lost at the next move."""

    # Classes, dataclasses and exceptions are exempt: a type name carries its own identity, and putting
    # a module in front of an annotation costs more than it explains.

    layers = {p.stem if p.suffix == ".py" else p.name for p in SRC.iterdir()}
    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module.split(".")[0] not in layers:
                continue
            for alias in node.names:
                if not alias.name[:1].islower() or alias.name.startswith("_"):
                    continue
                target = SRC.joinpath(*node.module.split("."))
                if (target / f"{alias.name}.py").is_file() or (target / alias.name).is_dir():
                    continue          # importing a MODULE, which is the form being asked for
                source = target.with_suffix(".py")
                if not source.is_file() and not (target / "__init__.py").is_file():
                    continue          # a partial tree - judge nothing
                harness.check(f"{path.relative_to(SRC)}:{node.lineno} imports a module, not a function",
                              False,
                              f"`from {node.module} import {alias.name}` - import "
                              f"`{node.module.rsplit('.', 1)[-1]}` and call it qualified")


def _bound_names(tree: ast.Module) -> set[str]:
    """Every name a module binds at its top level: imports, assignments, defs and classes."""

    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.ExceptHandler,)) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)

    return bound


def test_every_name_used_is_bound_somewhere_in_its_module():
    """A name read but never bound is a NameError waiting for the one run that reaches that line."""

    # Cheap, and it catches what a rename reliably breaks: the definition moves and one reference does
    # not follow. Measured during the rename that introduced this test - five of them. `compileall` was
    # clean and the whole suite was green on every one, because they all sat in code paths reachable
    # only through a real graph invocation: two were found by `main.py run --dry-run`, and two more by
    # reading, in the branch a dry run returns before.
    #
    # Scoping is deliberately coarse - every binding anywhere in the module counts, so a name bound in
    # one function and read in another passes. That is not a correctness claim about scope; it is the
    # cheapest test that has no false positives, and the defect it hunts is a name bound NOWHERE.

    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # The module globals every file gets for free, bound by the interpreter rather than the source.
        bound = _bound_names(tree) | set(dir(builtins)) | {"__file__", "__name__", "__doc__",
                                                          "__package__", "__spec__", "__loader__"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            harness.check(f"{path.relative_to(SRC)}:{node.lineno} `{node.id}` is bound",
                  node.id in bound,
                  f"`{node.id}` is read here and bound nowhere in the module")


def _top_level_names(tree: ast.Module) -> set[str]:
    """Everything a module binds at its top level - definitions, assignments and imports."""

    names = {node.name for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    names |= {_assigned_name(node) for node in tree.body
              if isinstance(node, (ast.Assign, ast.AnnAssign))}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}

    return names - {""}


def _promised_names(tree: ast.Module) -> list[tuple[str, int]]:
    """Every string a module's `__all__` lists, with the line it is listed on."""

    for node in tree.body:
        if isinstance(node, ast.Assign) and _assigned_name(node) == "__all__":
            return [(element.value, node.lineno) for element in getattr(node.value, "elts", [])
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)]

    return []


def test_every_name_a_module_promises_is_defined_in_it():
    """`__all__` is the module's stated surface, and `from x import *` copies exactly it.

    A name listed there that the module does not define raises `AttributeError` on the star-import -
    at import time, before a line of the module runs - and reads as a promise to everyone else.
    Measured: a service listed a function under the name it carried before a rename, and nothing said
    so, because nothing in this tree star-imports it.
    """

    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = _top_level_names(tree)
        for name, line in _promised_names(tree):
            harness.check(f"{path.relative_to(SRC)}:{line} __all__ promises `{name}`",
                          name in defined,
                          f"`{name}` is listed in __all__ and defined nowhere in the module")


def _annotation_names(tree: ast.Module) -> set[str]:
    """Names that appear only inside a STRING annotation, which no `ast.Name` walk can see."""

    names = set()
    for node in ast.walk(tree):
        for annotation in (getattr(node, "annotation", None), getattr(node, "returns", None)):
            if not isinstance(annotation, ast.Constant) or not isinstance(annotation.value, str):
                continue
            try:
                inner = ast.parse(annotation.value, mode="eval")
            except SyntaxError:
                continue          # not a type expression; nothing to read from it
            names |= {n.id for n in ast.walk(inner) if isinstance(n, ast.Name)}

    return names


def test_no_module_imports_a_name_it_never_uses():
    """An import nothing reads is a dependency this module does not have, stated as if it did.

    It is what anything reading the import block believes - a layer check, a dependency manifest, a
    person deciding what a module touches - and it outlives every rename because nothing fails when
    it is wrong. Measured on this seed: three modules imported an exception, the configuration module
    and a dataclass helper, and mentioned none of them again.
    """

    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        read = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        # A re-export is a use: a package `__init__` exists to bind names for its importers.
        read |= _annotation_names(tree) | {name for name, _ in _promised_names(tree)}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue          # a compiler directive, bound to no name anything can read
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue      # a star-import binds names this file never spells
                bound = alias.asname or alias.name.split(".")[0]
                harness.check(f"{path.relative_to(SRC)}:{node.lineno} `{bound}` is imported and used",
                              bound in read,
                              f"`{bound}` is imported here and read nowhere in the module")


def _module_for(alias: str, tree: ast.Module) -> str | None:
    """The dotted module an alias refers to, if it was imported from inside `src/`."""

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                if (imported.asname or imported.name) == alias:
                    return f"{node.module}.{imported.name}"
        elif isinstance(node, ast.Import):
            for imported in node.names:
                if (imported.asname or imported.name.split(".")[0]) == alias:
                    return imported.name

    return None


def _public_names(module: str) -> set[str] | None:
    """Everything a local module defines at its top level, or None if it is not local."""

    base = SRC.joinpath(*module.split("."))
    path = base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    # `A = 1` and `A, B = 1, 2` both define names
    names |= {t.id for n in tree.body if isinstance(n, ast.Assign)
              for target in n.targets for t in (target.elts if isinstance(target, ast.Tuple) else [target])
              if isinstance(t, ast.Name)}
    names |= {n.target.id for n in tree.body
              if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {a.asname or a.name.split(".")[0] for a in node.names}

    return names


def test_every_attribute_taken_from_a_local_module_exists():
    """`chat_model_service.describe_phase_models()` is checkable: the module is right there on disk."""

    # This is the defect class that has escaped three times in one session, every time from a rename
    # or a codemod: the module import is correct, the attribute after the dot is not. `compileall` is
    # clean because it is valid syntax, and the whole suite is green because the line only runs on a
    # real invocation. Two were caught by `main.py run --dry-run` and one by reading; none by a test.
    #
    # Only LOCAL modules are checked - a third-party module's surface is not on disk to compare
    # against, and guessing at it would produce false failures instead of findings.

    for path in _source_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            module = _module_for(node.value.id, tree)
            if module is None:
                continue
            names = _public_names(module)
            if names is None:
                continue
            harness.check(f"{path.relative_to(SRC)}:{node.lineno} {node.value.id}.{node.attr} exists",
                  node.attr in names,
                  f"`{module}` defines no `{node.attr}`")


# A boolean is a question, and a data name says what it holds. Both rules are mechanical, so they are
# asserted rather than reviewed: the names that broke them (`dry_run`, `repairing`, `only`) each read
# as something other than a flag or a list at the call site, two layers from their declaration.
BOOLEAN_PREFIXES = tuple(p for p in QUESTION_PREFIXES
                         if p not in ("looks_", "matches_", "contains_", "holds_", "stays_",
                                      "survives_", "exists_", "any_", "all_", "do_", "did_"))

# A name that is an EXTERNAL CONTRACT is exempt, because renaming it changes an interface: a config
# key bound to its camelCase form on disk, an item field the contracts tell the worker to write, and
# a tool parameter the model is shown in a schema.
EXEMPT_BOOLEANS = {
    ("config.py", None): "settings fields are config keys read off disk",
    ("graph_state.py", "ignored"): "a store and manifest item field the contracts describe",
}

# Bare qualifiers: each one asks the reader "which?" and answers nothing.
EMPTY_NAMES = {"only", "all", "any", "data", "info", "value", "mode", "extra", "stuff", "thing"}


def _is_bool_annotation(node) -> bool:
    text = ast.unparse(node) if node is not None else ""

    return text in ("bool", "bool | None", "Optional[bool]")


def _exempt(path: Path, name: str) -> bool:
    return any(file == path.name and (field is None or field == name)
               for file, field in EXEMPT_BOOLEANS)


def _declared_names(path: Path):
    """Every field and parameter in one module, as (name, is_boolean) pairs."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id, _is_bool_annotation(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
                yield argument.arg, _is_bool_annotation(argument.annotation)


def test_every_boolean_asks_a_question():
    """`dry_run` could be a mode string, a path or a count. `is_dry_run` could only be a flag."""

    for path in SRC.rglob("*.py"):
        if "tools" in path.parts:                      # tool parameters are a schema the model reads
            continue
        for name, is_boolean in _declared_names(path):
            if not is_boolean or _exempt(path, name):
                continue
            harness.check(f"{path.name}: `{name}` is named as a question",
                  name.startswith(BOOLEAN_PREFIXES),
                  f"a boolean is named is_/has_/can_/should_/must_/was_, not `{name}`")


def test_no_name_is_a_bare_qualifier():
    """A field called `only` names nothing two layers in, where the `--only` flag is not visible."""

    for path in SRC.rglob("*.py"):
        for name, _is_boolean in _declared_names(path):
            harness.check(f"{path.name}: `{name}` says what it holds", name not in EMPTY_NAMES,
                  f"`{name}` asks the reader 'which?' and answers nothing")



def _carrier_classes() -> list[tuple[Path, ast.ClassDef]]:
    """Every class declared in `src/`, with the file it came from."""

    out = []
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef):
                out.append((path, node))

    return out


def test_no_carrier_is_named_after_a_metaphor():
    """`Ledger` names a filing cabinet. The suffix has to name a LIFETIME."""

    # A reader meeting a type at a call site needs two facts from its name: when does this die, and may
    # it be serialised? `RunStore` answers both - it outlives the process, and it IS the file. `Ledger`
    # answers neither, which is how a durable record and a checkpointed value end up looking alike.

    for path, node in _carrier_classes():
        for word in BANNED_KIND_WORDS:
            harness.check(f"{path.name}: `{node.name}` names a kind, not a metaphor",
                  word not in node.name,
                  f"`{word}` describes a filing cabinet - use State/Context/Store/Handles/Result")


def test_every_carrier_class_names_its_kind():
    """The suffix is the only part of a type name a reader always sees."""

    for path, node in _carrier_classes():
        if node.name.startswith("_"):                  # private helpers are not a public vocabulary
            continue
        if any(isinstance(b, ast.Name) and b.id in {"Exception", "RuntimeError", "ValueError",
                                                    "BaseCallbackHandler", "BaseModel", "BaseSettings",
                                                    "TypedDict", "_OverlaySettings", "App"} for b in node.bases):
            continue
        harness.check(f"{path.name}: `{node.name}` ends in a kind word",
              node.name.endswith(CARRIER_SUFFIXES),
              f"one of {', '.join(CARRIER_SUFFIXES[:6])}, ... - or it is two carriers, not one")


def test_a_nested_context_never_redeclares_its_parents_field():
    """Seven of twelve fields copied from the parent is not decoupling - it is two carriers."""

    # `JobRetryContext` used to declare `repository`, `output_root`, `run_dir`, `store`, `budget`,
    # `control_check` and `model`, all of which `RunContext` already held, retyped at all four
    # construction sites. It answered "what handles does the run have?" and "what is this step's job?"
    # at once, so every new phase copied seven assignments correctly or introduced a bug.

    def fields(path: Path, name: str) -> set[str]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return {t.target.id for t in node.body if isinstance(t, ast.AnnAssign)
                        and isinstance(t.target, ast.Name)}

        return set()

    parent = fields(SRC / "models" / "run_context.py", "RunContext")
    child = fields(SRC / "models" / "job_retry_context.py", "JobRetryContext")
    harness.check("both carriers were found", parent and child, f"parent={parent} child={child}")
    harness.check("the nested context copies nothing from its parent", not (parent & child),
          f"re-declared: {sorted(parent & child)} - take RunContext alongside it instead")


def test_every_run_state_field_has_a_reader():
    """A field the graph writes and nothing reads rides in every snapshot for nobody."""

    # `RunState["items"]` carried a full copy of the store's item dict - written on both of the
    # discovery node's return paths, read by nothing, because every consumer went to the store for the
    # live value. It was free to diverge from disk for the life of the playbook.

    state_file = SRC / "graph" / "graph_state.py"
    declared = set()
    for node in ast.walk(ast.parse(state_file.read_text())):
        if isinstance(node, ast.ClassDef) and node.name == "RunState":
            declared = {t.target.id for t in node.body if isinstance(t, ast.AnnAssign)
                        and isinstance(t.target, ast.Name)}
    harness.check("RunState was found and has fields", bool(declared))

    # A read is `state.get("x")`, `state["x"]` or `final.get("x")` - anywhere but graph_state.py.
    read = set()
    for path in SRC.rglob("*.py"):
        if path == state_file or "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            key = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                key = node.args[0].value
            elif (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                  and isinstance(node.value, ast.Name)):
                key = node.slice.value
            if isinstance(key, str):
                read.add(key)

    for field_name in sorted(declared - read):
        harness.check(f"RunState.{field_name} has a reader", False,
              "nothing reads it - delete it, or read it where it was meant to steer the graph")

# ---------------------------------------------------------------------------------------
# The checks above are code, and code can contain the defect it looks for. A rule written as
# `"symbol" in text` was satisfied by its own docstring three times in this package's history, so
# every source-reading helper carries a known-bad and a known-clean fixture here.
# ---------------------------------------------------------------------------------------

DOCSTRING_ONLY_MODULE = '''"""Mentions json.loads, read_text, ChatPromptTemplate, Context.current(), open() and
subprocess - in prose only. A grep fires on this file; a parse does not."""

VALUE = 1  # not write_invocation, not claim_attempt_dir
'''

OFFENDING_MODULE = '''import json
import subprocess
from somewhere import ChatPromptTemplate, Context


def load(path):
    ctx = Context.current()
    return json.loads(path.read_text())
'''


# A nested definition is legal in exactly two shapes, so the fixture carries both of them beside the
# one that is not: the lookalike is what says what the rule is FOR, and it is the half nobody writes.
NESTED_DEFINITION_MODULE = '''from somewhere import tool


def build_tools(root):
    """Two legal nested definitions and one that is not."""

    def _only_called(target):
        return target / root

    def _handed_back(target):
        return target

    @tool
    def registered(target: str) -> str:
        return target

    squared = lambda value: value * value

    return [_handed_back, registered, squared, _only_called(root)]
'''

# An import is a finding for where it SITS, never for being mentioned: the lookalike talks about a
# deferred import in prose and writes its own at the top, which is what every clean module does.
FUNCTION_IMPORT_MODULE = '''"""Explains why `import tempfile` inside a function is banned. Prose only."""

import tempfile


def clean(path):
    return tempfile.mkdtemp()


def deferred(path):
    import json

    def inner(text):
        from pathlib import Path

        return Path(text)

    return json.loads(inner(path).read_text())
'''

def _write_reachability_fixture() -> tuple[Path, Path]:
    """A source root whose entry point reaches one module through a chain, and misses the other."""

    root = Path(tempfile.mkdtemp())
    source_root = root / "src"
    (source_root / "services").mkdir(parents=True)
    (source_root / "services" / "__init__.py").write_text("", encoding="utf-8")
    (root / "main.py").write_text("from cli import run_command\n", encoding="utf-8")
    (source_root / "cli.py").write_text("from services import wired_service\n", encoding="utf-8")
    (source_root / "services" / "wired_service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source_root / "services" / "orphan_service.py").write_text("VALUE = 2\n", encoding="utf-8")

    return source_root, root / "main.py"


def _write_fixture(text: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "fixture_module.py"
    path.write_text(text, encoding="utf-8")

    return path


def test_source_checks_parse_rather_than_grep():
    """A module that only TALKS about a forbidden symbol is clean; one that uses it fires."""

    clean = _write_fixture(DOCSTRING_ONLY_MODULE)
    offender = _write_fixture(OFFENDING_MODULE)
    for bad in ASSEMBLY_FORBIDDEN + NODE_BODY_MARKERS:
        harness.check(f"docstring-only module does not reference {bad}",
              bad not in _referenced_names(clean))
    for bad in ("ChatPromptTemplate", "json.loads", "read_text", "loads"):
        harness.check(f"offending module references {bad}", bad in _referenced_names(offender))
    harness.check("docstring-only module does no I/O", not _io_offenders(clean), str(_io_offenders(clean)))
    harness.check("offending module does I/O", _io_offenders(offender) >= {"subprocess", "path.read_text"},
          str(_io_offenders(offender)))
    harness.check("docstring-only module has no ambient accessor",
          not {n for n in _called_names(clean) if n.endswith("current")})
    harness.check("offending module has an ambient accessor", "Context.current" in _called_names(offender))
    nested = _write_fixture(NESTED_DEFINITION_MODULE)
    reported = {inner for _outer, inner, _line in _nested_definitions(nested)}
    harness.check("a nested definition the enclosing function only calls is reported",
          reported == {"_only_called"}, str(reported))
    for spared, why in (("_handed_back", "the factory hands it back"),
                        ("registered", "a decorator registers it"),
                        ("squared", "a lambda is not a definition")):
        harness.check(f"{spared} is not reported - {why}", spared not in reported)
    deferred = _write_fixture(FUNCTION_IMPORT_MODULE)
    inside = {(outer, name) for outer, name, _line in _function_level_imports(deferred)}
    harness.check("an import inside a function is reported against that function",
          inside == {("deferred", "json"), ("inner", "pathlib")}, str(inside))
    harness.check("a module-level import is not reported, however much the prose discusses one",
          not any(outer == "clean" for outer, _name in inside))
    source_root, entry_point = _write_reachability_fixture()
    reachable = _modules_reachable_from_entry_point(source_root, entry_point)
    harness.check("a module reached through a chain of imports is reachable",
          source_root / "services" / "wired_service.py" in reachable)
    harness.check("`from cli import run_command` resolves the MODULE that holds the symbol",
          source_root / "cli.py" in reachable)
    harness.check("a module nothing imports is not reachable",
          source_root / "services" / "orphan_service.py" not in reachable)



def _top_level_definitions(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))

    return [(node.name, node.lineno) for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def test_no_module_defines_a_name_twice():
    """A top-level name defined twice is a silent shadow: the second wins, the first is unreachable.
    In a test module it is worse - one test never runs and the suite stays green."""

    cases = Path(__file__).resolve().parent
    for path in sorted([*SRC.rglob("*.py"), *cases.glob("test_*.py")]):
        seen: dict[str, int] = {}
        for name, line in _top_level_definitions(path):
            harness.check(f"{path.name} defines {name} once", name not in seen,
                  f"line {line} redefines the one at line {seen.get(name)}")
            seen.setdefault(name, line)


def _state_fields() -> dict[str, set[str]]:
    """Every `*State` class in graph_state.py with its annotated fields, bases included."""

    tree = ast.parse((SRC / "graph" / "graph_state.py").read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name.endswith("State")}
    fields: dict[str, set[str]] = {}
    for name, node in classes.items():
        own = {item.target.id for item in node.body
               if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)}
        inherited = set()
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in classes:
                inherited |= {item.target.id for item in classes[base.id].body
                              if isinstance(item, ast.AnnAssign)
                              and isinstance(item.target, ast.Name)}
        fields[name] = own | inherited

    return fields


def test_state_constructors_pass_only_declared_fields():
    """`RunState(items=...)` where `items` is not a field is a keyword a TypedDict accepts without
    complaint and the graph never reads - dead weight in every snapshot, and a lie next to the
    docstring that says the field was removed."""

    fields = _state_fields()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            declared = fields.get(node.func.id)
            if declared is None:
                continue
            unknown = sorted(kw.arg for kw in node.keywords if kw.arg and kw.arg not in declared)
            harness.check(f"{path.relative_to(SRC)}:{node.lineno} {node.func.id}(...) passes only declared fields",
                  not unknown, f"undeclared: {unknown}")


# The words this repository bans outright - in code, comments, docstrings and prompts alike. A
# banned word left in one corner is the copy the next generated package inherits. The list lives
# here, in the test, and the scan covers `src/` only, so the rule cannot eat itself.
BANNED_WORDS = ("verdict", "ledger")


def test_no_banned_word_in_source():
    """`verdict` is `decision`; `ledger` is `store`. See the programming use-case file."""

    import re

    pattern = re.compile(r"\b(" + "|".join(BANNED_WORDS) + r")\w*", re.IGNORECASE)
    for path in sorted(SRC.rglob("*.py")):
        hits = pattern.findall(path.read_text(encoding="utf-8"))
        harness.check(f"{path.relative_to(SRC)} uses no banned word", not hits, str(sorted(set(hits))))


def _signatures_crammed_onto_one_line(path: Path):
    """Every multi-parameter `def` in `path` whose parameters do not each get their own line, as
    `(line, name)`. One parameter per line is what makes a signature scannable in a diff, and it is
    the rule that quietly loses whenever a parameter is added to a function that already fits."""

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if len(parameters) < 2:
            continue
        if len({parameter.lineno for parameter in parameters}) != len(parameters):
            yield node.lineno, node.name


def _docstrings_with_a_blank_line(path: Path):
    """Every function or class docstring in `path` carrying a blank line, as `(line, name)`. A
    docstring is compact prose; a blank line in one is a paragraph break that belongs in a comment
    above the code it describes, where it can name the thing it is talking about."""

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        # A `@tool` DOCSTRING IS NOT DOCUMENTATION. It is the description the model reads when it
        # decides whether to call the tool and how, so its paragraphs are laid out for that reader
        # and compacting one changes a contract rather than a comment.
        decorators = {getattr(d, "id", getattr(d, "attr", "")) for d in node.decorator_list} \
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set()
        if "tool" in decorators:
            continue
        written = ast.get_docstring(node, clean=False)
        if written and "\n\n" in written:
            yield node.lineno, node.name


# THE SWEEP IS THE POINT, not the two rules. Both are style rules with an obvious shape and no
# enforcement, which is how a package drifts past hundreds of sites before anything compares the code
# to them. Measured: a signature was squashed onto one line to get a different check to pass, and
# nothing in a suite of 38,918 tests noticed.
def test_every_multi_parameter_signature_gives_each_parameter_its_own_line():
    """One parameter per line, so adding or removing one is a one-line diff."""

    for path in sorted(SRC.rglob("*.py")):
        for line, name in _signatures_crammed_onto_one_line(path):
            harness.check(f"{path.relative_to(SRC)}:{line} {name}() is one parameter per line", False,
                          "put each parameter on its own line, return type at the end")


def test_no_docstring_carries_a_blank_line():
    """A docstring is compact; the discussion goes in a comment beside the code it explains."""

    for path in sorted(SRC.rglob("*.py")):
        for line, name in _docstrings_with_a_blank_line(path):
            harness.check(f"{path.relative_to(SRC)}:{line} {name} has a compact docstring", False,
                          "join the paragraphs, or move the discussion into a comment above the code")


def test_no_test_waits_on_a_clock():
    """A sleeping test is slow on every machine and still races on a loaded one, so the suite holds no timer. A helper
    process that must merely exist waits on its stdin instead (the owner, 2026-09-28)."""

    tests_dir = Path(__file__).resolve().parent.parent
    # This file states the ban, so it holds the word as DATA. Every other test file is checked.
    for path in sorted(path for path in tests_dir.rglob("*.py") if path != Path(__file__).resolve()):
        text = path.read_text(encoding="utf-8")
        sleeping = [line.strip() for line in text.splitlines()
                    if "sleep(" in line and not line.strip().startswith("#")]
        harness.check(f"{path.relative_to(tests_dir)} waits on no clock", not sleeping,
              "; ".join(sleeping[:2]) + " - wait on the thing itself: stdin, a file, or an injected clock")
