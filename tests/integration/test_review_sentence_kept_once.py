"""A review resolution replaced a sentence with one the document already held, beside it in its block or under another
heading; the edited document said it twice and the resolution was refused (DEFECTS 715). Through the real block filling
of a resolution against a review's working document written in a run folder, the real edit and the real repeat finder
the validator runs, on an invented document: each sentence stands once and nothing is said twice."""

import tempfile
from pathlib import Path

from support import harness
from models.run_paths import RunPaths
from services import review_file_service
from utils import document_edit_utils
from validators import document_folder_validator

FIRST = "The Gateway refunds an order within a day, and a new switch in `AcmeSync` passes the order id through."
LIMIT = "The Gateway caps each order at 500 units, and a larger order waits for a lead's approval."
ASKED = "Asked whether refunds are fixed, the team answered that `AcmeSync` now passes the order id through."
DOCUMENT = (f"# Gateway review\n\n## Refunds\n\n{FIRST} {ASKED}\n\n## Limits\n\n{LIMIT}\n\n"
            "Asked about larger orders, the team answered that a lead approves them.\n")


def _edit(answer: dict) -> str:
    run = Path(tempfile.mkdtemp()) / "run"
    paths = RunPaths(repository=run, module="gateway.mp4", output_root_override=run)
    working = review_file_service.build_working_document_path(paths)
    working.parent.mkdir(parents=True)
    working.write_text(DOCUMENT, encoding="utf-8")

    return document_edit_utils.apply_edit(DOCUMENT, review_file_service._fill_from_blocks(paths, answer))


def test_a_resolution_writing_again_a_sentence_the_document_holds_says_it_once_defect():
    # The copy beside the replaced sentence, and the copy under another heading, are each kept once
    beside = _edit({"status": "resolved", "block": 1, "sentences": [{"text": FIRST, "frames": [4]}], "replaces": ASKED})
    harness.check("a sentence beside the replaced one stands once", beside.count(FIRST) == 1 and ASKED not in beside,
                  beside)
    harness.check("and nothing is said twice", not document_folder_validator.find_repeated_document_sentences(beside),
                  str(document_folder_validator.find_repeated_document_sentences(beside)))
    other = _edit({"status": "resolved", "block": 3, "sentences": [{"text": LIMIT, "frames": [6]}],
                   "replaces": "Asked about larger orders, the team answered that a lead approves them."})
    harness.check("a sentence of the same section's other block stands once", other.count(LIMIT) == 1
                  and "Asked about larger" not in other, other)
    harness.check("and nothing is said twice there either",
                  not document_folder_validator.find_repeated_document_sentences(other), "")
