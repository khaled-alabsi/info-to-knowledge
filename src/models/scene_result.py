"""One scene the segmentation found: a run of frames with no cut inside it."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SceneResult"]


@dataclass(frozen=True)
class SceneResult:
    start_frame: int                # the first frame of the scene
    end_frame: int                  # one past its last frame

    @property
    def length(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def representative_frame(self) -> int:
        """The middle frame: a cut or a slide transition is still settling at either end."""

        return (self.start_frame + self.end_frame) // 2
