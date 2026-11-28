"""A frame left out first as shown by a placed cut and then for personal data: the real tracking line the worker reads
after a frame tool calls it left out, never shown by a placed frame (DEFECTS 731)."""

import tempfile
from pathlib import Path

from support import harness

from cases import test_illustration_actions as action_cases
from services import document_file_service
from validators import document_validator


def test_a_frame_left_out_again_for_personal_data_is_tracked_as_left_out_defect():
    # Defect: a frame left out as "shown by the placed cut", then for personal data, stayed "shown by a placed frame".
    # Fix: the latest leave_out reason of a frame decides, so the tracking line says only "left out".
    paths, _ = action_cases._build_run(Path(tempfile.mkdtemp()))
    store = action_cases._build_store()
    store["leftOut"] = [
        {"frame": "frame-057", "chapter": "chapter-04-text-7", "reason": "already shown by the placed cut"},
        {"frame": "frame-057", "chapter": "chapter-04-text-7",
         "reason": "it shows a table of client accounts with personal data"}]
    document_file_service.write_image_store(paths, store)
    sections = [{"slug": "heading-041-1", "neededFrames": ["frame-057"]}]
    line = document_validator.describe_open_needed_frames(paths, sections, "chapter-04-text-7", store)
    harness.check("the frame left out for personal data is tracked as left out",
                  "frame-057 left out" in line and "shown by a placed frame" not in line, line)
