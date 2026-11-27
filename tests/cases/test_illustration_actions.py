"""Phase 4's frame tools and publishing, REQUIREMENTS.md R10.

One agent decides which frames to show, cuts what it wants from them and places each image where it wants; the
program carries out each action, refuses one that cannot be done in words the agent can act on, and writes the
folder a reader opens. Tested here: the document as addressable blocks, the mapping of a cut from the frame the
agent saw to the recording at full resolution, every action and its refusals, the
whole phase writing the folder named after the meeting (R13), and the folder's validator.
"""

import json
import logging
import tempfile
from pathlib import Path

import cv2
import numpy

from support import media_fixtures
from support import harness
from support import merge_inputs

from graph import routing
from models import document_heading_spec
from models import output_contract
from models.illustration_context import IllustrationContext
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.pipeline_errors import MissingPhaseInput
from models.publication_result import PublicationResult
from models.run_paths import RunPaths
from services import document_file_service
from schemas import illustration_actions
from services import document_folder_service
from services import illustration_action_service
from services import pdf_source_service
from services import phase_file_service
from services import publication_service
from services import run_paths_service
from services import text_detection_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import document_block_utils
from utils import document_text_utils
from utils import json_store_utils
from utils import frame_region_utils
from validators import document_folder_validator
from validators import document_validator
from models.validation_report import ValidationReport
from support import fixtures as fx

# The chapter heading the frame tools work under, "Ordering" in MARKDOWN
ORDERING_CHAPTER_ID = "B007"
# The undos a frame is given in the undo test, apart from the shipped settings
UNDO_LIMIT = 12
# The shortest side a cut may have in these tests' context
MIN_CUT_PIXELS = 120
# The window _build_run draws on the recording's 1920 x 1080 frame, kept at half that size
WINDOW_RECT = (400, 300, 1200, 800)

MARKDOWN = """# Orders ship from the new warehouse from March

## Executive summary

Orders ship from the new warehouse from March.

- The closing lists one decision.

## Agenda

1. [Ordering](#ordering): Walks through the order form

## Ordering

The order form needs the customer number and the delivery date. The form shows two fields and a button.

```mermaid
flowchart LR
    A["Order form"] --> B["Warehouse"]
```

## Decisions, action items and open points

### Decisions

- Orders ship from the new warehouse from March.
"""
PROVENANCE = {"paragraphs": [{"text": "The order form needs the customer number and the delivery date. The form shows two "
                                      "fields and a button.", "entries": [{"id": "F0.1"}, {"id": "F0.2"}]}],
              "summary": [{"text": "Orders ship from the new warehouse from March.", "entries": [{"id": "F1.1"}]}]}
ENTRIES = [{"id": "S0001", "kind": "speech", "text": "Welcome to the order walkthrough.", "start": 0.0, "end": 2.0,
            "timestamps": [0.0], "source": "transcript"},
           {"id": "F0.1", "kind": "facts", "text": "The order form at https://shop.example.com/cb?token=abc&page=2 "
                                                          "needs the customer number.", "timestamps": [4.0],
            "frames": [0], "source": "frame"},
           {"id": "F0.2", "kind": "facts", "text": "The form also asks for the delivery date.", "timestamps": [4.0],
            "frames": [0], "source": "frame"},
           {"id": "F1.1", "kind": "facts", "text": "Orders ship from the new warehouse from March.",
            "timestamps": [9.0], "frames": [1], "source": "frame"}]
NON_CHAPTER_HEADINGS = [output_contract.EXECUTIVE_SUMMARY_HEADING, output_contract.AGENDA_HEADING, *document_heading_spec.ENGLISH.list_closing()]


def test_the_document_is_split_into_blocks_the_agent_can_address_and_rendered_back():
    blocks = document_block_utils.split_into_blocks(MARKDOWN, {" ".join(part["text"].split()): [e["id"] for e in part["entries"]]
                                                               for part in PROVENANCE["paragraphs"]})
    kinds = [(block["kind"], block["section"]) for block in blocks]
    harness.check("the title, the headings, each paragraph, the list, the diagram, each with the section it falls under",
                  kinds == [("title", ""), ("heading", "Executive summary"), ("paragraph", "Executive summary"),
                            ("list", "Executive summary"), ("heading", "Agenda"), ("list", "Agenda"),
                            ("heading", "Ordering"), ("paragraph", "Ordering"), ("diagram", "Ordering"),
                            ("heading", NON_CHAPTER_HEADINGS[2]), ("heading", NON_CHAPTER_HEADINGS[2]), ("list", NON_CHAPTER_HEADINGS[2])], str(kinds))
    paragraph = blocks[7]
    harness.check("a paragraph carries the entries phase 4's provenance says it rests on",
                  paragraph["entryIds"] == ["F0.1", "F0.2"], str(paragraph))
    harness.check("the chapters are the second-level sections other than the summary, the agenda and the closing, "
                  "each named by its heading's id", document_block_utils.list_chapters(blocks, NON_CHAPTER_HEADINGS) == [ORDERING_CHAPTER_ID]
                  and paragraph["sectionId"] == ORDERING_CHAPTER_ID)
    twice = document_block_utils.split_into_blocks("# T\n\n## Questions\n\nOne.\n\n## Demo\n\nTwo.\n\n## Questions\n\n"
                                                   "Three.\n", {})
    chapters = document_block_utils.list_chapters(twice, NON_CHAPTER_HEADINGS)
    harness.check("two parts with the same title are two chapters, each with its own blocks",
                  len(chapters) == 3 and len(set(chapters)) == 3
                  and [block["text"] for block in twice if block.get("sectionId") == chapters[2] and block["kind"] == "paragraph"]
                  == ["Three."], str(twice))
    image = {"id": "I01", "kind": "image", "cut": "cut-001", "frame": "frame-000", "caption": "The form has two fields.",
             "alt": "Screenshot of the order form", "entryIds": ["F0.1"], "section": "Ordering", "text": ""}
    paragraph["text"] += " {{figure:I01}} has the two fields."
    rendered = document_block_utils.render_blocks([*blocks[:8], image, *blocks[8:]], {"I01": "resources/figure-01.png"})
    harness.check("an image renders as its frame over its numbered caption, and a pointer as the figure's number",
                  "![Screenshot of the order form](resources/figure-01.png)\n\n*Figure 1. The form has two fields.*" in rendered
                  and "Figure 1 has the two fields." in rendered and "```mermaid" in rendered, rendered)
    harness.check("the rest of the document renders as it was", rendered.startswith("# Orders ship from the new warehouse")
                  and "- Orders ship from the new warehouse from March." in rendered, rendered)


def test_a_region_of_the_stored_frame_maps_to_the_recording_at_its_own_resolution():
    harness.check("a rectangle in a frame stored at half size doubles, offset by the screen box",
                  frame_region_utils.map_to_recording([100, 50, 300, 150], [40, 20, 1960, 1100], 960)
                  == [240, 120, 640, 320])
    words = [{"text": "inside", "box": [110, 60, 150, 70]}, {"text": "across", "box": [280, 60, 330, 70]},
             {"text": "outside", "box": [400, 60, 450, 70]}]
    harness.check("a word the cut's edge runs through is reported, one inside or outside is not",
                  frame_region_utils.list_crossed_words([100, 50, 300, 150], words) == ["across"])
    frame = numpy.full((540, 960, 3), 255, numpy.uint8)
    cv2.rectangle(frame, (200, 150), (600, 400), (0, 0, 0), 3)
    panels = frame_region_utils.find_panel_boxes(frame)
    harness.check("a drawn window is found as a panel", any(abs(box[0] - 200) <= 6 and abs(box[2] - 601) <= 8 for box in panels),
                  str(panels))


def _build_run(tmp: Path) -> tuple[RunPaths, Path]:
    """A run folder holding phase 4's document and provenance, two stored frames at half size, layer 2, and the
    recording they came from at 1920 x 1080."""

    video = tmp / "meeting.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5, (1920, 1080))
    full = numpy.full((1080, 1920, 3), 255, numpy.uint8)
    cv2.rectangle(full, WINDOW_RECT[:2], WINDOW_RECT[2:], (0, 0, 0), 6)
    # a mark in the bottom right corner, so a cut held in the corner shows something and is no empty area
    cv2.rectangle(full, (1870, 1040), (1915, 1075), (0, 0, 0), -1)
    for _ in range(10):
        writer.write(full)
    writer.release()
    paths = RunPaths(repository=tmp, module="meeting.mp4", output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    frames_dir = paths.output_root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for index, timestamp in enumerate((4.0, 9.0)):
        cv2.imwrite(str(frames_dir / f"frame-{index:03d}.jpg"), cv2.resize(full, (960, 540)))
        items.append({"slug": f"frame-{index:03d}", "index": index, "frameNumber": index * 2, "timestamp": timestamp,
                      "path": f"frames/frame-{index:03d}.jpg", "box": [0, 0, 1920, 1080], "screenBox": [0, 0, 1920, 1080]})
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items, "video": {}})
    merge_inputs.write_merge_inputs(paths, {
        "entries": ENTRIES, "frames": [{"index": 0, "timestamp": 4.0, "screenKind": "web application"},
                                       {"index": 1, "timestamp": 9.0, "screenKind": "slide"}]})
    paths.summary.write_text(MARKDOWN, encoding="utf-8")
    json_store_utils.write_json(paths.output_root / output_contract.DRAFT_PROVENANCE_FILE, PROVENANCE)

    return paths, video


def _build_context(paths: RunPaths,
                   video: Path) -> IllustrationContext:
    """What a conversation works in, in the chapter ORDERING_CHAPTER_ID opens."""

    items = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])["items"]

    return IllustrationContext(paths=paths, video_path=video, frames={item["slug"]: item for item in items},
                               min_cut_pixels=MIN_CUT_PIXELS,
                               chapter=ORDERING_CHAPTER_ID)


def _build_store() -> dict:
    return illustration_action_service.build_empty_store(document_block_utils.split_into_blocks(
        MARKDOWN, publication_service._build_entry_index(PROVENANCE)))


def _find_panel(store: dict,
                frame: str) -> str:
    """The id of the panel `look` found around the drawn window."""

    return next(box_id for box_id, box in store["looks"][frame]["boxes"].items()
                if box_id.startswith("P") and abs(box[0] - WINDOW_RECT[0] // 2) <= 8)


def test_a_cut_covers_the_camera_tile_phase_2_covered_defect():
    # Defect: phase 2 covered a lecturer's camera inset on the stored frame, but a cut is read from the recording, and
    # a cut of a lecture's diff showed the lecturer in its corner (Lecture 7's chapter 1, DEFECTS 582). Fix: the item
    # keeps the covered box, and the cut covers the same box of the recording before it is cropped
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    frames_file = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])
    frames_file["items"][0]["cameraTile"] = [1700, 900, 1919, 1079]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], frames_file)
    context, store = _build_context(paths, video), _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [560, 380, 960, 540]})
    cut_files = sorted((paths.output_root / "04_writing" / "images" / "cuts").glob("*.png"))
    harness.check("the cut is made", len(cut_files) == 1, str(cut_files))
    cut = cv2.imread(str(cut_files[0]), cv2.IMREAD_GRAYSCALE) if cut_files else numpy.zeros((1, 1), numpy.uint8)
    corner = cut[-60:, -80:]
    harness.check("the camera tile's corner, where the recording holds a black mark, is filled",
                  int(corner.min()) > 128, f"darkest {int(corner.min())} of the corner {corner.shape}")


def test_a_cut_named_that_was_never_made_is_refused_as_no_cut_defect():
    # A writer read a look's result label "[r10]" as a cut and looked at "cut-10"; the refusal spoke of frames only.
    # A cut name with no cut is now refused as that: no cut was made under it, a look makes none, the cuts made named
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context, store = _build_context(paths, video), _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    refused = illustration_action_service.run_action(context, store, "look", {"frame": "cut-10"})
    harness.check("the refusal says no cut has that name and a look makes none",
                  refused.is_refused and "no cut is named 'cut-10'" in refused.text and "A `look` makes no cut" in refused.text
                  and "no cut was made yet" in refused.text, refused.text)
    harness.check("an unknown frame is still refused as a frame",
                  "frames are named frame-<n>" in illustration_action_service.run_action(
                      context, store, "look", {"frame": "frame-9"}).text)


def test_every_action_does_what_was_asked_or_says_what_to_change():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    harness.check("an unknown tool is refused with the list of tools", illustration_action_service.run_action(context, store, "zoom", {}).text.startswith(
        "Refused: there is no tool 'zoom'"))
    harness.check("arguments that do not fit are refused", illustration_action_service.run_action(context, store, "look", {}).is_refused)
    # A cut before a look is answered with the look and no cut: refused, it stopped a run under failOnFrameCutFail
    early = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [0, 0, 10, 10]})
    harness.check("a cut before a look makes no cut and shows the look instead",
                  early.text.startswith("No cut was made") and early.frame_path is not None and not store["cuts"], early.text)
    # the early cut recorded its look; clear it so the replacement below comes before any look
    store["looks"].clear()
    # The same for a diagram replacing a frame not looked at: refused, it stopped a run on a chapter's first call
    replaced = illustration_action_service.run_action(context, store, "replace_with_diagram", {"frame": "frame-000", "reason": "the diagram keeps every box"})
    harness.check("a replacement before a look records nothing and shows the look instead",
                  replaced.text.startswith("Nothing was recorded") and replaced.frame_path is not None
                  and not store.get("diagramReplacements"), replaced.text)
    looked = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    harness.check("a look reports the frame's size and its boxes, and shows the frame with its grid",
                  "960 x 540 pixels" in looked.text and looked.frame_path is not None and looked.frame_path.is_file(),
                  looked.text)
    cut = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "box_ids": [_find_panel(store, "frame-000")], "margin": 10})
    cut_file = paths.output_root / store["cuts"]["cut-001"]["file"]
    piece = cv2.imread(str(cut_file))
    harness.check("the cut comes from the recording at its own resolution: twice the stored frame's pixels",
                  store["cuts"]["cut-001"]["isFromRecording"] and piece.shape[1] >= 800 and piece.shape[0] >= 500,
                  f"{cut.text} {piece.shape}")
    harness.check("and it is shown to the agent", cut.frame_path == cut_file)
    harness.check("a margin that runs off the frame is held inside it",
                  illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [950, 530, 960, 540], "margin": 5}).text
                  .startswith("cut-002"))
    harness.check("a cut with a side under the minimum comes back with a warning, and is still made",
                  f"shorter than {MIN_CUT_PIXELS}" in store["cuts"]["cut-002"]["warnings"][0], str(store["cuts"]["cut-002"]))
    harness.check("marking and masking change the cut and show it again",
                  illustration_action_service.run_action(context, store, "mark", {"cut": "cut-001", "rect": [10, 10, 100, 60], "number": 1}).frame_path == cut_file
                  and illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [200, 200, 260, 230]}).text.startswith("cut-001 masked"))
    unknown = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-099", "rect": [200, 200, 260, 230]}).text
    harness.check("a mask of a cut never made names the cuts in use and where an id comes from",
                  "there is no cut 'cut-099'" in unknown and "cut-001 (" in unknown and "`cut` it first" in unknown, unknown)
    outside = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [0, 5000, 10, 5010]})
    harness.check("a rectangle outside the cut is refused with the cut's size, and nothing is masked",
                  outside.is_refused and "cut's own pixels" in outside.text
                  and store["cuts"]["cut-001"]["masks"] == 1, outside.text)
    harness.check("a mask records the rectangle it covers",
                  store["cuts"]["cut-001"]["maskedAreas"][0]["rect"] == [200, 200, 260, 230], str(store["cuts"]["cut-001"]))
    harness.check("a frame left out is recorded with its reason",
                  illustration_action_service.run_action(context, store, "leave_out", {"frame": "frame-001", "reason": "The text states the date."}).text
                  .endswith("recorded as left out.") and store["leftOut"][0]["frame"] == "frame-001")


def _load_run_context(paths: RunPaths) -> RunContext:
    run_dir = paths.repository / "run"
    run_dir.mkdir(exist_ok=True)
    store = RunStore.create(run_dir / "run.json", RunIdentity(run_id="t", repository=str(paths.repository),
                                                              module=paths.module, output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 1}, models=[])

    return RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(), model=None)


def test_the_folders_validator_refuses_what_a_reader_would_trip_on():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    document = document_folder_service.build_document_path(paths)
    folder = document.parent
    resources = folder / output_contract.RESOURCES_DIR_NAME
    resources.mkdir(parents=True)
    cv2.imwrite(str(resources / "figure-01.png"), numpy.zeros((10, 10, 3), numpy.uint8))
    json_store_utils.write_json(folder / output_contract.DOCUMENT_PROVENANCE_FILE,
                                {"images": [{"figure": 1, "entries": [{"id": "F0.1"}]}]})
    known = [entry["id"] for entry in ENTRIES]
    for label, text, extra, code in (
            ("a linked frame that is missing", "![a](resources/figure-02.png)\n\n*Figure 1. A form.*\n", None,
             "DOCUMENT_IMAGE_MISSING"),
            ("a frame linked nowhere", "No image here.\n", None, "DOCUMENT_IMAGE_UNLINKED"),
            ("a file named after the recording", "![a](resources/figure-01.png)\n\n![b](resources/frame-3.png)\n",
             "frame-3.png", "DOCUMENT_IMAGE_NAME"),
            ("a caption that narrates the recording", "![a](resources/figure-01.png)\n\n*Figure 1. The video shows a form.*\n",
             None, "DOCUMENT_BANNED_PHRASE")):
        if extra:
            cv2.imwrite(str(resources / extra), numpy.zeros((10, 10, 3), numpy.uint8))
        document.write_text(text, encoding="utf-8")
        codes = harness.codes(document_folder_validator.validate_document_folder(paths, known))
        harness.check(f"{label} is {code}", code in codes, str(codes))
        if extra:
            (resources / extra).unlink()


