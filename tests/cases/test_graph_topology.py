# <!-- sync: seed/cases.test_graph_topology v1 -->
"""Compiling proves the nodes exist. The render proves they are wired as designed.

A template, not seed: it names the generated graph's nodes, and a playbook that renames or adds a
node updates the names here in the same change.
"""

from support import harness

from support.graph_inspection import build_graph_for_inspection

NODES = ("transcribe", "frames_preparing", "frames_processing", "finalize", "review", "critique_collection")
LOOP_NODE = "frames_processing"


def test_the_graph_compiles():
    harness.check("the graph compiles", build_graph_for_inspection() is not None)


def test_every_designed_node_is_present():
    rendered = build_graph_for_inspection().get_graph().draw_mermaid()
    for node in NODES:
        harness.check(f"node {node!r} is in the compiled graph", node in rendered)


def test_frames_processing_loops_back_to_itself():
    """Frames processing must route back to itself, or only the first batch of frames is processed."""

    edges = build_graph_for_inspection().get_graph().edges
    self_edges = [e for e in edges if e.source == LOOP_NODE and e.target == LOOP_NODE]
    harness.check("frames processing loops back to itself", bool(self_edges),
                  "without this only the first batch is processed")


def test_frames_preparing_can_reach_the_end_directly():
    """A failed frames preparing must terminate the run, not fall through into the loop."""

    edges = build_graph_for_inspection().get_graph().edges
    harness.check("frames preparing can end the run",
                  any(e.source == "frames_preparing" and e.target == "__end__" for e in edges))


def test_frames_preparing_can_skip_frames_processing_when_no_frames_are_left():
    edges = build_graph_for_inspection().get_graph().edges
    harness.check("frames preparing can go straight to finalize",
                  any(e.source == "frames_preparing" and e.target == "finalize" for e in edges),
                  "no frames left must bypass frames processing")


def test_draw_mermaid_needs_no_optional_dependency():
    """draw_ascii() needs `grandalf`; the default suite must not depend on it."""

    rendered = build_graph_for_inspection().get_graph().draw_mermaid()
    harness.check("draw_mermaid works without extra packages", rendered.strip().startswith("---"))


def test_every_route_returns_exactly_the_names_its_edge_map_holds():
    import typing

    from graph import routing

    problems = []
    for source, branches in build_graph_for_inspection().builder.branches.items():
        for route_name, branch in branches.items():
            named = set(typing.get_args(typing.get_type_hints(getattr(routing, route_name))["return"]))
            if named != set(branch.ends):
                problems.append(f"{route_name}: returns {sorted(named)}, edge map holds {sorted(branch.ends)}")
    harness.check("each route's return annotation and its edge map name the same values", not problems,
                  "; ".join(problems))
