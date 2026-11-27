"""Lecture 7's run (its git-ignored run folder, read in place, never written) and its review's todo 3, about frame
116's note "Any questions about these?": the request named frame 116 by number alone, and the reviewer looked for it at
04:30 and 06:00, though the frame stands at 46:26 (run 17, DEFECTS 613). The real todo through the real hints and the
real request text."""

import json
from pathlib import Path

from support import harness

from models.run_paths import RunPaths
from prompts import review_messages
from services import review_conversation_service

RUN = Path(__file__).resolve().parents[2] / "output" / "Lecture 7 Agentic Coding"


def test_the_review_request_names_frame_116_at_its_time_defect():
    # Defect: a todo's request named its frames without their times. Fix: each frame named with the time the reads take
    store_path = RUN / "05_review" / "todos.json"
    if not store_path.is_file():
        print(f"Lecture 7's review store is not here: skipped")
        return
    todo = next((todo for todo in json.loads(store_path.read_text(encoding="utf-8")).get("todos") or []
                 if "F116.6" in json.dumps(todo) or "Any questions were raised" in str(todo.get("quote"))), None)
    if todo is None:
        print("todo 3 is not in the store: skipped")
        return
    paths = RunPaths(repository=RUN, module="Lecture 7 Agentic Coding.mp4", output_root_override=RUN)
    text = review_messages.build_resolve_text(review_conversation_service._add_todo_hints(paths, todo), 3, ["F116.6"])
    harness.check("frame 116 is named at 46:26", "frame 116 at 46:26" in text, text[:600])