def test_a_figure_naming_its_frame_but_no_note_passes_and_one_citing_an_unknown_note_is_refused_defect():
    # A run's document was refused at publishing because one figure's caption named no note, though its record names
    # the frame it was cut from. Now the frame is enough; a figure with neither, or citing a note nobody wrote, is refused
    folder = Path(tempfile.mkdtemp())
    (folder / "resources").mkdir()
    for number in (1, 2, 3):
        cv2.imwrite(str(folder / "resources" / f"figure-0{number}.png"), numpy.zeros((10, 10, 3), numpy.uint8))
    (folder / "doc.md").write_text("# Orders\n\n" + "".join(f"![Figure {n}](resources/figure-0{n}.png)\n\n" for n in (1, 2, 3)),
                                   encoding="utf-8")

    def refused(images: list[dict]) -> bool:
        (folder / "provenance.json").write_text(json.dumps({"images": images}), encoding="utf-8")
        report = document_folder_validator.validate_document_file(folder, "doc.md", ["F1.1"])
        return "DOCUMENT_CAPTION_UNSOURCED" in harness.codes(report)

    harness.check("a figure with no note but its frame passes",
                  not refused([{"figure": 1, "frame": "frame-010", "entries": []}]), "")
    harness.check("a figure with neither a note nor a frame is refused", refused([{"figure": 2, "entries": []}]), "")
    harness.check("a figure citing a note nobody wrote is refused",
                  refused([{"figure": 3, "frame": "frame-011", "entries": [{"id": "F9.9"}]}]), "")


def test_every_tool_the_agent_is_shown_has_what_carries_it_out():
    harness.check("the shipped tool table and the table of runners hold the same names",
                  set(illustration_actions.ACTIONS) == set(illustration_action_service.RUNNERS),
                  str(set(illustration_actions.ACTIONS) ^ set(illustration_action_service.RUNNERS)))


def test_a_cut_made_earlier_can_be_looked_at_so_its_mask_is_placed_in_its_own_pixels():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    shown = illustration_action_service.run_action(context, store, "look", {"frame": "cut-001"})
    width, height = cv2.imread(str(paths.output_root / store["cuts"]["cut-001"]["file"])).shape[1::-1]
    harness.check("a cut listed from an earlier answer opens with its own size, as a rejection's fix asks",
                  shown.text.startswith(f"cut-001 from frame-000: {width} x {height} pixels")
                  and shown.frame_path == paths.output_root / store["cuts"]["cut-001"]["file"], shown.text)
    harness.check("a look at a cut never records it as a frame looked at", "cut-001" not in store["looks"], store["looks"])
    with media_fixtures.word_finder(lambda path, **kwargs: [{"text": "Value", "box": [20, 20, 60, 30]}]):
        store["cuts"]["cut-001"]["boxes"] = {"C1": [10, 10, 100, 40]}
        illustration_action_service.clear_last_call(store)  # the store was edited by hand between two looks; in a run an action stands between
        named = illustration_action_service.run_action(context, store, "look", {"frame": "cut-001"}).text
    harness.check("each box on the cut is shown with the words in it, so the one holding a value can be found",
                  "C1 [10, 10, 100, 40] 'Value'" in named, named)
    words = [{"text": "Value", "box": [20, 20, 60, 30]}, {"text": "xQ9~fake_value_7abc", "box": [70, 50, 180, 62]},
             {"text": "a", "box": [0, 0, 5, 5]}, {"text": "7", "box": [190, 90, 200, 100]}]
    with media_fixtures.word_finder(lambda path, **kwargs: words):
        blind = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [5, 80, 60, 90]})
        by_words = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "xQ9~fake_value"})
        by_words_area = dict(store["cuts"]["cut-001"]["maskedAreas"][-1])
        missing = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "no such text"})
    cut_file = paths.output_root / store["cuts"]["cut-001"]["file"]
    page = numpy.full((100, 200, 3), 255, dtype=numpy.uint8)
    page[50:62, 70:180] = 60
    cv2.imwrite(str(cut_file), page)
    with media_fixtures.word_finder(lambda path, **kwargs: []):
        illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [70, 50, 180, 62]})
        on_text = store["cuts"]["cut-001"]["maskedAreas"][-1]
        illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [5, 5, 60, 20]})
        on_blank = store["cuts"]["cut-001"]["maskedAreas"][-1]
        illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [70, 50, 180, 62]})
        on_mask = store["cuts"]["cut-001"]["maskedAreas"][-1]
    harness.check("a mask records whether the area held dark text: yes over text, no over blank space or an earlier mask",
                  (on_text["coveredText"], on_blank["coveredText"], on_mask["coveredText"]) == (True, False, False),
                  str((on_text, on_blank, on_mask)))
    harness.check("a mask over no word never says the value is hidden, and names how to find it",
                  "no longer readable" not in blind.text and "words" in blind.text, blind.text)
    harness.check("a mask by the value's words covers the word that holds them, and no short word elsewhere",
                  by_words_area["words"] == "xQ9~fake_value_7abc" and by_words_area["rect"] == [68, 48, 182, 64], by_words.text)
    phrase = [{"text": "Enter", "box": [10, 70, 40, 80], "block": 1}, {"text": "client", "box": [45, 70, 80, 80], "block": 1},
              {"text": "secret", "box": [85, 70, 120, 80], "block": 1}, {"text": "client", "box": [10, 5, 40, 15], "block": 2},
              {"text": "secrets", "box": [150, 90, 190, 99], "block": 3}]
    # The earlier masks of this small cut already pass the masked-share limit; this check is about where the mask lands
    store["cuts"]["cut-001"]["maskedAreas"] = []
    with media_fixtures.word_finder(lambda path, **kwargs: phrase):
        illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "Enter a client secret"})
        by_phrase = store["cuts"]["cut-001"]["maskedAreas"][-1]
    harness.check("a phrase is masked on the one line that holds it, never where its common words recur elsewhere",
                  by_phrase["rect"] == [8, 68, 122, 82], str(by_phrase))
    # Answered with no mask made, never refused: refused, it stopped a run under failOnFrameCutFail
    harness.check("words the cut does not hold make no mask, and the words it does hold are named",
                  missing.text.startswith("No mask was made") and "xQ9~fake_value_7abc" in missing.text, missing.text)
    harness.check("and it says a cut whose value earlier masks already cover is cut again from its frame",
                  "cut frame-000 again" in missing.text, missing.text)


def test_a_frame_without_a_screen_box_is_cut_whole_and_a_failing_action_is_refused_not_raised():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    for item in context.frames.values():
        item.pop("box", None)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    cut = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    harness.check("a frame an interval or scene run took, with no box, is mapped over the whole recording",
                  cut.text.startswith("cut-001") and store["cuts"]["cut-001"]["recordingRect"][0] >= 170, cut.text)
    original = illustration_action_service.RUNNERS["look"]
    illustration_action_service.RUNNERS["look"] = lambda context, store, action: 1 / 0
    try:
        failed = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    finally:
        illustration_action_service.RUNNERS["look"] = original
    harness.check("an action that fails inside the program comes back as a refusal, and the conversation goes on",
                  failed.text.startswith("Refused: the program failed"), failed.text)


def test_a_file_the_operating_system_leaves_in_resources_does_not_fail_the_folder():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    document = document_folder_service.build_document_path(paths)
    folder = document.parent
    (folder / output_contract.RESOURCES_DIR_NAME).mkdir(parents=True)
    (folder / output_contract.RESOURCES_DIR_NAME / ".DS_Store").write_bytes(b"x")
    document.write_text("No image here.\n", encoding="utf-8")
    json_store_utils.write_json(folder / output_contract.DOCUMENT_PROVENANCE_FILE, {"images": []})
    codes = harness.codes(document_folder_validator.validate_document_folder(paths, []))
    harness.check("a hidden file is not an unlinked frame", "DOCUMENT_IMAGE_UNLINKED" not in codes, str(codes))


def _place_cut_in_the_document(paths: RunPaths,
                               video: Path) -> None:
    """Phase 4's chapter placed one frame: its cut is on disk, in the image store, and the document holds its two lines."""

    store = _build_store()
    context = _build_context(paths, video)
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    panel = _find_panel(store, "frame-000")
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "box_ids": [panel]})
    document_file_service.write_image_store(paths, {"looks": store["looks"], "cuts": store["cuts"], "nextCut": store["nextCut"]})
    cut_file = store["cuts"]["cut-001"]["file"]
    marker = "The order form needs the customer number"
    text = paths.summary.read_text(encoding="utf-8")
    paragraph = next(line for line in text.split("\n") if line.startswith(marker))
    paths.summary.write_text(text.replace(paragraph, f"{paragraph}\n\n![Screenshot of the order form]({cut_file})\n\n"
                                                      "*The order form needs only two fields.*", 1), encoding="utf-8")


def test_publishing_writes_the_folder_a_reader_opens_from_the_images_phase_4_placed():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    _place_cut_in_the_document(paths, video)
    ctx = _load_run_context(paths)
    result = publication_service.publish_document(ctx)
    harness.check("the folder passes its checks", isinstance(result, PublicationResult) and result.is_ok, result.report)
    named = [path for path in paths.output_root.iterdir() if path.is_dir() and (path / f"{path.name}.md").is_file()]
    harness.check("R13: one folder under the document's own name, its markdown named the same", len(named) == 1
                  and result.document == named[0] / f"{named[0].name}.md", str(sorted(p.name for p in paths.output_root.iterdir())))
    folder = named[0]
    markdown = result.document.read_text(encoding="utf-8")
    harness.check("the frame phase 4 placed is kept: linked relatively under its caption", result.image_count == 1
                  and "](resources/figure-01.png)" in markdown and "*Figure 1. The order form needs only two fields.*" in markdown,
                  markdown)
    harness.check("resources/ holds exactly the figures the document links",
                  sorted(path.name for path in (folder / output_contract.RESOURCES_DIR_NAME).iterdir()) == ["figure-01.png"])
    provenance = json.loads((folder / output_contract.DOCUMENT_PROVENANCE_FILE).read_text(encoding="utf-8"))
    harness.check("the provenance names the image's frame and time, and the document's own file",
                  provenance["images"][0]["frame"] == "frame-000" and provenance["images"][0]["time"] == 4.0
                  and provenance["document"] == f"{folder.name}.md", str(provenance["images"]))


def test_publishing_places_a_pdf_figure_left_unread_where_it_stood():
    """The owner: a PDF of many figures has them placed in the document where they stood, unread.
    Phase 2 lists such a figure under `placedFigures`; publishing puts it in its chapter,
    never in the executive summary or the closing, citing the paragraph beside it, and the folder passes its checks."""

    paths, _video = _build_run(Path(tempfile.mkdtemp()))
    frames_file = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])
    cv2.imwrite(str(paths.output_root / "frames" / "frame-002.jpg"), numpy.full((200, 300, 3), 120, numpy.uint8))
    frames_file[output_contract.PLACED_FIGURES_KEY] = [
        {"slug": "frame-002", "index": 2, "timestamp": 6.0, "path": "frames/frame-002.jpg", "page": 3, "box": [0, 0, 300, 200],
         "caption": "The warehouse's loading bays", "placeBefore": ""}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], frames_file)
    result = publication_service.publish_document(_load_run_context(paths))
    harness.check("the folder passes its checks", isinstance(result, PublicationResult) and result.is_ok, result.report)
    markdown = result.document.read_text(encoding="utf-8")
    ordering = markdown.index("## Ordering")
    closing = markdown.index(f"## {NON_CHAPTER_HEADINGS[2]}")
    figure = markdown.find("](resources/figure-01.png)")
    harness.check("the figure stands in its chapter, after the paragraph it followed, under its own caption",
                  ordering < figure < closing and "*Figure 1. The warehouse's loading bays*" in markdown, markdown)
    provenance = json.loads((result.document.parent / output_contract.DOCUMENT_PROVENANCE_FILE).read_text(encoding="utf-8"))
    harness.check("its provenance names its frame, its time and the paragraph's entries",
                  provenance["images"][0]["frame"] == "frame-002" and provenance["images"][0]["time"] == 6.0
                  and [entry["id"] for entry in provenance["images"][0]["entries"]] == ["F0.1", "F0.2"],
                  str(provenance["images"]))


def test_publishing_without_phase_4s_document_is_refused_by_name():
    paths, _ = _build_run(Path(tempfile.mkdtemp()))
    paths.summary.unlink()
    try:
        publication_service.publish_document(_load_run_context(paths))
    except MissingPhaseInput as exc:
        harness.check("the refusal names phase 4", "phase 4" in str(exc), str(exc))
    else:
        harness.check("a missing document is refused", False)


def test_phase_5_follows_phase_4_unless_phase_4_failed():
    harness.check("after a blocked phase 4 the run stops, and no review runs",
                  routing.route_after_finalize({"phases": {"finalize": "blocked"}}) == "done")
    harness.check("after a failed phase 4 the run stops", routing.route_after_finalize({"phases": {"finalize": "failed"}})
                  == "failed")
    harness.check("a run told to stop at phase 4 stops there",
                  routing.route_after_finalize({"phases": {"finalize": "complete"}, "last_phase": 4}) == "done")
    harness.check("`--phase 5` alone reaches the review past every phase it does not run",
                  routing.route_after_frames_preparing({"phases": {"frames_preparing": "complete"}, "first_phase": 5, "last_phase": 5})
                  == "review")


def test_the_same_cut_again_is_the_cut_already_made_and_a_frame_is_cut_at_most_three_times():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    with fx.cuts_per_frame(3):
        illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
        first = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
        again = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
        harness.check("the same rectangle again is the cut already made, no new file", first.text.startswith("cut-001")
                      and again.text.startswith("cut-001") and list(store["cuts"]) == ["cut-001"], again.text)
        for rect in ([0, 0, 300, 200], [200, 200, 500, 400]):
            illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": rect})
        refused = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [50, 50, 350, 250]})
        harness.check("a fourth cut of one frame is refused, naming the cuts to place and leave_out",
                      len(store["cuts"]) == 3 and "cut-001" in refused.text and "cut-003" in refused.text
                      and "leave_out" in refused.text, refused.text)


def test_a_cut_of_a_frame_phase_3_says_shows_a_secret_warns_to_mask_it():
    """A worker cut a frame whose notes say it shows a credential, was told "Warnings: none", and placed the cut with no
    mask (2026-09-30): the cut's reply says what the check will demand, when the cut is made."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    plain = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    harness.check("a frame whose notes name no secret: no such warning", "secret" not in plain.text, plain.text)
    context.frames["frame-000"]["record"] = {"screenshots": [{"part": "the request", "importance": "needed",
                                                              "why": "it shows the client secret"}]}
    secret = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [0, 0, 300, 200]})
    harness.check("the cut's reply says to mask every secret value on it before placing it",
                  "shows a secret" in secret.text and "`mask`" in secret.text and "every" in secret.text, secret.text)


def test_a_mask_by_words_hides_every_line_that_holds_them():
    """A token stood on a page twice, in a login command and in a header; masked by its words, only one line was hidden,
    the worker masked the same words again, and the cut was placed with the token readable (2026-10-06)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    with media_fixtures.word_finder(lambda path, **kwargs: [
        {"text": "--token=tok9~Ab3dEf6hIj9kLm2n", "box": [10, 20, 150, 30], "block": 1},
        {"text": "Bearer", "box": [10, 120, 50, 130], "block": 2},
        {"text": "tok9~Ab3dEf6hIj9kLm2n", "box": [55, 120, 180, 130], "block": 2},
        {"text": "Your", "box": [10, 60, 30, 70], "block": 3}, {"text": "token", "box": [35, 60, 60, 70], "block": 3},
        {"text": "another", "box": [10, 160, 40, 170], "block": 4}, {"text": "token", "box": [45, 160, 70, 170], "block": 4}]):
        result = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "--token=tok9~Ab3dEf6hIj9kLm2n"})
    rects = [area["rect"] for area in store["cuts"]["cut-001"].get("maskedAreas") or []]
    harness.check("both lines holding the token are masked",
                  any(rect[1] <= 20 and rect[3] >= 30 for rect in rects) and any(rect[1] <= 120 and rect[3] >= 130 for rect in rects),
                  f"{rects} {result.text}")
    harness.check("and the word 'token' on lines about tokens is never masked",
                  not any(rect[1] <= 60 <= rect[3] or rect[1] <= 160 <= rect[3] for rect in rects), f"{rects}")


def test_a_mask_s_result_says_the_box_is_the_writer_s_not_the_screen_s_defect():
    # A writer masked a typed password over text the reader could not read, then wrote that the app's field hides what is
    # typed. Now every applied mask's result says the black box is the writer's, to be described as hidden in the document
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    result = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [20, 20, 120, 40]})
    harness.check("the mask's result says the box is the writer's", "your mask, not the screen's" in result.text, result.text)


def test_a_mask_inside_an_earlier_mask_is_answered_as_hidden_already():
    """A worker masked a box an earlier mask had blacked out three times, each time told "nothing shows this mask hides a
    value" (2026-10-06): a mask over black is answered with the mask that hides it, and none is added."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [10, 10, 200, 40]})
    store["cuts"]["cut-001"]["boxes"]["C9"] = [12, 12, 198, 38]
    again = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "box_id": "C9"})
    harness.check("answered as hidden already, with no second mask",
                  "already hidden" in again.text and not again.is_refused
                  and len(store["cuts"]["cut-001"]["maskedAreas"]) == 1, again.text)


def test_a_cut_of_a_frame_with_no_readable_word_is_not_called_a_miss():
    """Told "no text was found on it ... the cut missed it", a worker undid a right cut of a dimmed slide whose whole
    frame the text reader read no word on, and cut it again, twice."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    unread = illustration_action_service.run_action(
        context, store, "cut", {"frame": "frame-000", "box_ids": [_find_panel(store, "frame-000")], "margin": 10}).text
    harness.check("a frame with no word read says the reader read none on it, and to judge the cut by looking",
                  "read no word on the whole of frame-000" in unread and "the cut missed it" not in unread, unread)


