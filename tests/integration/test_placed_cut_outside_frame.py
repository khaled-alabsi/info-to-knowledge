"""A run's phase 4 stopped on a division by zero while naming the needed frames an earlier chapter already placed: a
placed cut made on a larger frame recorded its rectangle below the bottom of a smaller needed frame, the crop there
held no pixels, and the content comparison divided by its height. Made data: two frames and a placed cut written to a
run folder, through the real match step that reads them from disk."""

import cv2
import numpy

from support import fixtures as fx
from support import harness
from services import figure_match_service


def test_a_placed_cut_whose_rectangle_lies_below_a_smaller_frame_stops_nothing_defect():
    # Phase 4 stopped with a ZeroDivisionError; the empty crop is now not compared, and the chart is still found
    paths = fx.make_paths()
    frames, cuts_dir = paths.output_root / "frames", paths.output_root / "04_writing" / "images" / "cuts"
    sections = paths.output_root / "04_writing" / "sections"
    for folder in (frames, cuts_dir, sections):
        folder.mkdir(parents=True, exist_ok=True)
    large = numpy.full((1080, 1920, 3), 255, numpy.uint8)
    for box in range(4):
        cv2.rectangle(large, (300 + box * 260, 820), (480 + box * 260, 960), (120, 80, 40), -1)
    cv2.imwrite(str(frames / "frame-002.jpg"), large)
    cv2.imwrite(str(cuts_dir / "cut-001.png"), large[800:980, 280:1300])
    # The same chart on a smaller frame, at the cut's own size
    small = numpy.full((720, 1280, 3), 255, numpy.uint8)
    small[300:480, 100:1120] = large[800:980, 280:1300]
    cv2.imwrite(str(frames / "frame-009.jpg"), small)
    (sections / "heading-001-1.md").write_text('Flow. [F2.1]\n\n![flow](cut-001 "c") [F2.1]\n', encoding="utf-8")
    cuts = {"cut-001": {"frame": "frame-002", "file": "04_writing/images/cuts/cut-001.png",
                        "lookRect": [280, 800, 1300, 980]}}
    try:
        matches = figure_match_service.list_placed_matches(paths, ["frame-009"], cuts, ["heading-001-1"])
    except ZeroDivisionError as error:
        matches = error
    harness.check("nothing raises, and the smaller frame is named as showing the placed chart",
                  matches == ["frame-009 shows what cut-001 (from frame-002) already shows"], repr(matches))
