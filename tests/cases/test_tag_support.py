"""A frame's new subject tag is held to the frame (DEFECTS 457): a capitalised tag no earlier frame has, whose longest
word stands nowhere in the frame's notes or speech, is dropped as the notes are written."""

from support import fixtures as fx
from support import harness

from models import output_contract
from services import phase_file_service
from services import tag_support_service


def _run(record_tags: list[str],
         notes: dict,
         speech: str = "Speaker A: Let me look at a couple of examples.",
         earlier_tags: list[str] | None = None) -> list[str]:
    paths = fx.make_paths()
    items = [{"slug": "frame-000", "index": 0, "timestamp": 0.0, "transcriptBefore": "", "transcriptCurrent": "",
              "record": {"tags": earlier_tags or ["funding models"]}},
             {"slug": "frame-001", "index": 1, "timestamp": 5.0, "transcriptBefore": "", "transcriptCurrent": speech}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})

    return tag_support_service.drop_unsupported_new_tags(paths, "frame-001", {**notes, "tags": record_tags})["tags"]


NOTES = {"facts": ["The talk turns to examples of funding models."], "screen": '- slide: "Examples"'}


def test_a_new_tag_the_frame_does_not_hold_is_dropped_defect():
    # Defect: a chart slide before the first example was tagged with the two projects its next frames show, and the
    # repair cannot edit tags. Fix: a new capitalised tag with its longest word in neither notes nor speech goes
    tags = ["funding models", "Adblock Plus", "Drupal", "examples"]
    harness.check("the two later subjects go", _run(tags, NOTES) == ["funding models", "examples"])
    harness.check("a new tag the speech holds is kept and the other still goes",
                  _run(tags, NOTES, "Speaker A: Drupal is the first example.") == ["funding models", "Drupal", "examples"])
    harness.check("a tag an earlier frame has is copied as it stands", _run(tags, NOTES, earlier_tags=["Adblock Plus"]) ==
                  ["funding models", "Adblock Plus", "examples"])
    harness.check("a tag in lower case is a concept and stays", _run(["funding models", "grants"], NOTES) == ["funding models", "grants"])
    harness.check("no tag is left empty", _run(["Adblock Plus"], NOTES) == ["Adblock Plus"])