def test_a_cut_by_boxes_the_frame_does_not_have_is_answered_not_refused():
    """A worker cut one frame by the box ids of the frame it looked at last; refused, the cut stopped the run under
    failOnFrameCutFail (2026-10-06). No cut is made, and the frame's own boxes are named."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    result = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "box_ids": ["T99"]})
    harness.check("answered with no cut made, never refused, and the frame's boxes named",
                  result.text.startswith("No cut was made") and "T99" in result.text and not store["cuts"], result.text)


def test_a_cut_past_a_frames_limit_is_answered_not_refused():
    """A fourth cut of one frame was refused at the limit of three, and the refusal stopped the run under
    failOnFrameCutFail (2026-10-06). No cut is made, and the cuts to place are named."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    with fx.cuts_per_frame(3):
        illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
        for rect in ([100, 100, 400, 300], [110, 110, 410, 310], [120, 120, 420, 320]):
            illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": rect})
        made = len(store["cuts"])
        result = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [130, 130, 430, 330]})
        harness.check("answered with no cut made, never refused, the made cuts named",
                      result.text.startswith("No cut was made") and "cut-001" in result.text
                      and len(store["cuts"]) == made, result.text)


def test_a_header_label_read_with_a_curly_quote_is_a_credential():
    """A debugger listed a request's headers each in curly quotes, and '\u201cCookie:' with its value passed the cut tool and
    the check unnamed: only straight quotes were stripped (2026-10-06)."""

    words = ["\u201cCookie:", "QjljTktXijp7inMiOjF9LCJZdmRX", "View"]
    harness.check("a curly-quoted cookie label is read as a credential's label",
                  document_text_utils.is_credential_label(words[0]), "")
    harness.check("and it is named as readable", any("Cookie" in name for name in document_text_utils.find_readable_secrets(words)),
                  str(document_text_utils.find_readable_secrets(words)))
    harness.check("and with its long value it holds the cut to a mask with no note from phase 3",
                  document_text_utils.find_credential_keys(words) != [], str(document_text_utils.find_credential_keys(words)))
    harness.check("a label with a short word after it does not",
                  document_text_utils.find_credential_keys(["Cookie:", "none"]) == [], "")


def test_a_diagram_of_more_than_ten_arrows_never_replaces_its_screenshot():
    """A writer replaced a 20-arrow architecture slide with phase 3's diagram, about half of whose arrows were wrong,
    though write.md forbade it (2026-10-06): nothing is recorded, and the cut is asked for. A small diagram still may."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    arrows = "\n".join(f"    N{i} --> N{i + 1}" for i in range(12))
    context.frames["frame-000"]["record"] = {"diagram": f"flowchart LR\n{arrows}"}
    declined = illustration_action_service.run_action(context, store, "replace_with_diagram",
                                                      {"frame": "frame-000", "reason": "it keeps every box"})
    harness.check("a 12-arrow diagram is not recorded as a replacement, and the cut is asked for",
                  not store.get("diagramReplacements") and "cut" in declined.text
                  and not declined.is_refused, declined.text)
    context.frames["frame-000"]["record"] = {"diagram": "flowchart LR\n    A --> B\n    B --> C"}
    illustration_action_service.run_action(context, store, "replace_with_diagram", {"frame": "frame-000", "reason": "it keeps every box"})
    harness.check("a 2-arrow diagram is recorded", len(store.get("diagramReplacements") or []) == 1, str(store.get("diagramReplacements")))


def test_a_label_whose_value_is_masked_is_never_named_as_still_readable():
    """A header's token was masked and the reply still named its label as readable, beside the one line truly left; the
    worker masked the same words again and placed the cut with a token showing (2026-10-06)."""

    words = [{"text": "authorization:", "box": [10, 100, 50, 110]},
             {"text": "\"https://api.example.test/users\"", "box": [200, 100, 290, 110]},
             {"text": "cookie:", "box": [10, 150, 40, 160]},
             {"text": "\"https://api.example.test/items\"", "box": [200, 150, 290, 160]}]
    named = illustration_action_service._name_readable_secrets({}, words, [[55, 98, 195, 112]])
    harness.check("the label of a masked value is not named, a label with no mask after it is",
                  not any("authorization" in name for name in named) and any("cookie" in name for name in named), named)


def test_a_header_label_whose_value_is_masked_after_its_scheme_word_is_hidden():
    """'\u201cAuthorization: Bearer' with the token masked after 'Bearer' was named as a readable secret, and an accepted
    chapter was asked again (2026-10-06)."""

    words = [{"text": "\u201cAuthorization:", "box": [10, 100, 60, 110]}, {"text": "Bearer", "box": [65, 100, 95, 110]},
             {"text": "\u201chttps://api.example.test/users\u201d", "box": [300, 100, 400, 110]}]
    rects = [[100, 98, 290, 112]]
    harness.check("the label is hidden when the mask follows its scheme word",
                  frame_region_utils.is_label_value_masked(words, 0, rects, document_text_utils.is_auth_scheme), "")
    harness.check("and not without the mask",
                  not frame_region_utils.is_label_value_masked(words, 0, [], document_text_utils.is_auth_scheme), "")


def test_a_mask_says_which_secret_is_still_readable_on_the_cut():
    """Told "the value that must stay hidden is no longer readable" after one line of a code was masked, a worker placed a
    cut whose authorization header and cookie stayed readable (2026-09-30): the reply names what the reader still reads."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    context.frames["frame-000"]["record"] = {"screenshots": [{"part": "the request", "why": "it shows the token request"}]}
    with media_fixtures.word_finder(lambda path, **kwargs: [{"text": "Xy12Zz34Ww56Qq78Rr90Tt", "box": [10, 10, 60, 20], "block": 1},
                                                      {"text": "authorization:", "box": [200, 200, 260, 210], "block": 2}]):
        masked = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [0, 0, 100, 40]})
    with media_fixtures.word_finder(lambda path, **kwargs: [{"text": "content-type:", "box": [10, 10, 60, 20], "block": 1}]):
        clean = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [0, 0, 100, 40]})
    harness.check("a secret left readable is named, and the reply never says the value is hidden",
                  "still reads" in masked.text and "'authorization:'" in masked.text
                  and "no longer readable" not in masked.text, masked.text)
    harness.check("with nothing left, the reply asks to check the cut shown", "Check the cut shown" in clean.text, clean.text)


def test_a_credential_key_and_its_value_are_read_as_a_secret():
    """A configuration file's password and client secret keys were read whole, and their values passed as plain words
    (2026-10-02): the word after a key that names a credential is its value."""

    words = ['"syncUserPassword":', '"Qx%2B7kLm",', '"clientSecret":', '"0a1b2c3d-1111-2222-3333-444455556666",',
             '"clientId":', '"9f8e7d6c-1111-2222-3333-444455556666",', '"tokenUrl":', '"https://auth.acme.test/token",',
             '"apiKey":', '"{{apiKey}}"', 'client_secret:', '...,']
    found = document_text_utils.find_readable_secrets(words)
    harness.check("a password key and a secret key are named, by their keys, never their values",
                  found == ['"syncUserPassword":', '"clientSecret":'], str(found))
    placeholder = document_text_utils.find_credential_keys(['"password":', '"your-sync-password",'])
    harness.check("a value that names its own kind is a placeholder, no secret", placeholder == [], str(placeholder))
    masked = document_text_utils.find_readable_secrets(['"syncUserPassword":', '"syncUser":', '"Jonas",'])
    harness.check("a key whose value was masked, followed by the next line's key, shows no secret", masked == [], str(masked))


def test_a_cut_whose_text_shows_a_credential_warns_to_mask_it_whatever_phase_3_noted():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    context.frames["frame-000"]["record"] = {"screenshots": [{"part": "the settings file", "why": "the values each call uses"}]}
    with media_fixtures.word_finder(lambda path, **kwargs: [
        {"text": '"syncUserPassword":', "box": [10, 10, 120, 20], "block": 1, "line": 1},
        {"text": '"Qx%2B7kLm",', "box": [130, 10, 200, 20], "block": 1, "line": 1}]):
        cut = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    harness.check("the cut says to mask the secret", "`mask` every secret" in cut.text, cut.text)


def test_a_cut_that_stops_inside_a_table_says_its_rows_go_on():
    """A table cut after its third row was placed as the table (2026-10-04)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    rows = [("Criterion", "Alpha", 120), ("Volume", "<number>", 160), ("Horizon", "medium", 330)]
    with media_fixtures.word_finder(lambda path, **kwargs: [
        {"text": text, "box": [left, top, left + 80, top + 12], "block": block, "line": line}
        for line, (first, second, top) in enumerate(rows, start=1)
        for block, (text, left) in enumerate(((first, 110), (second, 250)), start=1)]):
        illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
        cut = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    harness.check("the cut names the row going on below its bottom edge",
                  "a row in its columns goes on below its bottom edge ('Horizon', 'medium')" in cut.text, cut.text)


def test_a_cut_showing_a_face_names_where_to_mask_it():
    """A presenter's floating camera, face and name, was cut with a credentials page and offered to place (2026-10-02)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    with media_fixtures.face_finder(lambda gray: [(20, 30, 80, 100)]):
        shown = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    with media_fixtures.face_finder(lambda gray: []):
        none = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [0, 0, 300, 200]})
    harness.check("a face is named with its rectangle on the cut", "finds a face on the cut at [20, 30, 80, 100]" in shown.text, shown.text)
    harness.check("a drawn icon is left as it is", "a drawn icon stays as it is" in shown.text, shown.text)
    harness.check("no face, no warning", "face" not in none.text, none.text)


def test_a_value_misread_by_a_character_is_masked_by_its_parts_and_a_masked_one_is_named_hidden():
    """A client secret the reader misread in three characters could not be masked by its words, and a value masked
    already was refused as "no word holds", so the worker believed it still showed (2026-10-02)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    with media_fixtures.word_finder(lambda path, **kwargs: [
        {"text": '"clientSecret":', "box": [10, 10, 100, 20], "block": 1},
        {"text": '"0a1b2c3d-f@ce\u20144c9b-bkes-4473a8722270",', "box": [110, 10, 290, 20], "block": 1}]):
        masked = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "0a1b2c3d-f8cc-4c9b-b8e6-6473a8722270"})
    with media_fixtures.word_finder(lambda path, **kwargs: [
            {"text": '"clientSecret":', "box": [10, 10, 100, 20], "block": 1}]):
        again = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "0a1b2c3d-f8cc-4c9b-b8e6-6473a8722270"})
    harness.check("a value misread by a character is masked by its parts", masked.text.startswith("cut-001 masked"), masked.text)
    # Answered, never refused: refused, a second mask of a hidden token stopped a run under failOnFrameCutFail (2026-10-06)
    harness.check("the same value again is answered, never refused", not again.is_refused,
                  again.text)
    harness.check("the same value again is named hidden by the earlier mask", "already hidden" in again.text
                  and str(store["cuts"]["cut-001"]["maskedAreas"][0]["rect"]) in again.text, again.text)


def test_a_mask_over_a_credential_label_alone_is_refused():
    """A worker masked the label 'cookie:' and left the cookie's value readable; with the label gone, nothing on the cut
    showed a secret any more (2026-09-30). A mask over a label without its value is refused before it is drawn."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    for label, words, is_refused in (
            ("the label alone: refused", ["cookie:"], True),
            ("the label and its scheme only: refused", [">", "authorization:", "Basic"], True),
            ("the label with its value: drawn", ["cookie:", "session=fAke0Value9"], False),
            ("a settings file's password key alone: refused", ['"syncUserPassword":'], True)):
        masks = store["cuts"]["cut-001"]["masks"]
        with media_fixtures.word_finder(lambda path, words=words, **kwargs: [
                {"text": text, "box": [10 + 30 * number, 10, 35 + 30 * number, 20], "block": 1}
                for number, text in enumerate(words)]):
            result = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [0, 0, 200, 40]})
        harness.check(label, (store["cuts"]["cut-001"]["masks"] == masks) == is_refused
                      and ("not its value" in result.text) == is_refused, result.text)
        if is_refused:
            harness.check("a refusal invites a report when the worker believes it is wrong (the owner, 2026-09-30)",
                          "`report_problem`" in result.text and "wrong_rejection" in result.text, result.text)


def test_a_mask_on_a_credential_line_runs_to_the_cut_s_edge_and_over_its_wrapped_line():
    """A worker masked a cookie's box and an authorization header's box; each value ran past its box, to the edge and onto
    a wrapped line no box held, and stayed readable (2026-09-30). A mask over a credential's label is widened by the tool."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    width = cv2.imread(str(paths.output_root / store["cuts"]["cut-001"]["file"])).shape[1]
    store["cuts"]["cut-001"]["boxes"] = {"C1": [5, 5, 200, 20], "C2": [5, 60, 200, 75]}
    with media_fixtures.word_finder(lambda path, **kwargs: [{"text": "cookie:", "box": [10, 8, 50, 18], "block": 1},
                                                      {"text": "session=fAke0Value9", "box": [55, 8, 190, 18], "block": 1}]):
        illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "box_id": "C1"})
    area = store["cuts"]["cut-001"]["maskedAreas"][-1]
    harness.check("the mask runs to the cut's right edge and down to the next box", area["rect"][2] == width
                  and area["rect"][3] == 59 and area.get("reachesEdge") is True, str(area))


def test_a_mask_on_the_line_of_an_earlier_credential_mask_or_an_authorization_scheme_is_widened_too():
    """After a credential line's first mask stopped at its box, a worker masked a word further along the same line, and
    an authorization line read only as 'Basic <value>' was masked by its box (2026-09-30): both are widened."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    width = cv2.imread(str(paths.output_root / store["cuts"]["cut-001"]["file"])).shape[1]
    store["cuts"]["cut-001"]["boxes"] = {"C1": [5, 5, 200, 20], "C2": [5, 40, 200, 55], "C3": [5, 90, 200, 100]}
    store["cuts"]["cut-001"]["maskedAreas"] = [{"rect": [5, 5, 60, 20], "words": "cookie: a=b"}]
    with media_fixtures.word_finder(lambda path, **kwargs: [{"text": "gateway=xyz;", "box": [70, 8, 150, 18], "block": 1}]):
        illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [70, 6, 150, 19]})
    with media_fixtures.word_finder(lambda path, **kwargs: [{"text": "Basic", "box": [10, 42, 40, 52], "block": 2},
                                                            {"text": "cTd4Mm16c", "box": [45, 42, 120, 52], "block": 2}]):
        illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "box_id": "C2"})
    same_line, scheme = store["cuts"]["cut-001"]["maskedAreas"][-2:]
    harness.check("a mask on the rows of a short credential mask runs to the edge", same_line["rect"][2] == width
                  and same_line.get("reachesEdge") is True, str(same_line))
    harness.check("an authorization scheme's line runs to the edge and down to the next box", scheme["rect"][2] == width
                  and scheme["rect"][3] == 89 and scheme.get("reachesEdge") is True, str(scheme))


def test_a_cookie_line_read_without_its_label_is_still_a_credential_line():
    """The reader read a cookie header as 'x-ms-slice=abc; sessioncookie=abc;' with no 'cookie:' label, and its value's
    wrapped line was left readable (2026-09-30): a name=value word naming a cookie, a session or a token counts."""

    from utils import document_text_utils
    harness.check("a cookie pair is a credential sign", document_text_utils.has_credential_label(["x-ms-slice=abc;", "sessioncookie=abc;"]))
    harness.check("an ordinary parameter is not", not document_text_utils.has_credential_label(["grant_type=authorization_code", "scope=openid"]))


def test_a_mask_by_words_finds_a_word_read_below_the_confidence_floor():
    """An e-mail address read at confidence 22 could not be masked by its words: the match used only words read at 40 or
    more (2026-09-30). With no match there, the words read at any confidence are tried."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    with media_fixtures.word_finder(lambda path, min_confidence=40.0, **kwargs: [
        {"text": "jonas@acme.example.com", "box": [10, 10, 120, 20], "block": 1}] if min_confidence == 0 else []):
        masked = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "words": "jonas@acme.example.com"})
    harness.check("the address is found and masked", store["cuts"]["cut-001"]["masks"] == 1, masked.text)


def test_a_mask_on_a_frame_that_shows_no_secret_names_no_value_left():
    """After an e-mail address was masked, the reply named an app registration's name as a secret still readable, on a
    frame phase 3 notes show no secret (2026-09-30): the list is for frames that show one."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    with media_fixtures.word_finder(lambda path, **kwargs: [{"text": "001234-AcmeSync_ServiceAuth-DEV", "box": [10, 10, 60, 20], "block": 1}]):
        masked = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [0, 30, 100, 40]})
    harness.check("no value is named", "still reads" not in masked.text, masked.text)


def test_a_name_of_digit_groups_and_words_is_no_readable_secret():
    """A cut of documentation showed an application's name as an example, "<digits>-<Word>App-DEV"; read as a secret, it
    made the worker mask two whole paragraphs of the figure (2026-09-30). A random value mixes letters and digits inside
    one part, and stays a secret."""

    from utils import document_text_utils
    harness.check("an app name joined by hyphens is no secret",
                  document_text_utils.find_readable_secrets(["(example", "010042-AcmeSyncApp-DEV)."]) == [])
    harness.check("a random token id still is",
                  document_text_utils.find_readable_secrets(["\"72210Qa19J4_Z8kPxtrwBB\","]) != [])


def test_a_guid_misread_by_the_text_reader_is_no_readable_secret():
    """The text reader read a client id's "5" as "S", and the GUID rule, hex only, called it a secret; the worker was
    told to mask the ids the figure was cut for."""

    from utils import document_text_utils
    harness.check("a GUID with a misread letter is no secret",
                  document_text_utils.find_readable_secrets(["ab12cSde-34f5-4a6b-9c0d-1e2f3a4b5c6d"]) == [])
    harness.check("a long random value still is",
                  document_text_utils.find_readable_secrets(["Zq8xWm2LkP4vRt7yUe3N"]) != [])


