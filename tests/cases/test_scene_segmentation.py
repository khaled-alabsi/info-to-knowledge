"""Scene mode cuts one frame per scene, however long the scene is on screen. REQUIREMENTS.md R1.

The segmentation is a pure function of a difference score per frame, so it is tested on a score
series with known cut points. The measure itself is tested on two small synthetic frames.
"""

import numpy

from support import harness

from utils import scene_segmentation_utils

FPS = 10.0
THRESHOLD = 0.3


def _build_scores(scene_lengths: list[int]) -> list[float]:
    """A score per frame: a jump above the threshold at the first frame of every scene but the first."""

    scores: list[float] = []
    for length in scene_lengths:
        scores.extend([0.9 if scores else 0.0] + [0.01] * (length - 1))

    return scores


def _segment(scene_lengths: list[int],
             min_scene_seconds: float = 0.0,
             max_count: int = 60) -> list:
    return scene_segmentation_utils.split_into_scenes(_build_scores(scene_lengths), FPS, THRESHOLD,
                                                   min_scene_seconds, max_count)


def test_n_slides_give_n_scenes_whatever_their_length():
    scenes = _segment([400, 40, 120, 30])
    harness.check("four slides are four scenes", len(scenes) == 4, str(scenes))
    harness.check("a slide held ten times longer is still one scene",
                  [(scene.start_frame, scene.end_frame) for scene in scenes]
                  == [(0, 400), (400, 440), (440, 560), (560, 590)], str(scenes))


def test_a_static_clip_is_one_scene():
    harness.check("a clip with no cut is a single scene", len(_segment([600])) == 1)


def test_scenes_come_back_in_chronological_order():
    starts = [scene.start_frame for scene in _segment([50, 300, 20, 90])]
    harness.check("start frames increase", starts == sorted(starts), str(starts))


def test_a_scene_shorter_than_the_minimum_is_merged_into_its_neighbour():
    scenes = _segment([100, 5, 100], min_scene_seconds=1.0)
    harness.check("a half-second flash is not a scene of its own", len(scenes) == 2, str(scenes))
    harness.check("its frames joined the scene before it", (scenes[0].start_frame, scenes[0].end_frame) == (0, 105),
                  str(scenes))
    leading = _segment([5, 100], min_scene_seconds=1.0)
    harness.check("a too-short FIRST scene joins the one after it",
                  [(scene.start_frame, scene.end_frame) for scene in leading] == [(0, 105)], str(leading))


def test_the_cap_drops_the_shortest_scenes_first_and_keeps_the_order():
    scenes = _segment([100, 20, 300, 30, 200], max_count=3)
    harness.check("the cap holds", len(scenes) == 3, str(scenes))
    harness.check("the three longest survive, still in order",
                  [scene.end_frame - scene.start_frame for scene in scenes] == [100, 300, 200], str(scenes))


def test_each_scene_is_represented_by_its_middle_frame():
    scenes = _segment([100, 40])
    harness.check("the representative frame is mid-scene, away from the transition",
                  [scene.representative_frame for scene in scenes] == [50, 120], str(scenes))


def test_degenerate_arguments_are_refused_or_empty_never_a_hang():
    harness.check("no frames means no scenes", scene_segmentation_utils.split_into_scenes([], FPS, THRESHOLD, 0, 60) == [])
    for label, arguments in (("a zero frame rate", ([0.0, 0.0], 0.0, THRESHOLD, 0.0, 60)),
                             ("a zero scene budget", ([0.0, 0.0], FPS, THRESHOLD, 0.0, 0))):
        try:
            scene_segmentation_utils.split_into_scenes(*arguments)
        except ValueError:
            harness.check(f"{label} is refused", True)
        else:
            harness.check(f"{label} is refused", False)


def test_the_difference_measure_is_zero_for_the_same_frame_and_one_for_black_against_white():
    black = numpy.zeros((18, 32, 3), dtype=numpy.uint8)
    white = numpy.full((18, 32, 3), 255, dtype=numpy.uint8)
    harness.check("identical frames differ by 0", scene_segmentation_utils.compute_frame_difference(black, black) == 0.0)
    harness.check("black against white differs by 1",
                  abs(scene_segmentation_utils.compute_frame_difference(black, white) - 1.0) < 1e-6)
    half = black.copy()
    half[:, :16] = 255
    harness.check("half the frame changing is a difference of one half",
                  abs(scene_segmentation_utils.compute_frame_difference(black, half) - 0.5) < 1e-6)
