"""Where one scene ends and the next begins. Pure functions: a difference score per frame in, scenes
out. Nothing here opens a video.

The measure is carried from the archived program: the mean absolute grayscale difference between two
frames. There it SELECTED frames, the N most different ones, which sampled a long slide many times
and missed a short one. Here it only finds the cuts, and every scene is then represented once.
"""

from __future__ import annotations

import cv2
import numpy

from models.scene_result import SceneResult

__all__ = ["compute_frame_difference", "split_into_scenes"]

_WHITE = 255.0


def _merge_short_scenes(scenes: list[SceneResult],
                        min_frames: int) -> list[SceneResult]:
    """A scene shorter than the minimum joins the scene before it. A short FIRST scene joins the next."""

    merged: list[SceneResult] = []
    for scene in scenes:
        if merged and (scene.length < min_frames or merged[-1].length < min_frames):
            merged[-1] = SceneResult(merged[-1].start_frame, scene.end_frame)
        else:
            merged.append(scene)

    return merged


def _drop_shortest_scenes(scenes: list[SceneResult],
                          max_count: int) -> list[SceneResult]:
    """The `max_count` longest scenes, still in chronological order."""

    if len(scenes) <= max_count:
        return scenes
    kept = sorted(scenes, key=lambda scene: (-scene.length, scene.start_frame))[:max_count]

    return sorted(kept, key=lambda scene: scene.start_frame)


def compute_frame_difference(frame_a: numpy.ndarray,
                             frame_b: numpy.ndarray) -> float:
    """The mean absolute grayscale difference of two BGR frames, from 0 (the same) to 1."""

    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)

    return float(numpy.mean(cv2.absdiff(gray_a, gray_b))) / _WHITE


def split_into_scenes(scores: list[float],
                      fps: float,
                      threshold: float,
                      min_scene_seconds: float,
                      max_count: int) -> list[SceneResult]:
    """`scores[i]` is how much frame `i` differs from frame `i - 1`. A score above `threshold` starts
    a new scene at frame `i`. Returns the scenes in chronological order."""

    if fps <= 0:
        raise ValueError(f"fps is {fps}: a frame rate must be positive to turn seconds into frames")
    if max_count <= 0:
        raise ValueError(f"max_count is {max_count}: at least one scene must be allowed")
    if not scores:
        return []

    # STEP 1: cut wherever the frame jumps
    starts = [0] + [index for index, score in enumerate(scores) if index > 0 and score > threshold]
    scenes = [SceneResult(start, end) for start, end in zip(starts, starts[1:] + [len(scores)])]

    # STEP 2: a scene too short to be one joins its neighbour
    scenes = _merge_short_scenes(scenes, int(round(min_scene_seconds * fps)))

    # STEP 3: hold the frame budget by giving up the shortest scenes first
    return _drop_shortest_scenes(scenes, max_count)