def test_data_names_and_variables_on_a_request_are_no_readable_secrets():
    """A cut of a request and its response showed a rate-limit header as JSON, correlation ids the reader read with "@",
    a media type, a GUID cut off with a long dash and an authorization header whose value is a variable; each was
    called a secret and the worker was told to mask them (2026-09-30)."""

    from utils import document_text_utils
    for word in ['[{"window":49,"type":"throttle",', "Id-@a1b2c3d4e5f6a7b8c9d0e1f2", "application/vnd.acme.v2+json",
                 "ab12cd34-ef56-4a7b—c8"]:
        harness.check(f"{word!r} is no secret", document_text_utils.find_readable_secrets([word]) == [])
    harness.check("a header label followed by a variable is no secret",
                  document_text_utils.find_readable_secrets(["Authorization:", "Bearer", "{{accessToken.body}}"]) == [])
    harness.check("a header label followed by a value still is",
                  document_text_utils.find_readable_secrets(["Authorization:", "Bearer", "eyJhbGciOi"]) != [])
    harness.check("a long random value still is", document_text_utils.find_readable_secrets(["Zq8xWm2LkP4vRt7yUe3N"]) != [])


def test_a_mask_hiding_a_credential_is_never_refused_for_its_size():
    """A terminal's login command held a whole token over two lines of a narrow window; its mask, widened to the edge as
    a credential's is, covered 23% of the cut and was refused four times, each refusal stopping the run (2026-10-06).
    Hiding a secret is never refused for its size; the limit is for masks over areas that hold none."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    store["cuts"]["cut-001"]["boxes"] = {"C1": [5, 5, 290, 80], "C2": [5, 150, 200, 165]}
    with media_fixtures.word_finder(lambda path, **kwargs: [
        {"text": "--token=sha999~Ab3dEf6hIj9kLm2nOp5qRs8tUvWxYz", "box": [5, 5, 290, 80], "block": 1}]):
        result = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "box_id": "C1"})
    harness.check("the credential's mask is made", not result.is_refused and
                  len(store["cuts"]["cut-001"].get("maskedAreas") or []) == 1, result.text)


def test_a_figure_mostly_hidden_by_masks_is_refused():
    """Figures masked over a quarter and more were placed and showed the reader nothing (2026-10-01): a mask past a
    fifth of the cut is refused, and the share is measured with overlaps counted once."""

    harness.check("two overlapping halves cover the whole image once",
                  frame_region_utils.compute_masked_share(10, 10, [[0, 0, 10, 5], [0, 0, 10, 10]]) == 1.0)
    harness.check("a thin row is a small share",
                  frame_region_utils.compute_masked_share(100, 100, [[0, 10, 100, 15]]) == 0.05)
    harness.check("the limit sits between the useful figures and the useless ones",
                  0.08 < frame_region_utils.MOST_MASKED_SHARE < 0.24)


def test_two_cuts_of_the_same_view_count_as_a_repeat_and_different_screens_do_not():
    """Two cuts of one view a second apart were placed as two figures (2026-10-01)."""

    page = numpy.full((90, 160, 3), 250, dtype=numpy.uint8)
    cv2.putText(page, "Refunds", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    again = page.copy()
    cv2.circle(again, (150, 80), 3, (0, 200, 0), -1)
    other = numpy.full((90, 160, 3), 250, dtype=numpy.uint8)
    cv2.putText(other, "Orders", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    harness.check("the same view with a moved pointer is a repeat",
                  frame_region_utils.compute_content_difference(page, again) < frame_region_utils.REPEATED_FIGURE_SHARE)
    harness.check("a different screen is not",
                  frame_region_utils.compute_content_difference(page, other) > frame_region_utils.REPEATED_FIGURE_SHARE)
    harness.check("images of different shapes are never compared",
                  frame_region_utils.compute_content_difference(page, page[:, :60]) is None)


def test_a_cut_of_the_whole_browser_frame_is_refused_and_a_slide_is_not():
    """A whole browser frame (tabs, bookmarks, side panel) was placed for a flowchart that filled a third of it
    (2026-10-01)."""

    from services import illustration_action_service as service
    browser = {"record": {"screen_kind": "web application", "screenshots": [{"part": "the onboarding flowchart"}]}}
    reason = service._explain_whole_frame_cut(browser, [0, 0, 960, 540], 960, 540)
    harness.check("the whole browser frame is refused, naming the part", "the onboarding flowchart" in reason, reason)
    harness.check("a part of it is not", service._explain_whole_frame_cut(browser, [300, 200, 960, 540], 960, 540) == "")
    slide = {"record": {"screen_kind": "slide", "screenshots": [{"part": "the slide"}]}}
    harness.check("a slide is cut whole", service._explain_whole_frame_cut(slide, [0, 0, 960, 540], 960, 540) == "")
    # A PDF's figure frame is the figure, cut from its page already: NIST's Figure 1, a diagram filling it, was refused
    # whole twice, and the cut that passed lost the diagram's title box and its last box
    figure = {"medium": "pdf-figure", "record": {"screen_kind": "PDF figure",
                                                 "screenshots": [{"part": "the three-column structural diagram"}]}}
    harness.check("a PDF's figure frame is cut whole", service._explain_whole_frame_cut(figure, [0, 0, 960, 540], 960, 540) == "")


def test_a_cut_through_a_line_of_text_is_refused_with_the_rectangle_that_keeps_it():
    """A slide's decision line lost its last words: the edge crossed one word, and the words past it were wholly
    outside, so nothing named them (2026-10-01)."""

    words = [{"text": "One", "box": [600, 100, 640, 120], "block": 7}, {"text": "service", "box": [650, 100, 720, 120], "block": 7},
             {"text": "per", "box": [730, 100, 760, 120], "block": 7}, {"text": "gateway", "box": [770, 100, 850, 120], "block": 7},
             {"text": "Title", "box": [20, 10, 120, 40], "block": 1}]
    grown = frame_region_utils.expand_to_whole_blocks([10, 5, 750, 300], words, 960, 540)
    harness.check("the rectangle grows to the end of the crossed line", grown == [10, 5, 853, 300], str(grown))
    harness.check("a rectangle crossing no word stays as it is",
                  frame_region_utils.expand_to_whole_blocks([10, 5, 900, 300], words, 960, 540) == [10, 5, 900, 300])
    # one paragraph over the whole editor, three lines: crossing its second line takes in that line only
    editor = [{"text": "alpha", "box": [20, 100, 900, 115], "block": 9, "line": 9001},
              {"text": "beta", "box": [20, 200, 400, 215], "block": 9, "line": 9002},
              {"text": "gamma", "box": [420, 200, 950, 215], "block": 9, "line": 9002},
              {"text": "delta", "box": [20, 500, 940, 515], "block": 9, "line": 9003}]
    grown = frame_region_utils.expand_to_whole_blocks([300, 150, 600, 300], editor, 960, 540)
    harness.check("an edge through one line of a big paragraph grows to that line, never the whole paragraph",
                  grown == [17, 150, 953, 300], str(grown))
    # one line of the reader's runs across two panes: a file list on the left, the editor on the right, a wide gap between
    panes = [{"text": "orders.rest", "box": [10, 100, 120, 112], "block": 3, "line": 3001},
             {"text": '"region":', "box": [330, 100, 400, 112], "block": 3, "line": 3001},
             {"text": '"eu",', "box": [408, 100, 450, 112], "block": 3, "line": 3001}]
    grown = frame_region_utils.expand_to_whole_blocks([345, 80, 900, 300], panes, 960, 540)
    harness.check("a line across two panes grows only through the words next to the crossed one, never into the other pane",
                  grown == [327, 80, 900, 300], str(grown))


def test_an_edge_through_text_is_moved_to_keep_the_line_whole():
    """An edge through a line was refused with the rectangle that keeps it whole; a worker guessing edges from the grid
    was refused twice on one slide, and failOnFrameCutFail stopped the run each time (2026-10-04). The cut is made
    with the edges moved, and says so."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    store["looks"]["frame-000"]["words"] = [
        {"text": f"word{row}", "box": [100, 100 + row * 20, 500, 112 + row * 20], "block": 1, "line": row}
        for row in range(10)]
    taken = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [150, 125, 450, 165], "margin": 12})
    harness.check("the cut is made, not refused", taken.text.startswith("cut-001"), taken.text)
    harness.check("and says its edges were moved to keep the lines whole", "its edges were moved from" in taken.text, taken.text)
    rect = store["cuts"]["cut-001"]["lookRect"]
    harness.check("the cut holds the crossed lines whole", rect[0] <= 100 and rect[2] >= 500, str(rect))


def test_a_placed_cut_whose_edge_runs_through_text_is_rejected():
    """A cut made before `cut` refused such an edge was offered to place again (2026-10-01)."""

    paths = fx.make_paths()
    words = [{"text": "One", "box": [600, 100, 640, 120], "block": 7}, {"text": "gateway", "box": [770, 100, 850, 120], "block": 7}]
    document_file_service.write_image_store(paths, {
        "looks": {"frame-017": {"width": 960, "height": 540, "words": words}},
        "cuts": {"cut-001": {"frame": "frame-017", "lookRect": [10, 5, 800, 300]},
                 "cut-002": {"frame": "frame-017", "lookRect": [10, 5, 900, 300]}}})
    for cut, is_rejected in (("cut-001", True), ("cut-002", False)):
        report = ValidationReport(subject="heading-001-1")
        document_validator._check_text_cut_off(paths, "heading-001-1", {"images": [{"cut": cut}]}, report)
        codes = harness.codes(report)
        harness.check(f"{cut} rejected: {is_rejected}", ("DOCUMENT_IMAGE_TEXT_CUT_OFF" in codes) == is_rejected, str(codes))


def test_a_placed_cut_whose_edge_crosses_marks_with_no_word_is_named_and_never_refused_defect():
    # A chapter part spent its 40 calls cutting and undoing one dense form, every edge it tried crossing 10 to 39 marks
    # (DEFECTS 705). Now marks an edge crosses are a console note; an edge through a line of words is still refused
    paths = fx.make_paths()
    frame = numpy.full((540, 960, 3), 255, numpy.uint8)
    for row in range(12):
        cv2.line(frame, (100, 60 + 35 * row), (700, 60 + 35 * row), (0, 0, 0), 3)
    (paths.output_root / "frames").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(paths.output_root / "frames" / "frame-017.jpg"), frame)
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {
        "items": [{"slug": "frame-017", "index": 17, "path": "frames/frame-017.jpg"}], "video": {}})
    document_file_service.write_image_store(paths, {
        "looks": {"frame-017": {"width": 960, "height": 540, "words": []}},
        "cuts": {"cut-001": {"frame": "frame-017", "lookRect": [300, 20, 800, 500]},
                 "cut-002": {"frame": "frame-017", "lookRect": [50, 20, 800, 500]}}})
    for cut, is_named in (("cut-001", True), ("cut-002", False)):
        report = ValidationReport(subject="heading-001-1")
        document_validator._check_text_cut_off(paths, "heading-001-1", {"images": [{"cut": cut}]}, report)
        noted = [finding.code for finding in report.notes]
        harness.check(f"{cut} not refused", "DOCUMENT_IMAGE_TEXT_CUT_OFF" not in harness.codes(report), str(report.findings))
        harness.check(f"{cut} named on the console: {is_named}", ("DOCUMENT_IMAGE_TEXT_CUT_OFF" in noted) == is_named, str(noted))


def test_an_edge_along_a_flat_margin_of_another_tone_crosses_no_marks_defect():
    # A right cut of a blue terminal screen was warned its bottom edge ran through 16 marks: the edge lay on the slide's
    # dark margin, whose noise crossed the bar set from the cut's own median (DEFECTS 369). A mark now differs from its
    # line's own background too, and an edge through lines of text still crosses them
    from utils import frame_region_utils

    noise = numpy.random.default_rng(7).integers(-6, 7, size=(540, 960))
    frame = (numpy.full((540, 960), 20) + noise).clip(0, 255).astype(numpy.uint8)
    frame[0:400, 100:800] = 85
    for row in range(12):
        frame[40 + 30 * row:46 + 30 * row, 150:750] = 230
    harness.check("an edge on the flat margin crosses no marks",
                  frame_region_utils.list_marked_edges(frame, [90, 0, 810, 420]) == [],
                  str(frame_region_utils.list_marked_edges(frame, [90, 0, 810, 420])))
    harness.check("an edge through the lines still crosses them",
                  [side for side, _ in frame_region_utils.list_marked_edges(frame, [300, 0, 810, 420])] == ["left"],
                  str(frame_region_utils.list_marked_edges(frame, [300, 0, 810, 420])))


def test_a_tall_text_block_is_listed_line_by_line_and_a_line_is_split_at_a_wide_gap():
    """On an editor the reader made one block of the whole file, and the worker had no box to cut a few lines by: it
    guessed rectangles a dozen times (2026-10-02)."""

    tall = [{"text": f"line{row}", "box": [300, 100 + 20 * row, 500, 112 + 20 * row], "block": 9, "line": 9000 + row}
            for row in range(10)]
    tall.append({"text": "orders.rest", "box": [10, 100, 120, 112], "block": 9, "line": 9000})
    short = [{"text": f"title{row}", "box": [600, 20 + 20 * row, 700, 32 + 20 * row], "block": 2, "line": 2000 + row}
             for row in range(3)]
    blocks = text_detection_service.group_text_blocks(tall + short)
    boxes = [block["box"] for block in blocks]
    harness.check("a block of ten lines is ten line boxes, the short block one box",
                  len(blocks) == 12 and [600, 20, 700, 72] in boxes, str(boxes))
    harness.check("a line across a wide gap is two boxes, never one over both panes",
                  [10, 100, 120, 112] in boxes and [300, 100, 500, 112] in boxes, str(boxes))
    # 2026-10-02: an editor's grey hint inside a line ("selector:") left a gap of about four heights, and each line was
    # cut in two at it
    hinted = [{"text": f"part{row}", "box": [300, 100 + 20 * row, 380, 112 + 20 * row], "block": 4, "line": 4000 + row}
              for row in range(8)] + [{"text": f"rest{row}", "box": [430, 100 + 20 * row, 600, 112 + 20 * row], "block": 4,
                                       "line": 4000 + row} for row in range(8)]
    lines = [block["box"] for block in text_detection_service.group_text_blocks(hinted)]
    harness.check("a gap of four heights inside a line keeps the line whole", [300, 100, 600, 112] in lines, str(lines))


def test_a_note_naming_credentials_as_code_or_a_flow_is_weak_and_one_naming_a_secret_is_strong():
    """A cut of code that fills in credentials, and showed none, was held to a mask it could not have: phase 3 wrote
    "filling credentials" (2026-10-02). "Credential" names a flow, a section or code as often as a value."""

    for note, is_strong in (("the code shows the implementation of filling credentials", False),
                            ("the exact syntax used for the client credentials flow", False),
                            ("the new secret row shows the generated secret", True),
                            ("the password field with the value typed", True),
                            # 2026-10-03: an empty sign-in form was held to a mask over nothing
                            ("shows the login fields and password change prompt", False),
                            ("the password rules of the old terminal", False),
                            ("the password field and, beside it, the generated password", True)):
        harness.check(f"{note!r}: strong {is_strong}", document_text_utils.is_secret_named_strongly(note) == is_strong)
        harness.check(f"{note!r}: still a sign of a secret", document_text_utils.is_secret_shown(note))


def test_an_empty_cut_is_refused_when_made_and_never_counts():
    """A cut of empty editor space was made, counted toward the frame's three cuts and placed (2026-10-02)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    empty = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [20, 20, 160, 120]})
    harness.check("a cut of an empty area is refused and made no cut", empty.is_refused
                  and "empty" in empty.text and not store["cuts"], empty.text)


def test_an_undone_cut_frees_its_place_and_undos_are_capped():
    """A frame's three cuts were spent on an empty one and a half-cut one, and nothing could undo them (2026-10-02).
    `undo_cut` is the one way a wrong cut is set aside, capped by `budgets.undosPerFrame`."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    with fx.cuts_per_frame(3):
        illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
        rects = ([100, 100, 400, 300], [150, 120, 420, 320], [180, 130, 450, 330], [190, 140, 460, 340],
                 [195, 145, 470, 350])
        for rect in rects[:3]:
            illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": rect})
        harness.check("three cuts made", list(store["cuts"]) == ["cut-001", "cut-002", "cut-003"], str(list(store["cuts"])))
        harness.check("no fourth cut is made while three are in use",
                      illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": rects[3]}).text.startswith("No cut was made")
                      and len(store["cuts"]) == 3)
        undone = illustration_action_service.run_action(context, store, "undo_cut", {})
        harness.check("the last cut is undone", store["cuts"]["cut-003"].get("discarded") == output_contract.UNDO_DISCARD_REASON
                      and "cut-003 undone" in undone.text, undone.text)
        fourth = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": rects[3]})
        harness.check("an undone cut frees its place: a fourth cut is made", fourth.text.startswith("cut-004"), fourth.text)
        with fx.undos_per_frame(1):
            harness.check("an undo past `undosPerFrame` is refused",
                          illustration_action_service.run_action(context, store, "undo_cut", {}).is_refused)
        document_file_service.write_image_store(paths, store)
        document_file_service.write_section(paths, "heading-001-1", 'Text. [F1.1]\n\n![A figure](cut-003 "A caption")\n[F1.1]\n')
        codes = harness.codes(document_validator.validate_section_images(paths, "heading-001-1"))
        harness.check("an undone cut is never placed", "DOCUMENT_IMAGE_INVALID" in codes, str(codes))


