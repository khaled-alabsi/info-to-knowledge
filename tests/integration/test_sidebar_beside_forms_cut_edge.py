"""A meeting's frame of a design guide, its sidebar's entries read by the text reader as one line with the form lines
beside them (`tests/resources/cut-edges/sidebar-beside-forms-words.json`: the reader's real word boxes, each word
replaced by a placeholder). The chapter check refused every cut of the frame as running through those lines, though
each left edge stood in the gap between the sidebar and the forms, and the chapter blocked after 20 undos."""

import json
from pathlib import Path

from support import harness

from utils import frame_region_utils

FIXTURE = Path(__file__).resolve().parents[1] / "resources" / "cut-edges" / "sidebar-beside-forms-words.json"


def test_a_cut_edge_in_the_gap_beside_a_sidebar_is_no_cut_line_and_an_edge_through_a_form_line_still_is_defect():
    # An edge in a gap over a pane gap wide cut a line the reader had joined across two panes. Now it cuts none, while
    # an edge between two close words of one line still does
    look = json.loads(FIXTURE.read_text(encoding="utf-8"))
    beside = frame_region_utils.list_lines_cut_at_sides(look["rects"]["cut-026"], look["words"])
    harness.check("cut-026's left edge, between the sidebar and the forms, cuts no line", beside == [],
                  str([line["box"] for _side, line in beside]))
    through = frame_region_utils.list_lines_cut_at_sides(look["rects"]["cut-027"], look["words"])
    harness.check("cut-027's left edge, through a form line, still cuts it",
                  bool(through) and {side for side, _line in through} == {"left"},
                  str([line["box"] for _side, line in through]))
