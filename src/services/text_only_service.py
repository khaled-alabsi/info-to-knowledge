"""Phase 2's text-only mark: the frame classifier's tags tell, for every kept frame, whether it holds only text an OCR
carries whole.

The tags are the ones phase 2's classifier stage read once (`knowledge_filter_service.read_tags`, written onto each
item by `add_tags`):
a frame tagged `text` is marked. Each kept frame gets `isTextOnly` and `textOnlyScore` (1 when every ink measure is
within the classifier's cuts, 0 when not); phase 3 sends a marked frame as its read text instead of its image while
`framesProcessing.isTextOnlyFrameSentAsText` is on.
"""

from __future__ import annotations

from services import knowledge_filter_service

__all__ = ["TEXT", "mark_text_only"]

# The classifier's tag for a frame that holds only text
TEXT = "text"


def mark_text_only(items: list[dict],
                   tags: dict[str, dict]) -> list[dict]:
    """Every item with its `tag`, `isTextOnly` and `textOnlyScore`, from the classifier's `tags`; a frame with no tag stops phase 2
    by name."""

    marked = []
    for item in items:
        tagged = knowledge_filter_service.find_tag(tags, item)
        # The item's tag, written once from the classifier (`add_tags`) and changed only by the board stage's retag of
        # a frame beside a board frame: written back from the classifier, the run's record said other for a frame
        # handled as a board (DEFECTS 126)
        is_board_by_neighbour = bool(item.get("isBoardByNeighbour"))
        tag = item.get("tag") or tagged["tag"]
        marked.append({**item, "tag": tag, "isTextOnly": tag == TEXT,
                       "textOnlyScore": 1.0 if tagged["isPlainText"] and not is_board_by_neighbour else 0.0})

    return marked