def test_undo_cut_sets_aside_the_last_cut_made():
    """The owner asked for an undo of the last cut (2026-10-02): it sets the newest cut aside like a discard, so the
    frame may be cut again, and it counts toward the frame's cuts in all like any cut."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    harness.check("with no cut made there is nothing to undo", illustration_action_service.run_action(context, store, "undo_cut", {}).is_refused)
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [150, 120, 420, 320]})
    undone = illustration_action_service.run_action(context, store, "undo_cut", {})
    harness.check("undo sets aside the newest cut only", store["cuts"]["cut-002"].get("discarded")
                  and not store["cuts"]["cut-001"].get("discarded") and "cut-002" in undone.text, undone.text)
    again = illustration_action_service.run_action(context, store, "undo_cut", {})
    harness.check("a second undo sets aside the cut before it", store["cuts"]["cut-001"].get("discarded"), again.text)
    # the owner (2026-10-02): an undone cut is taken back from the budget, so cutting and undoing never reaches the limit
    cut_and_undone = 8
    with fx.cuts_per_frame(3), fx.undos_per_frame(UNDO_LIMIT):
        before = len(store["cuts"])
        made = []
        for step in range(1, cut_and_undone + 1):
            made.append(illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100 + step, 100, 400 + step, 300]}).text)
            illustration_action_service.run_action(context, store, "undo_cut", {})
        harness.check(f"{cut_and_undone} cuts each undone are all new cuts: an undone cut never counts toward the frame's cuts",
                      len(store["cuts"]) == before + cut_and_undone, str([text[:40] for text in made]))
        # the owner (2026-10-02): undos have a budget per frame
        for step in range(cut_and_undone + 1, cut_and_undone + UNDO_LIMIT + 2):
            illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100 + step, 100, 400 + step, 300]})
            last = illustration_action_service.run_action(context, store, "undo_cut", {})
            if last.is_refused:
                break
    undone = sum(1 for cut in store["cuts"].values() if cut.get("discarded") == output_contract.UNDO_DISCARD_REASON)
    harness.check("the undo past `undosPerFrame` is refused", undone == UNDO_LIMIT and f"undone {UNDO_LIMIT} times" in last.text,
                  f"{undone} {last.text[:120]}")


def test_look_draws_as_many_grid_lines_as_asked_on_a_frame_or_a_cut():
    """The owner (2026-10-02): the agent sets the number of grid lines, on a frame and on a cut it works on. Code lines
    stand about 14 pixels apart, and a line every 100 pixels could not place an edge between two of them."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    looked = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000", "grid_lines": 9})
    harness.check("a frame looked at with 9 grid lines says so and the spacing", "9 grid lines across each side" in looked.text
                  and "every 96 pixels across and 54 down" in looked.text, looked.text[:160])
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    cut_file = paths.output_root / store["cuts"]["cut-001"]["file"]
    before = cv2.imread(str(cut_file)).copy()
    shown = illustration_action_service.run_action(context, store, "look", {"frame": "cut-001", "grid_lines": 4})
    harness.check("a cut looked at with a grid is shown from a copy, with the grid's spacing in the cut's pixels",
                  shown.frame_path != cut_file and shown.frame_path.is_file() and "4 grid lines across each side" in shown.text,
                  shown.text[:200])
    harness.check("the cut itself is never drawn on", numpy.array_equal(cv2.imread(str(cut_file)), before))
    plain = illustration_action_service.run_action(context, store, "look", {"frame": "cut-001"})
    harness.check("without grid_lines a cut is shown as it is", plain.frame_path == cut_file, str(plain.frame_path))
    harness.check("more than 40 lines is refused", illustration_action_service.run_action(context, store, "look", {"frame": "frame-000", "grid_lines": 41})
                  .is_refused)


def test_a_chart_beside_a_closed_panel_gets_a_figure_box_of_its_own():
    """A slide held three closed text panels on the left and a bar chart and a group of shapes on the right, split by
    dashed rules; look gave the chart no box, and the worker cut the text panel for "the bar chart" (2026-10-04)."""

    frame = numpy.full((540, 960, 3), 255, numpy.uint8)
    cv2.rectangle(frame, (40, 40), (420, 220), (180, 120, 60), -1)                 # a closed, filled text panel
    for x in range(30, 930, 24):                                                    # a dashed rule across the slide
        cv2.line(frame, (x, 260), (x + 12, 260), (60, 60, 60), 1)
    for number, height in enumerate((180, 140, 110, 80, 50)):                       # an unframed bar chart
        x = 540 + number * 60
        cv2.rectangle(frame, (x, 500 - height), (x + 36, 500), (200, 140, 90), -1)
    panels = frame_region_utils.find_panel_boxes(frame)
    figures = frame_region_utils.find_figure_boxes(frame, panels, [])
    harness.check("the panel is found as a panel", any(abs(box[0] - 40) <= 6 and abs(box[1] - 40) <= 6 for box in panels),
                  str(panels))
    harness.check("the chart is one figure box, apart from the panel and the rule",
                  len(figures) == 1 and figures[0][0] >= 530 and figures[0][1] >= 300 and figures[0][2] <= 840,
                  str(figures))
    harness.check("a frame of thin lines and no solid shape has no figure",
                  frame_region_utils.find_figure_boxes(cv2.rectangle(numpy.full((540, 960, 3), 255, numpy.uint8),
                                                                     (500, 300), (900, 500), (0, 0, 0), 1),
                                                       [], []) == [])


def test_text_beside_a_cut_on_its_rows_is_named_and_text_above_it_is_not():
    """A table's first two columns were cut by a box, its last column left out, and the cut placed (2026-10-04)."""

    blocks = [{"text": "Deliverables", "box": [930, 140, 1030, 156]},       # the next column's heading, on the cut's rows
              {"text": "Status report", "box": [930, 200, 1100, 220]},
              {"text": "What was done", "box": [60, 80, 500, 110]},          # the slide's title, above the cut
              {"text": "Work packages", "box": [100, 140, 220, 156]}]        # inside the cut
    named = frame_region_utils.list_lined_up_blocks([60, 132, 900, 570], blocks)
    harness.check("the column beside the cut is named with its side, the title above and the text inside are not",
                  [(side, block["text"]) for side, block in named] == [("right", "Deliverables"), ("right", "Status report")],
                  str(named))


def test_a_row_in_the_cut_s_columns_just_past_its_edge_is_named_and_a_title_is_not():
    """A cut of a table stopped after its third row, through a cell's second line, and was placed as the table: rows of
    text in the cut's own columns continued below its bottom edge (2026-10-04)."""

    def word(text: str, block: int, line: int, box: list[int]) -> dict:
        return {"text": text, "block": block, "line": line, "box": box}

    words = [word("Criterion", 1, 1, [100, 140, 190, 156]), word("Alpha", 2, 1, [300, 140, 360, 156]),
             word("Beta", 3, 1, [500, 140, 550, 156]),                               # the header row, inside the cut
             word("Volume", 1, 2, [100, 180, 170, 196]), word("<number>", 2, 2, [300, 180, 380, 196]),
             word("<number>", 3, 2, [500, 180, 580, 196]),                           # a row inside the cut
             word("Horizon", 1, 3, [100, 230, 180, 246]), word("medium", 2, 3, [300, 230, 370, 246]),
             word("medium", 3, 3, [500, 230, 570, 246]),                             # the next row, below the cut
             word("Overview", 9, 1, [100, 60, 260, 90])]                             # the slide's title, above the cut
    continued = frame_region_utils.list_continued_rows([90, 130, 700, 205], words)
    harness.check("the row below in the cut's columns is named, the title above is not",
                  {side: [line["text"] for line in lines] for side, lines in continued.items()}
                  == {"below": ["Horizon", "medium", "medium"]}, str(continued))
    harness.check("a cut holding the whole table names nothing",
                  frame_region_utils.list_continued_rows([90, 130, 700, 260], words) == {}, "")


def test_a_blank_panel_is_never_offered_to_cut():
    """A frame kept a strip of the meeting tool's black stage at its edge; `look` listed it as panel P1 and the worker
    cut it twice, for two frames, as the slide: a box with nothing on it is never offered."""

    frame = numpy.full((360, 640, 3), 245, dtype=numpy.uint8)
    frame[2:357, 2:80] = 15                                                   # the black strip
    cv2.putText(frame, "while x < 5:", (120, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 2)
    kept = frame_region_utils.drop_blank_boxes(frame, [[2, 2, 80, 357], [100, 100, 500, 200]])
    harness.check("the blank strip goes, the box with text stays", kept == [[100, 100, 500, 200]], str(kept))


def test_a_line_the_cut_s_side_edge_runs_through_is_named():
    """A cut's left edge ran through a code line ("else:" shown as "e:") and the tool warned of nothing; the cut was
    placed with the code broken at its edge."""

    def word(text: str, block: int, line: int, box: list[int]) -> dict:
        return {"text": text, "block": block, "line": line, "box": box}

    words = [word("else:", 1, 1, [110, 200, 160, 214]),                      # crosses the left edge at 138
             word("<set", 1, 2, [140, 220, 180, 234]), word("background>", 1, 2, [185, 220, 300, 234]),  # inside
             word("Overview", 9, 1, [100, 60, 260, 90])]                       # above the cut's band: never named
    crossed = frame_region_utils.list_lines_cut_at_sides([138, 180, 560, 340], words)
    harness.check("the line through the left edge is named with its side",
                  [(side, line["text"]) for side, line in crossed] == [("left", "else:")], str(crossed))
    harness.check("a cut holding every line whole names nothing",
                  frame_region_utils.list_lines_cut_at_sides([100, 180, 560, 340], words) == [], "")


def test_an_edge_between_a_sidebar_and_the_content_beside_it_cuts_no_line_defect():
    # A cut's left edge stood between a sidebar's entries and the content row beside each, read as one line, and the
    # chapter refused every cut of the frame; an edge in a gap wider than a pane gap now cuts no line
    def word(text: str, block: int, line: int, box: list[int]) -> dict:
        return {"text": text, "block": block, "line": line, "box": box}

    words = [word("Layout", 1, 1, [20, 200, 80, 212]), word("Borrow", 1, 1, [420, 200, 490, 212]),  # one line, a pane apart
             word("else:", 2, 1, [110, 260, 160, 272]), word("return", 2, 1, [170, 260, 230, 272])]  # a code line
    crossed = frame_region_utils.list_lines_cut_at_sides([300, 180, 700, 340], words)
    harness.check("an edge in the gap between two panes cuts no line", crossed == [], str(crossed))
    crossed = frame_region_utils.list_lines_cut_at_sides([165, 180, 700, 340], words)
    harness.check("an edge between two words of one code line still cuts it",
                  [(side, line["text"]) for side, line in crossed] == [("left", "else: return")], str(crossed))


def test_two_tables_on_one_template_with_other_words_are_not_a_repeat():
    """Two slides of one deck, the same three-row table layout with other words in every cell, were refused as one
    figure placed twice: compared 256 pixels wide, the text blurred away and only the layout was left (2026-10-04)."""

    def draw_table(words: list[str]) -> numpy.ndarray:
        table = numpy.full((580, 1530, 3), 255, numpy.uint8)
        for row, word in enumerate(words):
            top = 60 + row * 175
            cv2.rectangle(table, (80, top), (390, top + 150), (225, 225, 225), -1)
            cv2.rectangle(table, (1160, top), (1530, top + 150), (200, 140, 100), -1)
            for line in range(3):
                cv2.putText(table, f"{word} step {line + 1}", (430, top + 40 + line * 45), cv2.FONT_HERSHEY_SIMPLEX,
                            1.0, (30, 30, 30), 2)
        return table

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    tables = {"cut-001": draw_table(["Refund intake", "Refund review", "Refund payout"]),
              "cut-002": draw_table(["Invoice export", "Ledger sync", "Audit archive"])}
    tables["cut-003"] = tables["cut-001"].copy()
    for cut_id, image in tables.items():
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    cuts = {cut_id: {"file": f"04_writing/images/cuts/{cut_id}.png"} for cut_id in tables}
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the cuts' words", True)
        return
    harness.check("two tables with other words are not read alike",
                  not document_validator._is_same_wording(paths, cuts, "cut-001", "cut-002"))
    harness.check("one table cut twice is read alike", document_validator._is_same_wording(paths, cuts, "cut-001", "cut-003"))


def test_the_same_look_twice_in_a_row_points_back_to_the_first():
    """A worker re-planning each turn from its first step looked at one frame five times running and never cut it
    (2026-10-04)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    first = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    again = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    harness.check("the first look shows the frame", first.frame_path is not None, first.text)
    harness.check("the same look right after points back and shows no frame",
                  again.frame_path is None and "your last call looked at" in again.text, again.text)
    gridded = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000", "grid_lines": 10})
    harness.check("a look with other arguments is carried out", gridded.frame_path is not None, gridded.text)


def test_a_rectangle_a_few_pixels_off_a_cut_made_hands_that_cut_back():
    """A cut 2 pixels off an earlier one of the same frame was made anew (2026-10-04)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    store["looks"]["frame-000"]["words"] = []
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300], "margin": 0})
    again = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [102, 98, 401, 302], "margin": 0})
    harness.check("the near rectangle hands back cut-001", again.text.startswith("cut-001") and "cut already" in again.text,
                  again.text)
    other = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 420, 300], "margin": 0})
    harness.check("a rectangle further off is a new cut", other.text.startswith("cut-002"), other.text)


def test_a_light_highlighted_bar_is_one_panel_with_its_label():
    """A highlighted row 15 grey levels off white was outlined from its middle on, and its label fell outside the cut
    (2026-10-04)."""

    frame = numpy.full((723, 1280, 3), 253, numpy.uint8)
    cv2.rectangle(frame, (82, 360), (1078, 432), (238, 238, 238), -1)
    cv2.putText(frame, "Refund history", (110, 405), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (30, 30, 30), 2)
    cv2.circle(frame, (330, 410), 9, (20, 20, 20), 2)                     # a cursor that breaks the faint outline
    panels = frame_region_utils.find_panel_boxes(frame)
    harness.check("the whole bar, its label included, is one panel",
                  any(box[0] <= 90 and box[2] >= 1070 and box[1] <= 365 and box[3] >= 428 for box in panels), str(panels))


