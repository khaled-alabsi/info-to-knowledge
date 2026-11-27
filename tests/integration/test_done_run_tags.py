"""Every finished run folder carries tags (the owner: "i dont see tags in the already done runs"): `apply_tag_rules`
over made run folders named like the real ones, with the default settings' naming rules and each folder's frames file.
Before, a lecture series, a PDF and an audio run had none."""

import json
import tempfile
from pathlib import Path

from support import harness

from services import run_operations_service
from services import run_tags_service

# Each made run folder, the medium its frames file records, and the tags it must carry
FOLDERS = (("Lecture 7 Agentic Coding", "video", ["lecture", "from-video"]),
           ("6100l-lecture-3-multi_360p_16_9", "video", ["lecture", "from-video"]),
           ("pdf-02-report-v2", "pdf-text-only", ["from-pdf"]),
           ("Track 15", "audio", ["from-audio"]),
           ("39c3-1253-eng-deu-spa-Closing_Ceremony_sd", "video", ["ccc-congress", "from-video"]))


def test_every_finished_run_folder_gets_its_rule_and_format_tags_defect():
    # Finished runs showed no tags: the naming rules missed the lecture series, and nothing gave a run its format.
    # Every run folder now gets its naming rules' tags and the format of the file it was made from
    root = Path(tempfile.mkdtemp())
    for name, medium, _ in FOLDERS:
        (root / name).mkdir()
        (root / name / "02_frames.json").write_text(json.dumps({"items": [], "medium": medium}), encoding="utf-8")
    run_operations_service.apply_tag_rules([root])
    for name, _, expected in FOLDERS:
        held = run_tags_service.read_tags(root / name)
        harness.check(f"{name}: {', '.join(expected)}", held == expected, str(held))
