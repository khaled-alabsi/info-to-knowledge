"""The frame tools a chapter of phase 4 may use, one schema each. REQUIREMENTS.md R10.

The agent chooses which frames to show and cuts what it wants from them; it places a cut by writing its line into the
chapter's markdown. The program carries each action out and checks it, and never makes the choice. Each schema's docstring is the description the
model reads when it decides whether to call the tool, so it is written for that reader.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["LookAction", "CutAction", "MarkAction", "MaskAction", "LeaveOutAction", "UndoCutAction",
           "ReplaceWithDiagramAction", "ACTIONS"]


class LookAction(BaseModel):
    """Look at one frame: the image with a grid line every 100 pixels, its width and height, and the boxes the program found on it (T = a block of text, P = a panel, table or window, G = a figure no panel holds, such as a chart or a group of shapes), each with its id and pixel box. These are your anchor points for a cut. Given a cut's id instead, it shows that cut as it stands, its size and its C boxes, the pixels a `mark` or `mask` on it uses."""

    frame: str = Field(description="the frame's id with three digits, as the notes name it (frame-000, frame-017), or a "
                                   "cut's id, cut-<n>")
    grid_lines: int = Field(default=0, description="how many grid lines to draw across each side, evenly spaced and "
                                                   "labelled in the image's own pixels, 1 to 40; left out, a frame gets a "
                                                   "line every 100 pixels and a cut none. More lines place an edge "
                                                   "between two close lines of text")


class CutAction(BaseModel):
    """Cut a region out of a frame you looked at. Name the boxes to cover, or give a rectangle in the looked-at frame's pixels, plus a margin. The program cuts it from the recording at full resolution, saves it, and shows it to you with its size, the boxes found on the cut (C ids) and any warning; look at it and cut again when the part that matters is not whole or readable."""

    # Three digits, as the notes name a frame for the tools: told "frame-<n>", a writer cut "frame-1" for frame-000 and
    # the strict run stopped (DEFECTS 500)
    frame: str = Field(description="the frame's id with three digits, as the notes name it for the frame tools: "
                                   "frame-000, frame-017")
    box_ids: list[str] = Field(default_factory=list, description="ids of boxes from your look at this frame, such as T3 "
                                                                 "or P1; the cut covers all of them")
    rect: list[int] = Field(default_factory=list, description="or a rectangle [x0, y0, x1, y1] in the looked-at "
                                                              "frame's pixels; used when no box id is given. A PDF "
                                                              "figure frame with neither is cut whole, no look needed")
    margin: int = Field(default=12, description="pixels of the looked-at frame added around the target on every side")


class MarkAction(BaseModel):
    """Draw a box around a part of a cut, or a numbered marker at its corner, so the reader sees where to look. One to three marks per image."""

    cut: str = Field(description="the cut's id, cut-<n>")
    box_id: str = Field(default="", description="a box id on the cut, such as C2")
    rect: list[int] = Field(default_factory=list, description="or a rectangle [x0, y0, x1, y1] in the cut's pixels")
    number: int = Field(default=0, description="the marker's number, matching a numbered step in the text; 0 for a box only")


class MaskAction(BaseModel):
    """Black out a part of a cut that shows a secret, a credential, a token, an account or customer number, a name or other personal data. Name the words to hide, as `look` on the cut lists them, and the program finds them on the cut; or name a box id, or give a rectangle in the cut's pixels."""

    cut: str = Field(description="the cut's id, cut-<n>")
    words: str = Field(default="", description="the words to hide as the cut shows them, such as the start of a secret value; "
                                               "the program masks every word on the cut that holds them")
    box_id: str = Field(default="", description="or a box id on the cut, such as C2")
    rect: list[int] = Field(default_factory=list, description="or a rectangle [x0, y0, x1, y1] in the cut's pixels")


class UndoCutAction(BaseModel):
    """Undo one cut still in use: it is never placed, and its frame may be cut again in its place. Name the cut when
    you mean a particular one; with no name the newest cut still in use is undone, whichever frame it belongs to. An
    older cut you do not want is simply left out of your answer."""

    cut: str | None = Field(default=None, description="the cut's id, cut-<n>; leave it out for the newest cut")


class LeaveOutAction(BaseModel):
    """Record why a frame suggested as needed is not shown: the text already carries it, or a better frame shows it."""

    frame: str = Field(description="the frame's id with three digits, as the notes name it: frame-000, frame-017")
    reason: str = Field(description="one sentence saying why it is left out")


class ReplaceWithDiagramAction(BaseModel):
    """Record that a needed screenshot of a diagram is replaced by a complete Mermaid diagram preserving all visual information the reader needs."""

    frame: str = Field(description="the frame's id with three digits, as the notes name it: frame-000, frame-017")
    reason: str = Field(description="one sentence naming the boundaries, labels and relationships the complete diagram preserves")


# tool name -> the schema of its arguments, looked up by `illustration_action_service.run_action`; a chapter is offered
# these tools, in this order (`write_actions.FRAME_TOOLS`).
ACTIONS: dict[str, type[BaseModel]] = {
    "look": LookAction,
    "cut": CutAction,
    "mark": MarkAction,
    "mask": MaskAction,
    "leave_out": LeaveOutAction,
    "replace_with_diagram": ReplaceWithDiagramAction,
    "undo_cut": UndoCutAction,
}