def test_a_fragment_of_a_placed_table_is_a_repeat_and_a_cut_adding_rows_is_not():
    """A table's first rows, cut from a later frame, were placed beside the whole table already placed (2026-10-04)."""

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    rows = ["Refund limit daily", "Refund owner finance", "Ledger export nightly", "Invoice archive yearly",
            "Payment holds weekly", "Dispute escalation days", "Customer records hourly", "Audit trail monthly"]

    def draw(lines: list[str], width: int) -> numpy.ndarray:
        image = numpy.full((60 + 55 * len(lines), width, 3), 255, numpy.uint8)
        for number, line in enumerate(lines):
            cv2.putText(image, line, (20, 50 + number * 55), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
        return image

    for cut_id, image in (("cut-001", draw(rows, 700)), ("cut-002", draw(rows[:4], 900)),
                          ("cut-003", draw(rows[:3] + ["Tax filing quarterly", "Vendor review yearly", "Budget plan annual"], 900))):
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    cuts = {cut_id: {"file": f"04_writing/images/cuts/{cut_id}.png"} for cut_id in ("cut-001", "cut-002", "cut-003")}
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the cuts' words", True)
        return
    harness.check("the table's first rows are a fragment of the whole", document_validator._is_part_of(paths, cuts, "cut-002", "cut-001"))
    harness.check("a cut that adds rows of its own is not", not document_validator._is_part_of(paths, cuts, "cut-003", "cut-001"))
    # One table cut from two frames scrolled a row apart: the same size, so only their pixels were compared, which the
    # scroll moved; both were placed, and the frame left out as shown by the other was refused
    scrolled = rows + ["Bank fees monthly"]
    for cut_id, image in (("cut-004", draw(scrolled[:8], 700)), ("cut-005", draw(scrolled[1:], 700))):
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    cuts.update({cut_id: {"file": f"04_writing/images/cuts/{cut_id}.png"} for cut_id in ("cut-004", "cut-005")})
    harness.check("one table scrolled a row apart, cut at the same size, is a repeat",
                  document_validator._compare_cuts(paths, cuts, "cut-005", "cut-004") is not None)
    # The worker followed the first of two mirrored findings and left out the whole table, keeping its fragment
    # (2026-10-04): both findings name the fragment to leave out and the whole to keep
    for checked, earlier in (("cut-001", "cut-002"), ("cut-002", "cut-001")):
        fix = document_validator._render_repeat_fix(paths, cuts, checked, earlier, is_part=True)
        harness.check(f"checked {checked}: the fragment cut-002 is left out, the whole cut-001 kept",
                      fix.startswith("cut-002 is part of cut-001: keep cut-001 and `leave_out` the frame of cut-002"), fix)
    # The earlier cut stood in a section written before, which this answer cannot change; the fix told the worker to
    # leave out that earlier frame (2026-10-05)
    fix = document_validator._render_repeat_fix(paths, cuts, "cut-001", "cut-002", is_part=True, is_earlier_kept=True)
    harness.check("an earlier cut in a written section stays: the cut of this answer is the one to leave out",
                  "`leave_out` the frame of cut-001" in fix and "cut-002 is placed already" in fix, fix)


def test_a_faint_outlined_card_is_a_panel():
    """App cards outlined 20 grey levels off white had no panel box, and a cut's edge ran through one unseen
    (2026-10-04)."""

    frame = numpy.full((772, 1156, 3), 253, numpy.uint8)
    cv2.rectangle(frame, (263, 502), (565, 688), (233, 233, 233), 1)
    cv2.putText(frame, "Checklist", (290, 540), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 2)
    panels = frame_region_utils.find_panel_boxes(frame)
    harness.check("the faint card is one panel",
                  any(abs(box[0] - 263) <= 6 and abs(box[2] - 565) <= 6 and abs(box[1] - 502) <= 6
                      and abs(box[3] - 688) <= 6 for box in panels), str(panels))


def test_a_cut_edge_through_a_panel_names_it_and_a_panel_holding_the_cut_is_not_named():
    """A cut's right edge, moved to keep a line whole, ran through a card beside it and half the card was placed
    (2026-10-04)."""

    panels = {"P1": [593, 498, 899, 691], "P2": [262, 499, 567, 690], "P3": [100, 100, 1000, 760]}
    harness.check("the card the right edge runs through is named, its border-only bottom is not",
                  frame_region_utils.list_cut_panels([250, 134, 814, 682], panels) == [("right", "P1")],
                  str(frame_region_utils.list_cut_panels([250, 134, 814, 682], panels)))
    harness.check("a cut taking every card whole names none",
                  frame_region_utils.list_cut_panels([240, 120, 910, 700], panels) == [], "")


def test_a_panel_is_named_as_cut_through_only_when_its_left_out_part_holds_text():
    """An editor's empty space beside a margin guide was read as a panel, and every cut of two code frames was told its
    edge ran through it; the worker cut the frames again and again (2026-10-06)."""

    words = [{"text": "Total", "box": [960, 300, 1000, 312]}, {"text": "order", "box": [500, 300, 540, 312]}]
    harness.check("a panel whose part past the edge holds a word is named",
                  frame_region_utils.holds_text_outside([950, 277, 1249, 431], [350, 250, 950, 400], words), "")
    harness.check("a panel whose part past the edge holds no word is not",
                  not frame_region_utils.holds_text_outside([950, 277, 1249, 431], [350, 250, 950, 400], words[1:]), "")


def test_after_a_frame_tool_each_needed_frame_says_where_it_stands():
    """A worker tracking ten needed frames from memory dropped a cut's image line from its answer and never called a
    `leave_out` it had planned (2026-10-04)."""

    paths = fx.make_paths()
    sections = [{"slug": "heading-002-1", "neededFrames": ["frame-004", "frame-005"]},
                {"slug": "heading-003-1", "neededFrames": ["frame-006", "frame-007", "frame-005"]}]
    store = {"cuts": {"cut-001": {"frame": "frame-004"}, "cut-002": {"frame": "frame-006", "discarded": output_contract.UNDO_DISCARD_REASON}},
             "leftOut": [{"frame": "frame-005", "chapter": "chapter-02-text"},
                         {"frame": "frame-007", "chapter": "chapter-01-text"}]}
    line = document_validator.describe_open_needed_frames(paths, sections, "chapter-02-text", store)
    harness.check("a cut to place, a frame left out, an undone cut not counted, another follow-up's leave_out not counted",
                  line == "Needed frames of this follow-up: frame-004 cut-001 made: put one in your answer as its own image "
                          "line; frame-005 left out; frame-006 nothing yet: look and cut it, or `leave_out` it; frame-007 "
                          "nothing yet: look and cut it, or `leave_out` it.", line)
    harness.check("no needed frame, no line",
                  document_validator.describe_open_needed_frames(paths, [{"slug": "heading-004-1"}], "chapter-02-text", store) == "", "")
    # A leave_out as shown by a placed frame holds only while a placed cut shows it: told just "left out", a worker
    # placed no such cut and the leave_out was refused again (2026-10-06)
    store["leftOut"][0]["reason"] = "another placed frame shows the same visual information more clearly"
    line = document_validator.describe_open_needed_frames(paths, sections, "chapter-02-text", store)
    harness.check("a leave_out as shown elsewhere says the answer must place a cut that shows it",
                  "frame-005 left out as shown by a placed frame: it holds while a placed cut of another frame shows the same" in line, line)
    # A cut made of a frame left out for another reason: told only "left out" beside it, a worker could not tell whether
    # to place it (2026-10-06)
    store["leftOut"][0]["reason"] = "the text states it"
    store["cuts"]["cut-003"] = {"frame": "frame-005"}
    line = document_validator.describe_open_needed_frames(paths, sections, "chapter-02-text", store)
    harness.check("a cut made of a frame left out is named, to place or undo",
                  "frame-005 left out, and cut-003 made of it: place one as its own image line" in line, line)


def test_a_figure_repeats_only_one_placed_in_an_earlier_section():
    """A blocked answer's sections stay on disk; judged again on a resume, an accepted earlier part found its figure
    "repeated" by a later section that was never accepted, and was asked again (2026-10-05). Only sections before
    the one judged place a figure earlier."""

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    image = numpy.full((260, 900, 3), 255, dtype=numpy.uint8)
    for row, line in enumerate(["Reconciliation not available", "Name and account could not be checked",
                                "Contact the payee when in doubt"]):
        cv2.putText(image, line, (20, 60 + row * 70), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 2, cv2.LINE_AA)
    for cut_id in ("cut-001", "cut-002"):
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    document_file_service.write_image_store(paths, {"cuts": {
        cut_id: {"file": f"04_writing/images/cuts/{cut_id}.png", "frame": "frame-001"} for cut_id in ("cut-001", "cut-002")}})
    document_file_service.write_section(paths, "heading-001-1", '![Warning box](cut-001 "The check is down")\n[F1.1]\n')
    document_file_service.write_section(paths, "heading-002-1", '![Warning box](cut-002 "The check is down")\n[F2.1]\n')
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the cuts' words", True)
        return
    codes = {}
    for slug in ("heading-001-1", "heading-002-1"):
        report = ValidationReport(subject=slug)
        document_validator._check_repeated_figures(paths, slug, document_file_service.read_section(paths, slug), report)
        codes[slug] = {finding.code for finding in report.findings}
    harness.check("the earlier section is not judged against a later one", "DOCUMENT_IMAGE_REPEATED" not in codes["heading-001-1"],
                  str(codes))
    harness.check("the later section repeats the earlier one's figure", "DOCUMENT_IMAGE_REPEATED" in codes["heading-002-1"],
                  str(codes))
    # Part 10 of a heading comes after part 2, though "heading-001-10" sorts before "heading-001-2" as text
    for slug in ("heading-001-1", "heading-002-1"):
        document_file_service.build_section_path(paths, slug).unlink()
    document_file_service.write_section(paths, "heading-001-2", '![Warning box](cut-001 "The check is down")\n[F1.1]\n')
    document_file_service.write_section(paths, "heading-001-10", '![Warning box](cut-002 "The check is down")\n[F2.1]\n')
    report = ValidationReport(subject="heading-001-10")
    document_validator._check_repeated_figures(paths, "heading-001-10",
                                               document_file_service.read_section(paths, "heading-001-10"), report)
    harness.check("part 10 is judged against part 2", "DOCUMENT_IMAGE_REPEATED" in {f.code for f in report.findings},
                  str([f.code for f in report.findings]))


def test_a_frame_left_out_counts_as_shown_when_its_own_cut_repeats_a_placed_one():
    """A warning box shown again at another height: the worker cut it, was told the cut repeats one placed under an
    earlier heading, and left the frame out; the leave-out check compared the same rectangle of both frames and
    called it unfounded, so two checks contradicted each other (2026-10-05)."""

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    image = numpy.full((260, 900, 3), 255, dtype=numpy.uint8)
    for row, line in enumerate(["Reconciliation not available", "Name and account could not be checked",
                                "Contact the payee when in doubt"]):
        cv2.putText(image, line, (20, 60 + row * 70), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 2, cv2.LINE_AA)
    for cut_id in ("cut-001", "cut-002"):
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    document_file_service.write_image_store(paths, {
        "cuts": {"cut-001": {"file": "04_writing/images/cuts/cut-001.png", "frame": "frame-001"},
                 "cut-002": {"file": "04_writing/images/cuts/cut-002.png", "frame": "frame-002"}},
        "leftOut": [{"frame": "frame-002", "chapter": "chapter-01-text-2",
                     "reason": "another placed frame shows the same warning"}]})
    document_file_service.write_section(paths, "heading-001-1", '![Warning box](cut-001 "The check is down")\n[F1.1]\n')
    document_file_service.write_section(paths, "heading-002-1", "The transfer still goes ahead. [F2.1]\n")
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the cuts' words", True)
        return
    report = document_validator.validate_needed_screenshots(paths, "heading-002-1", ["frame-002"], "chapter-01-text-2")
    harness.check("the left-out frame's own cut repeats a placed cut: the leave-out is founded",
                  "DOCUMENT_LEAVE_OUT_UNFOUNDED" not in {finding.code for finding in report.findings},
                  str([(finding.code, finding.detail) for finding in report.findings]))


def test_a_frame_left_out_counts_as_shown_when_its_cut_is_placed_in_the_same_answer():
    """The worker placed the left-out frame's cut in the same answer: skipping placed cuts, the leave-out check found no
    cut of the frame to compare, called the leave-out unfounded, and the repeat check refused the same cut as shown
    under an earlier heading (2026-10-05)."""

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    image = numpy.full((260, 900, 3), 255, dtype=numpy.uint8)
    for row, line in enumerate(["Reconciliation not available", "Name and account could not be checked",
                                "Contact the payee when in doubt"]):
        cv2.putText(image, line, (20, 60 + row * 70), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 2, cv2.LINE_AA)
    for cut_id in ("cut-001", "cut-002"):
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    document_file_service.write_image_store(paths, {
        "cuts": {"cut-001": {"file": "04_writing/images/cuts/cut-001.png", "frame": "frame-001"},
                 "cut-002": {"file": "04_writing/images/cuts/cut-002.png", "frame": "frame-002"}},
        "leftOut": [{"frame": "frame-002", "chapter": "chapter-01-text-2",
                     "reason": "another placed frame shows the same warning"}]})
    document_file_service.write_section(paths, "heading-001-1", '![Warning box](cut-001 "The check is down")\n[F1.1]\n')
    document_file_service.write_section(paths, "heading-002-1", '![Warning box](cut-002 "The check is down")\n[F2.1]\n')
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the cuts' words", True)
        return
    report = document_validator.validate_needed_screenshots(paths, "heading-002-1", ["frame-002"], "chapter-01-text-2")
    harness.check("the left-out frame's cut placed in the same answer still repeats the earlier cut: the leave-out is founded",
                  "DOCUMENT_LEAVE_OUT_UNFOUNDED" not in {finding.code for finding in report.findings},
                  str([(finding.code, finding.detail) for finding in report.findings]))


def test_a_frame_left_out_and_placed_by_its_own_cut_is_shown():
    """A frame was left out as shown elsewhere, then placed by its own cut when the leave-out was refused; the record
    stayed, and the check compared the frame with other frames' cuts only and refused it a third time (2026-10-06)."""

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(cuts_dir / "cut-001.png"), numpy.full((200, 400, 3), 255, dtype=numpy.uint8))
    document_file_service.write_image_store(paths, {
        "cuts": {"cut-001": {"file": "04_writing/images/cuts/cut-001.png", "frame": "frame-001"}},
        "leftOut": [{"frame": "frame-001", "chapter": "chapter-01-text-2",
                     "reason": "another placed frame shows the same visual information more clearly"}]})
    document_file_service.write_section(paths, "heading-001-1", '![Editor](cut-001 "Breakpoints")\n[F1.1]\n')
    report = document_validator.validate_needed_screenshots(paths, "heading-001-1", ["frame-001"], "chapter-01-text-2")
    harness.check("a frame placed by its own cut is shown, whatever its leave-out says",
                  "DOCUMENT_LEAVE_OUT_UNFOUNDED" not in {finding.code for finding in report.findings},
                  str([(finding.code, finding.detail) for finding in report.findings]))


def test_words_one_letter_off_count_as_shared_between_two_cuts():
    """The text reader misread an umlaut differently in two cuts of one warning box, and the box shown twice fell under
    the share that names one cut a part of the other (2026-10-05)."""

    fewer = {"reconciliation", "fiihren", "payee", "contact", "account", "checked", "could", "name", "the", "doubt"}
    more = {"reconciliation", "filhren", "payee", "contact", "account", "checked", "could", "name", "the", "doubt",
            "information"}
    harness.check("a word one letter off counts as shared", text_detection_service.count_shared_words(fewer, more) == 10)
    harness.check("short words must match exactly", text_detection_service.count_shared_words({"ab1"}, {"ab2"}) == 0)
    harness.check("two letters off is another word", text_detection_service.count_shared_words({"payment"}, {"pavmenf"}) == 0)


def test_a_cut_a_reset_set_aside_still_founds_a_leave_out_and_an_undone_one_does_not():
    """A later chapter's reset set aside the cuts an accepted chapter's leave-out rested on, and the resume refused that
    chapter; a cut the worker undid is its own wrong cut and proves nothing."""

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    image = numpy.full((260, 900, 3), 255, dtype=numpy.uint8)
    for row, line in enumerate(["Reconciliation not available", "Name and account could not be checked",
                                "Contact the payee when in doubt"]):
        cv2.putText(image, line, (20, 60 + row * 70), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 2, cv2.LINE_AA)
    for cut_id in ("cut-001", "cut-002"):
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the cuts' words", True)
        return
    for discarded, is_founded in ((output_contract.RESET_DISCARD_REASON, True), (output_contract.UNDO_DISCARD_REASON, False)):
        cuts = {"cut-001": {"file": "04_writing/images/cuts/cut-001.png", "frame": "frame-001"},
                "cut-002": {"file": "04_writing/images/cuts/cut-002.png", "frame": "frame-002", "discarded": discarded}}
        harness.check(f"a cut set aside as {discarded!r} {'founds' if is_founded else 'does not found'} the leave-out",
                      document_validator._is_frame_cut_shown(paths, cuts, "frame-002", ["cut-001"]) is is_founded)


def test_a_missing_needed_frame_whose_cut_repeats_a_placed_cut_is_told_to_be_left_out():
    """Told only "place its cut", a worker placed a cut that repeated one already placed and was refused for the repeat."""

    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    image = numpy.full((260, 900, 3), 255, dtype=numpy.uint8)
    for row, line in enumerate(["Reconciliation not available", "Name and account could not be checked",
                                "Contact the payee when in doubt"]):
        cv2.putText(image, line, (20, 60 + row * 70), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 2, cv2.LINE_AA)
    for cut_id in ("cut-001", "cut-002"):
        cv2.imwrite(str(cuts_dir / f"{cut_id}.png"), image)
    document_file_service.write_image_store(paths, {
        "cuts": {"cut-001": {"file": "04_writing/images/cuts/cut-001.png", "frame": "frame-001"},
                 "cut-002": {"file": "04_writing/images/cuts/cut-002.png", "frame": "frame-002"}}})
    document_file_service.write_section(paths, "heading-001-1", '![Warning box](cut-001 "The check is down")\n[F1.1]\n')
    if not text_detection_service.is_available():
        harness.check("Tesseract is needed to read the cuts' words", True)
        return
    report = document_validator.validate_needed_screenshots(paths, "heading-001-1", ["frame-001", "frame-002"],
                                                            "chapter-01-text-1")
    fixes = " ".join(finding.fix or "" for finding in report.findings if finding.code == "DOCUMENT_NEEDED_FRAME_UNACCOUNTED")
    harness.check("the frame whose cut repeats the placed cut is named with its leave_out",
                  "frame-002 already shown" in fixes and "leave_out" in fixes, fixes)


def test_a_missing_needed_frame_names_the_cut_already_made_of_it():
    """Told only "frame-002 not accounted for", a worker that had cut it and left the cut's line out of its rewrite
    believed the line was there, and twice in one chapter."""

    paths = fx.make_paths()
    document_file_service.write_image_store(paths, {
        "cuts": {"cut-004": {"file": "04_writing/images/cuts/cut-004.png", "frame": "frame-002"},
                 "cut-005": {"file": "04_writing/images/cuts/cut-005.png", "frame": "frame-002", "discarded": True}}})
    document_file_service.write_section(paths, "heading-001-1", "The proxy keeps the token. [F1.1]\n")
    report = document_validator.validate_needed_screenshots(paths, "heading-001-1", ["frame-002"], "chapter-01-text-1")
    messages = " ".join(f"{finding.detail} {finding.fix or ''}" for finding in report.findings
                        if finding.code == "DOCUMENT_NEEDED_FRAME_UNACCOUNTED")
    harness.check("the cut made of the missing frame is named, the one set aside is not",
                  "cut-004 made of frame-002 is not in your answer" in messages and "cut-005" not in messages, messages)


def test_a_missing_pdf_figure_is_asked_only_for_its_cut():
    """Told a missing figure could be replaced or left out, the wiki's second run's writer tried both on the Calvin
    cycle, a turn each, and the frame tool refuses both for a PDF figure."""

    from services import phase_file_service
    paths = fx.make_paths()
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": output_contract.PDF_TEXT_ONLY_MEDIUM,
                                                   "items": [{"slug": "frame-002", "index": 2,
                                                              "medium": output_contract.PDF_FIGURE_MEDIUM}]})
    document_file_service.write_image_store(paths, {"cuts": {}})
    document_file_service.write_section(paths, "heading-001-1", "The cycle fixes carbon. [F2.1]\n")
    report = document_validator.validate_needed_screenshots(paths, "heading-001-1", ["frame-002"], "chapter-01-text-1")
    fixes = " ".join(finding.fix or "" for finding in report.findings if finding.code == "DOCUMENT_NEEDED_FRAME_UNACCOUNTED")
    harness.check("the fix asks for the cut alone", "never replaced or left out" in fixes
                  and "replace_with_diagram" not in fixes, fixes)


def test_a_cut_taking_in_the_slide_s_footer_names_it_defect():
    # A cut taken again wider took in the slide's address line and page number below the part, and the tool warned of
    # nothing. Now a web address or a page number alone in the frame's bottom band, outside every box, is named
    def word(text: str, block: int, line: int, box: list[int]) -> dict:
        return {"text": text, "block": block, "line": line, "box": box}

    words = [word("Gateway", 1, 1, [100, 60, 200, 80]),                                  # inside the panel
             word("https://wiki.example.test/Gateway", 2, 1, [160, 350, 380, 362]),   # the slide's address line
             word("<number>", 3, 1, [520, 366, 532, 374]),                           # not a page number
             word("39", 4, 1, [524, 380, 531, 388]),                                  # the page number
             word("The", 5, 1, [100, 300, 120, 310])]                                 # a sentence above the band
    boxes = [[70, 0, 480, 290]]
    footer = frame_region_utils.list_footer_lines([20, 0, 545, 395], words, 395, boxes)
    harness.check("the address line and the page number are named", [line["text"] for line in footer]
                  == ["https://wiki.example.test/Gateway", "39"], str(footer))
    harness.check("a cut of the panel names nothing",
                  frame_region_utils.list_footer_lines([60, 0, 490, 326], words, 395, boxes) == [], "")


def test_a_cut_holding_none_of_the_texts_phase_3_names_is_warned():
    """A cut by a box id that was the window's title bar held the bookmarks, not the code phase 3 asked for, and its
    result said "Warnings: none"."""

    item = {"record": {"screenshots": [{"importance": "needed", "part": "the order file in the editor",
                                        "why": "shows the tags (`OrderHeader`, `LineItem`) of the file"}]}}
    harness.check("a cut with none of the named texts is warned with them",
                  illustration_action_service._list_needed_texts_missing(item, ["Bookmarks", "Wiki", "Tracker"])
                  == ["OrderHeader", "LineItem"])
    harness.check("a named text the reader misread by two letters still counts as on the cut",
                  illustration_action_service._list_needed_texts_missing(
                      {"record": {"screenshots": [{"importance": "needed", "why": 'the status "Delivered"', "part": "x"}]}},
                      ["Status", "Dellvered"]) == [])
    harness.check("a cut holding one of them is not warned",
                  illustration_action_service._list_needed_texts_missing(item, ["<OrderHeader>", "<Id>"]) == [])
    harness.check("notes that quote nothing warn of nothing",
                  illustration_action_service._list_needed_texts_missing(
                      {"record": {"screenshots": [{"importance": "needed", "why": "the form", "part": "top"}]}}, []) == [])


def test_an_account_number_is_found_however_the_reader_groups_it():
    """No check refused a readable IBAN on a cut, and a worker reasoned that an IBAN needed no mask."""

    harness.check("an IBAN in groups of four is found", bool(illustration_action_service._IBAN.findall("Girokonto DE47 1004 0000 0372 4036 01 | EUR")))
    harness.check("an IBAN run together is found", bool(illustration_action_service._IBAN.findall("DE89330400010290862200,Jonas")))
    harness.check("an IBAN whose country code the reader split off is found", bool(illustration_action_service._IBAN.findall("JONAS DOE 1004 0000 0372 4036 00")))
    harness.check("a year and an order number are not", illustration_action_service._IBAN.findall("Order 2025 1234 total") == [] and illustration_action_service._IBAN.findall("DE12 3456") == [])


def test_a_table_s_columns_of_four_digits_are_no_account_number_defect():
    # A process viewer's memory columns ("<four digits> <four digits> ...") were warned as a readable account number.
    # Now digit groups with no country code are an IBAN only as four groups of four and a closing pair
    harness.check("a table's number columns are not",
                  illustration_action_service._IBAN.findall("1000 2000 3000 4000 5000 6000 RES SHR") == [])
    harness.check("an IBAN whose country code was split off still is",
                  bool(illustration_action_service._IBAN.findall("JONAS DOE 1004 0000 0372 4036 00")))


def test_a_token_flag_on_a_command_line_is_a_credential_key():
    """A terminal's login command showed `--token=<value>`, read with the dashes as one long dash; it was no credential
    key, so a cut showing a whole token would have been placed unmasked (2026-10-06)."""

    words = ["PS", "H:\\>", "oc", "login", "\u2014token=sha999~Ab3dEf6hIj9kLm2nOp5qRs8tUv", "--server=https://api.example.test"]
    keys = document_text_utils.find_credential_keys(words)
    harness.check("the token flag with its long value is a credential key", keys == [words[4]], str(keys))
    harness.check("and a readable secret", len(document_text_utils.find_readable_secrets(words)) == 1,
                  str(document_text_utils.find_readable_secrets(words)))
    harness.check("a flag naming a token with a short placeholder is none",
                  document_text_utils.find_credential_keys(["oc", "login", "--token=generated"]) == [])


def test_a_long_value_typed_from_the_image_matches_the_reader_s_word():
    """A worker masked a token by typing it from the image; the reader had read a few of its letters otherwise, and the
    exact match found no word, so the cut kept the whole token (2026-10-06)."""

    from services import illustration_action_service
    read = "--token=sha999~Ab3dEf6hIj9kLm2nOp5qRs8tUvWx"
    typed = ["sha999~Ab3dEf6hlj9kLm2nOp5qRs8tUvWx"]
    harness.check("a long value a few letters off matches the word holding it",
                  illustration_action_service._holds_piece(read, typed))
    harness.check("a short word a letter off does not", not illustration_action_service._holds_piece("Port", ["Pork"]))
    harness.check("an unrelated long word does not",
                  not illustration_action_service._holds_piece("--server=https://api.example.test", typed))


def test_the_published_document_reads_a_figure_description_as_no_repeat():
    """The published copy's own repeat check failed a run on a screen message quoted in a chapter and, as write.md
    asks, in its figure's description, after the draft's check had been told to skip descriptions (2026-10-08)."""

    from validators import document_folder_validator
    markdown = ('## One\n\nThe page confirms "Thank you, your consent was sent" at once.\n\n'
                '![page](resources/figure-01.png)\n\n*Figure 1. Consent is confirmed at once.*\n\n'
                '> **What the figure shows:** the heading "Thank you, your consent was sent" over a line of help text.\n')
    harness.check("a description quoting the prose is no repeat in the published copy",
                  document_folder_validator.find_repeated_document_sentences(markdown) == [],
                  str(document_folder_validator.find_repeated_document_sentences(markdown)))
    harness.check("the prose said twice still is",
                  document_folder_validator.find_repeated_document_sentences(
                      markdown + '\nThe page confirms "Thank you, your consent was sent" at once.\n') != [], "")


def test_a_refused_look_asked_again_is_carried_out_again_not_pointed_back_to():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    frame_file = paths.output_root / store_frame_path(paths, "frame-000")
    kept = frame_file.read_bytes()
    frame_file.write_bytes(b"no image")
    refused = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    frame_file.write_bytes(kept)
    again = illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    harness.check("a look that was refused is looked again, with its boxes", refused.is_refused and again.frame_path is not None,
                  f"{refused.text[:80]} | {again.text[:80]}")


def store_frame_path(paths, frame_id: str) -> str:
    return f"frames/{frame_id}.jpg"


def test_a_frame_left_out_twice_keeps_one_record_with_the_newer_reason():
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "leave_out", {"frame": "frame-001", "reason": "The text states the date."})
    illustration_action_service.run_action(context, store, "leave_out", {"frame": "frame-001", "reason": "Frame 2 shows the same form."})
    records = [record for record in store["leftOut"] if record["frame"] == "frame-001"]
    harness.check("one record, the newer reason", [record["reason"] for record in records] == ["Frame 2 shows the same form."],
                  str(records))


def test_two_cuts_whose_words_could_not_be_read_are_no_part_of_each_other():
    from services import text_detection_service as reader

    paths = fx.make_paths()
    cuts = {"cut-001": {"file": "04_writing/images/cuts/cut-001.png"}, "cut-002": {"file": "04_writing/images/cuts/cut-002.png"}}
    kept = reader.read_figure_words
    reader.read_figure_words = lambda path: None
    try:
        try:
            result = document_validator._is_part_of(paths, cuts, "cut-001", "cut-002")
        except TypeError as error:
            result = f"raised: {error}"
    finally:
        reader.read_figure_words = kept
    harness.check("an unread cut is part of nothing", result is False, str(result))


def test_a_blank_cut_s_fix_offers_only_cuts_still_in_use():
    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(cuts_dir / "cut-001.png"), numpy.full((60, 120, 3), 255, dtype=numpy.uint8))
    store = {"cuts": {"cut-001": {"file": "04_writing/images/cuts/cut-001.png", "frame": "frame-001"},
                      "cut-002": {"file": "04_writing/images/cuts/cut-002.png", "frame": "frame-001", "discarded": output_contract.UNDO_DISCARD_REASON},
                      "cut-003": {"file": "04_writing/images/cuts/cut-003.png", "frame": "frame-001"}}}
    document_file_service.write_image_store(paths, store)
    section = {"images": [{"cut": "cut-001", "cutFile": "04_writing/images/cuts/cut-001.png"}]}
    report = ValidationReport(subject="heading-001-1")
    document_validator._check_blank_cuts(paths, "heading-001-1", section, report)
    fix = " ".join(finding.fix for finding in report.findings)
    harness.check("the undone cut is not offered, the one in use is", "cut-003" in fix and "cut-002" not in fix, fix)


def test_a_chapter_is_offered_every_frame_tool_the_runner_knows():
    from schemas import write_actions
    harness.check("the offered frame tools are the registry's tools, in its order",
                  list(write_actions.FRAME_TOOLS) == list(illustration_actions.ACTIONS),
                  str(list(write_actions.FRAME_TOOLS)))



def test_a_mask_or_mark_by_a_box_id_the_cut_does_not_list_is_handed_back_not_refused():
    """A refusal of a frame tool stops the run under `failOnFrameCutFail`; `cut` already hands the same mix-up back."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    masked = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "box_id": "T3"})
    marked = illustration_action_service.run_action(context, store, "mark", {"cut": "cut-001", "box_id": "T3"})
    both = illustration_action_service.run_action(context, store, "mark", {"cut": "cut-001", "box_id": "C1",
                                                                          "rect": [10, 10, 50, 50]})
    harness.check("a mask by an id of another list is handed back, naming the boxes the cut has",
                  not masked.is_refused and "no box T3" in masked.text, masked.text)
    harness.check("a mark by it is handed back too", not marked.is_refused and "no box T3" in marked.text, marked.text)
    harness.check("a box id given with a rectangle is handed back", not both.is_refused and "both ways" in both.text,
                  both.text)
    harness.check("and nothing was masked or marked", store["cuts"]["cut-001"]["masks"] == 0
                  and store["cuts"]["cut-001"]["marks"] == 0, str(store["cuts"]["cut-001"]))


def test_a_mark_with_a_number_and_no_place_is_handed_back_not_refused_defect():
    # A writer marked a cut "1" to number its figure, with no box and no rectangle; the refusal stopped the strict run
    # (DEFECTS 511). Now nothing is drawn, the program's own figure numbers are named, and nothing is refused
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    numbered = illustration_action_service.run_action(context, store, "mark", {"cut": "cut-001", "number": 1})
    blank = illustration_action_service.run_action(context, store, "mark", {"cut": "cut-001"})
    harness.check("a number alone is handed back, not refused", not numbered.is_refused
                  and "numbers the document's figures itself" in numbered.text, numbered.text)
    harness.check("nothing was marked", store["cuts"]["cut-001"]["marks"] == 0, str(store["cuts"]["cut-001"]))
    harness.check("a mark with neither a number nor a place is still refused", blank.is_refused, blank.text)


def test_a_mask_whose_image_cannot_be_written_is_refused_and_not_recorded():
    """`cv2.imwrite` returns False on a failed write and raises nothing: the secret stayed readable on the cut while the
    tool reported it masked."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [100, 100, 400, 300]})
    real_write = illustration_action_service.cv2.imwrite
    illustration_action_service.cv2.imwrite = lambda path, image: False
    try:
        result = illustration_action_service.run_action(context, store, "mask", {"cut": "cut-001", "rect": [10, 10, 200, 40]})
    finally:
        illustration_action_service.cv2.imwrite = real_write
    harness.check("the failed write is refused by name", result.is_refused and "could not be written" in result.text,
                  result.text)
    harness.check("and no mask is recorded", store["cuts"]["cut-001"]["masks"] == 0
                  and not store["cuts"]["cut-001"].get("maskedAreas"), str(store["cuts"]["cut-001"]))


def test_a_look_at_a_pdf_figure_frame_says_the_whole_frame_is_the_figure():
    """A PDF figure frame is the figure, cut from its page by phase 2; a Venn diagram's look listed only its garbled
    labels' boxes, and the worker cut its title strip as the figure (NIST, chapter 6)."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    context.frames["frame-000"]["medium"] = output_contract.PDF_FIGURE_MEDIUM
    looked = illustration_action_service.run_action(context, _build_store(), "look", {"frame": "frame-000"})
    harness.check("the look names the whole frame as the figure, with its rectangle",
                  "the whole frame is the figure" in looked.text and "[0, 0, 960, 540]" in looked.text, looked.text)


def test_a_thin_figure_cut_whole_is_not_warned_of_a_short_side_defect():
    # A thin PDF figure (674 x 88) cut whole was warned its side was under the minimum, and the writer undid the right cut,
    # tried a diagram and cut it again (DEFECTS 525). A whole cut is as large as the frame allows, so it is not warned
    paths, _video = _build_run(Path(tempfile.mkdtemp()))
    strip = numpy.full((88, 674, 3), 255, numpy.uint8)
    for x in (20, 250, 480):
        cv2.rectangle(strip, (x, 20), (x + 170, 68), (60, 60, 60), 2)
    cv2.imwrite(str(paths.output_root / "frames" / "frame-001.jpg"), strip)
    # no recording to read, so the cut is the stored figure itself, at its own 674 x 88
    context = _build_context(paths, paths.output_root / "missing.mp4")
    context.frames["frame-001"].update({"medium": output_contract.PDF_FIGURE_MEDIUM, "box": None, "screenBox": None})
    store = _build_store()
    whole = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-001"})
    cut = store["cuts"].get("cut-001", {})
    harness.check("the whole thin figure is cut with no short-side warning",
                  cut and not any("shorter than" in warning for warning in cut.get("warnings", [])), f"{whole.text} {cut}")
    part = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-001", "rect": [0, 0, 300, 88]})
    harness.check("a part of it is still warned", any("shorter than" in warning
                                                      for warning in store["cuts"].get("cut-002", {}).get("warnings", [])),
                  part.text)


def test_a_cut_asked_in_the_turn_of_its_frame_s_first_look_waits_for_the_next_turn_defect():
    # A writer called `look` and `cut` on one frame in one turn, its rectangle guessed before the picture reached it, and
    # the cut kept the slide's margin and cut through its lines (DEFECTS 363). A look is seen from the next turn on now
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    store = _build_store()
    illustration_action_service.mark_looks_seen(store)
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    blind = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [10, 10, 400, 300]})
    harness.check("the cut in the look's own turn is not made, and says why", not store["cuts"]
                  and "same turn" in blind.text, blind.text)
    illustration_action_service.mark_looks_seen(store)
    seen = illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000", "rect": [10, 10, 400, 300]})
    harness.check("in the next turn the cut is made", len(store["cuts"]) == 1, seen.text)


def test_a_pdf_figure_frame_is_cut_whole_with_no_look_first():
    """A PDF figure frame is the figure, so its look says only "cut it whole": a chapter of 9 figures spent a look and a
    cut on each, 24 minutes and 1.17 M prompt tokens (the hard figures PDF). A meeting's frame still gets its look."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    results = {}
    for medium in ("video", output_contract.PDF_FIGURE_MEDIUM):
        context = _build_context(paths, video)
        context.frames["frame-000"]["medium"] = medium
        store = _build_store()
        results[medium] = (illustration_action_service.run_action(context, store, "cut", {"frame": "frame-000"}), store)
    meeting, figure = results["video"], results[output_contract.PDF_FIGURE_MEDIUM]
    harness.check("a meeting's frame gets its look and no cut", "had not looked" in meeting[0].text and not meeting[1]["cuts"],
                  meeting[0].text)
    harness.check("a PDF figure is cut at once", "had not looked" not in figure[0].text and len(figure[1]["cuts"]) == 1,
                  figure[0].text)


def test_a_pdf_figure_is_never_replaced_by_a_diagram():
    """A PDF's figure is the document's own picture: replaced by phase 3's diagram, the wiki's Calvin cycle lost its
    molecules and its cycle figure its return arrows. A meeting's frame may still be replaced."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    context.frames["frame-000"]["medium"] = output_contract.PDF_FIGURE_MEDIUM
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    result = illustration_action_service.run_action(context, store, "replace_with_diagram",
                                                    {"frame": "frame-000", "reason": "the diagram keeps every part"})
    harness.check("nothing is recorded, and the cut is asked for",
                  not store.get("diagramReplacements") and "placed as its cut" in result.text, result.text)


def test_a_frame_whose_lines_the_notes_diagram_leaves_out_is_not_replaced_defect():
    # A slide's attribute lists stood under its boxes; replaced by the notes' diagram of its arrows alone, they were lost.
    # A frame showing lines of two words or more its notes' diagram does not hold is now handed back, the cut asked for
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    context.frames["frame-000"]["record"] = {
        "diagram": 'flowchart LR\n    A["Gateway"] --> B["Billing"]',
        "screen": '- slide: white\n  - title "Order flow"\n  - box "Gateway"\n  - box "Billing"\n'
                  '  - text "Refunds possible"\n  - text "Partner payouts possible"'}
    store = _build_store()
    illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
    result = illustration_action_service.run_action(context, store, "replace_with_diagram",
                                                    {"frame": "frame-000", "reason": "the diagram keeps every box"})
    harness.check("nothing is recorded, the left-out lines named and the cut asked for",
                  not store.get("diagramReplacements") and "Partner payouts possible" in result.text
                  and "Order flow" not in result.text and "`cut` frame-000" in result.text, result.text)
    context.frames["frame-000"]["record"]["screen"] = '- slide: white\n  - title "Order flow"\n  - box "Gateway"'
    illustration_action_service.run_action(context, store, "replace_with_diagram",
                                           {"frame": "frame-000", "reason": "the diagram keeps every box"})
    harness.check("a frame whose lines the diagram holds is still replaced", bool(store.get("diagramReplacements")), "")


def test_a_needed_pdf_figure_is_never_left_out():
    """Left out because the notes hold its diagram, the wiki's Calvin cycle was the replacement `replace_with_diagram`
    refuses, by another tool. A figure phase 3 did not mark needed may still be left out."""

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    results = {}
    for importance in ("needed", "none"):
        context = _build_context(paths, video)
        context.frames["frame-000"]["medium"] = output_contract.PDF_FIGURE_MEDIUM
        context.frames["frame-000"]["record"] = {"slug": "frame-000",
                                                  "screenshots": [{"importance": importance, "why": "the cycle", "part": ""}]}
        store = _build_store()
        results[importance] = (illustration_action_service.run_action(
            context, store, "leave_out", {"frame": "frame-000", "reason": "the diagram note draws it"}), store)
    needed, plain = results["needed"], results["none"]
    harness.check("a needed figure is not recorded left out, and the cut is asked for",
                  not needed[1]["leftOut"] and "placed as its cut" in needed[0].text, needed[0].text)
    harness.check("a figure nobody needs may be left out", len(plain[1]["leftOut"]) == 1, plain[0].text)


def test_a_needed_pdf_figure_written_as_a_markdown_block_may_be_left_out_defect():
    # A table figure the notes held as a markdown block was asked to be written as the block and its frame never cut,
    # while `leave_out` refused it as a needed figure; the chapter held the table three times (DEFECTS 517). Now such a
    # figure may be left out, and a figure with no block still may not
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    results = {}
    for markdown in ("| Alert | Action |\n|---|---|\n| GW-RATE | open ticket |", "(none)"):
        context = _build_context(paths, video)
        context.frames["frame-000"]["medium"] = output_contract.PDF_FIGURE_MEDIUM
        context.frames["frame-000"]["record"] = {"slug": "frame-000", "as_markdown": markdown,
                                                  "screenshots": [{"importance": "needed", "why": "the table", "part": ""}]}
        store = _build_store()
        results[markdown] = (illustration_action_service.run_action(
            context, store, "leave_out", {"frame": "frame-000", "reason": "written as its markdown table"}), store)
    table, picture = results.values()
    harness.check("a figure held as a markdown block is recorded left out", len(table[1]["leftOut"]) == 1, table[0].text)
    harness.check("a figure with no block is still placed as its cut",
                  not picture[1]["leftOut"] and "placed as its cut" in picture[0].text, picture[0].text)


def test_a_cut_of_a_pdf_figure_frame_names_no_face_to_mask():
    """A face on a cut is named so a meeting's camera is masked; a PDF's photograph is the document's published
    content, and told to mask each face, a worker would place a page of blanked portraits (the hard figures PDF)."""

    from services import face_detection_service

    paths, video = _build_run(Path(tempfile.mkdtemp()))
    original = face_detection_service.detect_faces
    face_detection_service.detect_faces = lambda gray: [(10, 10, 60, 60)]
    try:
        texts = {}
        for medium in ("video", output_contract.PDF_FIGURE_MEDIUM):
            context = _build_context(paths, video)
            context.frames["frame-000"]["medium"] = medium
            store = _build_store()
            illustration_action_service.run_action(context, store, "look", {"frame": "frame-000"})
            texts[medium] = illustration_action_service.run_action(
                context, store, "cut", {"frame": "frame-000", "box_ids": [_find_panel(store, "frame-000")]}).text
    finally:
        face_detection_service.detect_faces = original
    harness.check("a meeting's cut names the face, a PDF figure's does not",
                  "finds a face" in texts["video"] and "finds a face" not in texts[output_contract.PDF_FIGURE_MEDIUM],
                  str(texts))


def test_a_pdf_figure_s_caption_words_are_not_asked_of_its_cut():
    """A PDF figure's caption stands under it on the page and is said over its frame, never in its picture: the hard
    figures PDF's portrait was warned that the reader did not read "first owner" on the cut."""

    from schemas import frame_record

    shots = [{"importance": frame_record.SCREENSHOT_NEEDED, "part": 'the portrait', "why": 'defines the "first owner" role'}]
    figure = {"medium": output_contract.PDF_FIGURE_MEDIUM, "caption": "Figure 2. The gateway's first owner",
              "record": {"screenshots": shots}}
    slide = {"record": {"screenshots": shots}}
    harness.check("the caption's words are not asked of a figure's cut, a slide's quoted text still is",
                  illustration_action_service._list_needed_texts_missing(figure, []) == []
                  and illustration_action_service._list_needed_texts_missing(slide, []) == ["first owner"])


def test_a_cut_of_a_pdf_figure_over_a_page_break_holds_both_parts_defect():
    # Defect: a figure the page break cut in two was two frames, each cut from its own page. Fix: phase 1 joins them into
    # one frame of the parts stacked, and its cut reads every part from the PDF and stacks them the same way.
    from cases import test_pdf_source

    pdf = test_pdf_source._build_page_break_figure_pdf()
    transcript, _removed = pdf_source_service.build_page_transcript(pdf, is_read_as_pages=False)
    paths, _video = _build_run(Path(tempfile.mkdtemp()))
    items = pdf_source_service.render_figures(pdf, transcript, paths.output_root / "frames")
    context = IllustrationContext(paths=paths, video_path=pdf, frames={items[0]["slug"]: items[0]},
                                  min_cut_pixels=MIN_CUT_PIXELS, chapter=ORDERING_CHAPTER_ID)
    store = _build_store()
    result = illustration_action_service.run_action(context, store, "cut", {"frame": items[0]["slug"]})
    cut = next(iter(store["cuts"].values()), {})
    image = cv2.imread(str(paths.output_root / cut.get("file", "missing")))
    stacked = pdf_source_service.read_figure_image(pdf, items[0]["parts"])
    harness.check("the cut is the whole stacked figure at full resolution, read from the PDF", cut.get("isFromRecording") and image is not None and stacked is not None
                  and abs(image.shape[0] - stacked.shape[0]) <= 4 and abs(image.shape[1] - stacked.shape[1]) <= 4,
                  f"{result.text} {None if image is None else image.shape} {None if stacked is None else stacked.shape}")


def test_a_frame_named_by_its_number_is_the_frame_with_that_number_defect():
    # Defect: the review's `look_at_frame` takes a frame's number and its `cut` a slug; a reviewer sent "frame-17" and
    # "17" to `cut`, three refusals in a row. Fix: a number names the one frame with that number.
    paths, video = _build_run(Path(tempfile.mkdtemp()))
    context = _build_context(paths, video)
    slug = next(iter(context.frames))
    number = int(context.frames[slug]["index"])
    for name in (str(number), f"frame-{number}"):
        store = _build_store()
        result = illustration_action_service.run_action(context, store, "look", {"frame": name})
        harness.check(f"{name!r} looks at {slug}", not result.is_refused and slug in store["looks"], result.text)
    refused = illustration_action_service.run_action(context, _build_store(), "look", {"frame": "frame-999"})
    harness.check("a number no frame has is refused, naming the slug's form", refused.is_refused and "frame-007" in refused.text,
                  refused.text)


def test_an_edge_many_marks_cross_is_named_and_a_clear_edge_is_not_defect():
    # A schematic's cut ended through its rotated pin labels, which the text reader does not read, and the tool warned
    # of nothing; an edge crossed by many marks is now named, an edge across empty background is not
    gray = numpy.full((300, 400), 245, numpy.uint8)
    for column in range(40, 360, 16):
        gray[150:260, column:column + 3] = 40                 # rotated labels: thin strokes standing across row 200
    gray[40:100, 60:340] = 120                                # a drawing above them
    harness.check("the bottom edge through the labels is named",
                  frame_region_utils.list_marked_edges(gray, [20, 20, 380, 200]) == [("bottom", 20)],
                  str(frame_region_utils.list_marked_edges(gray, [20, 20, 380, 200])))
    harness.check("an edge above them, on empty background, is not",
                  frame_region_utils.list_marked_edges(gray, [20, 20, 380, 130]) == [], "")


def test_a_cut_lying_inside_another_is_contained_and_a_shifted_part_is_not_defect():
    # Two cuts of one schematic, one inside the other, read too few words for the repeat check and were placed as two
    # figures; a cut found inside another at the same scale is now a part of it
    rng = numpy.random.default_rng(7)
    whole = (rng.random((240, 400)) * 255).astype(numpy.uint8)
    part = whole[40:160, 100:300].copy()
    other = (rng.random((120, 200)) * 255).astype(numpy.uint8)
    harness.check("a part cut from the whole is contained", frame_region_utils.is_contained(part, whole))
    harness.check("another picture of the same size is not", not frame_region_utils.is_contained(other, whole))
    harness.check("a sliver is never contained", not frame_region_utils.is_contained(whole[:10, :50].copy(), whole))


def test_the_smaller_cut_is_named_the_part_whatever_their_words_defect():
    # A schematic's whole frame, which the text reader reads almost nothing on, was named the part of its own relay
    # cut, and the fix told the worker to leave out the frame that alone showed the jumpers; the part is now the
    # smaller cut by area
    paths = fx.make_paths()
    cuts_dir = paths.output_root / "04_writing" / "images" / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    rng = numpy.random.default_rng(3)
    whole = (rng.random((300, 440, 3)) * 255).astype(numpy.uint8)
    cv2.imwrite(str(cuts_dir / "cut-004.png"), whole[:200, :300])
    cv2.imwrite(str(cuts_dir / "cut-007.png"), whole)
    cuts = {cut_id: {"file": f"04_writing/images/cuts/{cut_id}.png"} for cut_id in ("cut-004", "cut-007")}
    fix = document_validator._render_repeat_fix(paths, cuts, "cut-007", "cut-004", is_part=True)
    harness.check("the smaller cut is the part to leave out, the whole kept",
                  fix.startswith("cut-004 is part of cut-007: keep cut-007"), fix)


def test_an_edge_through_a_two_line_title_takes_in_the_whole_title_defect():
    # A cut's top edge ran through a slide's two-line title and was moved to keep only the second line whole: the
    # figure showed "Holzofen" alone; a short block of stacked lines is now taken whole
    words = [{"text": "Diagramm", "box": [86, 59, 300, 80], "block": 4, "line": 4001},
             {"text": "Solarthermie", "box": [310, 59, 464, 80], "block": 4, "line": 4001},
             {"text": "Holzofen", "box": [200, 86, 290, 107], "block": 4, "line": 4002}]
    grown = frame_region_utils.expand_to_whole_blocks([38, 95, 461, 353], words, 546, 403)
    harness.check("both lines of the title are in", grown[1] == 56, str(grown))


def test_a_placed_chart_is_found_on_a_frame_that_shows_it_lower_defect():
    # A slide scrolled up a few lines showed its chart lower on the next frame; the needed-frame check looked for the
    # placed cut at the same rectangle, found nothing, and refused the frame left out as shown by it
    from services import figure_match_service
    rng = numpy.random.default_rng(5)
    chart = (rng.random((120, 200, 3)) * 255).astype(numpy.uint8)
    later = numpy.full((300, 400, 3), 255, numpy.uint8)
    later[150:270, 100:300] = chart                    # the same chart, 90 pixels lower than on its own frame
    cut = {"frame": "frame-067", "lookRect": [100, 60, 300, 180]}
    harness.check("the chart is found lower on the other frame",
                  figure_match_service._is_same_view(later, "frame-068", cut, chart))
    other = (rng.random((300, 400, 3)) * 255).astype(numpy.uint8)
    harness.check("a frame without it is not", not figure_match_service._is_same_view(other, "frame-068", cut, chart))


def test_a_panel_spanning_the_frame_s_width_is_never_named_on_a_cut_defect():
    # Defect: a slide's upper part, read as a panel from edge to edge, was named on three right cuts of its diagram, and
    # the worker cut again each time. Fix: a panel spanning the frame's whole width is left out of the warning.
    panels = {"P1": [0, 4, 543, 230], "P2": [153, 93, 227, 208]}
    harness.check("the frame-wide band is not named", frame_region_utils.list_cut_panels([0, 80, 450, 400], panels, 543) == [],
                  str(frame_region_utils.list_cut_panels([0, 80, 450, 400], panels, 543)))
    harness.check("a panel inside the frame still is", frame_region_utils.list_cut_panels([0, 80, 200, 400], panels, 543)
                  == [("right", "P2")], str(frame_region_utils.list_cut_panels([0, 80, 200, 400], panels, 543)))


def test_a_cut_s_margin_stops_at_the_border_of_the_part_beside_it_defect():
    # Defect: a drop-down list cut by its box with a 12-pixel margin held a strip of the search field above it, its
    # border and half its text. Fix: each side of the margin stops at the first straight edge it meets
    gray = numpy.full((200, 300), 247, dtype=numpy.uint8)
    cv2.rectangle(gray, (20, 30), (280, 60), 190, 2)          # the search field, its bottom border at rows 59 to 61
    cv2.putText(gray, "typed text", (30, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 40, 1)
    cv2.putText(gray, "first suggestion", (30, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 40, 1)
    target = [20, 64, 280, 120]                                # the list, 2 pixels under the field
    rect = frame_region_utils.expand_rect(target, 12)
    stopped = frame_region_utils.stop_margin_at_borders(gray=gray, target=target, rect=rect)
    harness.check("the top stops under the field's border", stopped[1] >= 62, str(stopped))
    harness.check("the sides and bottom, on flat background, keep their margin", stopped[0] == 8 and stopped[2] == 292
                  and stopped[3] == 132, str(stopped))


def test_a_cut_holding_a_chart_s_axes_but_not_its_values_is_named_defect():
    # a chart was cut at its x axis, its values and title lost, and a chart's arrow ran out of a cut's top, with no
    # warning; a chart found by its axes is now named when a cut holds its axes and not its labels or arrows
    frame = numpy.full((400, 600), 255, dtype=numpy.uint8)
    cv2.line(frame, (100, 50), (100, 300), 0, 2)
    cv2.line(frame, (100, 300), (500, 300), 0, 2)
    for x in (150, 250, 350, 450):
        cv2.putText(frame, "0.5", (x - 15, 316), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1)
    cv2.circle(frame, (300, 150), 5, 0, -1)
    charts = frame_region_utils.find_chart_boxes(frame)
    harness.check("the chart is found with its axis values", len(charts) == 1 and charts[0][1][3] >= 312, str(charts))
    harness.check("a cut at its x axis is named", frame_region_utils.list_cut_charts([90, 40, 510, 302], charts) != [])
    harness.check("a cut holding the whole chart is not", frame_region_utils.list_cut_charts([80, 30, 520, 340], charts) == [])
    harness.check("nor a cut of another part of the frame",
                  frame_region_utils.list_cut_charts([0, 330, 600, 400], charts) == [])


def test_a_cut_a_few_pixels_short_of_a_chart_s_box_holds_it_whole_defect():
    # A flow chart cut whole by its own box ran 1 px past the cut's top and bottom, and the warning sent the writer to
    # undo a right cut. A chart running past by 6 px or less is held whole; one cut 12 px short is still named
    charts = [([100, 50, 500, 300], [90, 40, 510, 320])]
    harness.check("a chart 3 px past the cut is held whole",
                  frame_region_utils.list_cut_charts([93, 43, 507, 317], charts) == [])
    harness.check("one 12 px past is named", frame_region_utils.list_cut_charts([102, 52, 498, 308], charts) != [])


def test_a_chart_box_is_drawn_back_from_a_flat_margin_at_the_frame_s_edge_defect():
    # A chart in a slide's picture, a dark margin after it to the frame's edge: the box ran through the margin, and every
    # cut of the picture was warned the chart was cut short. Now a side at the edge is drawn back over flat lines
    import numpy

    gray = numpy.full((300, 400), 150, numpy.uint8)
    gray[:, 330:] = 20                                   # the slide's dark margin, out to the frame's edge
    gray[60:240, 80] = 0                                 # the y axis
    gray[240, 80:400] = 0                                # the x axis, its line running on through the margin
    for x in range(100, 320, 30):
        gray[150:240, x:x + 12] = 40                     # the bars
    charts = frame_region_utils._draw_back_flat_sides(gray, [80, 60, 400, 241], [70, 50, 400, 260])
    harness.check("the box's right side is drawn back to the picture's edge", charts[2] <= 331, str(charts))


def test_a_closed_outline_with_more_than_four_corners_is_a_panel_defect():
    # A window's two panes were dropped as panels, their outlines having 9 corners where their borders met the
    # content, and the worker cut a title bar, the only box. Now a closed outline filling its box is a panel
    import cv2
    import numpy

    frame = numpy.full((400, 600, 3), 255, numpy.uint8)
    pane = numpy.array([[60, 80], [300, 80], [300, 340], [200, 340], [200, 312], [170, 312], [170, 340], [60, 340]],
                       numpy.int32)
    cv2.polylines(frame, [pane], True, (0, 0, 0), 3)
    boxes = frame_region_utils.find_panel_boxes(frame)
    harness.check("the notched pane is a panel", any(abs(box[0] - 60) <= 4 and abs(box[2] - 300) <= 4 for box in boxes),
                  str(boxes))


def test_an_edge_moved_to_keep_a_line_whole_stops_past_the_line_defect():
    # The cut tool moved a bottom edge onto the box of the address line it ran through, and the chapter check refused the
    # placed cut, the edge's own row being the letters' last. Now a moved side stops 3 pixels past the line
    words = [{"text": "https://gateway.example.test/acmesync", "box": [100, 380, 400, 392], "block": 4, "line": 4001}]
    grown = frame_region_utils.expand_to_whole_blocks([0, 80, 520, 386], words, 960, 540)
    harness.check("the bottom edge stops past the line, the sides not moved stay", grown == [0, 80, 520, 395], str(grown))


def test_a_photo_beside_a_flat_margin_band_gets_a_box_defect():
    # A photo slide's dark margin band and page number had no box to leave out, so the whole frame was cut. Now the
    # frame drawn back over a flat band of another grey is offered, and a frame of one even background gets none
    frame = numpy.full((300, 400), 20, dtype=numpy.uint8)
    rng = numpy.random.default_rng(3)
    frame[:, :330] = rng.integers(60, 230, size=(300, 330))
    frame[280:290, 360:375] = 200
    box = frame_region_utils.find_content_box(frame)
    harness.check("the box stops at the band", box == [0, 0, 330, 300], str(box))
    even = numpy.full((300, 400), 20, dtype=numpy.uint8)
    harness.check("an even frame gets none", frame_region_utils.find_content_box(even) is None, "")



def test_the_footer_warning_names_the_rectangle_above_it_defect():
    # Told only to cut again above the slide's footer, a writer guessed the bottom edge from the grid four times and cut
    # through the terminals above the address each time. Now the warning names the rectangle, ending just above it
    address = {"text": "https://gateway.example.test/acmesync", "box": [40, 380, 260, 392], "block": 7, "line": 7001}
    footer = frame_region_utils.list_footer_lines([0, 60, 520, 400], [address], 420, [])
    harness.check("the rectangle ends 3 pixels above the address",
                  frame_region_utils.build_rect_above_footer([0, 60, 520, 400], footer) == [0, 60, 520, 377])
