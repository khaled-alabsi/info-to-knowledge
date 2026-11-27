"""No data is missing from the notes files (the owner, 2026-09-29: "no missing data is allowed").

`frame-NNN.md` and `frames-A-B-batch-summary.md` are the same results as `02_frames.json` and `03_batch_summaries.json` in
another container, for a person to read. Whatever the JSON holds for a frame or a batch is in its file as well: the frame's
time, its frame number and file, its change score and boxes, the words spoken over it, and for a batch its name, its frames, its
start and end and its source. The test does not fix a format: every stored value must appear, as the number or the text
the JSON holds, anywhere in the file.

Built test first: it fails until the files are written with everything in them. `todo.md`, open item 2.
"""

from support import fakes
from support import frame_batch_fixtures
from support import harness

from services import batch_summaries_service
from services import phase_file_service
from services import record_file_service

from cases import test_batch_open_questions as open_questions

FRAME_KEYS = ("timestamp", "frameNumber", "path", "score", "box", "screenBox", "transcriptBefore", "transcriptCurrent",
              "repeatOf", "builtFrom")
BATCH_KEYS = ("slug", "frames", "start", "end", "source")


def _flatten(value) -> list[str]:
    """The pieces of a stored value that must appear in the file: a number or a text as it is, a list element by element."""

    if value is None or value == "" or value == []:
        return []
    if isinstance(value, (list, tuple)):
        return [piece for element in value for piece in _flatten(element)]

    return [" ".join(str(value).split())]


def _find_missing(text: str, entry: dict, keys: tuple) -> list[str]:
    seen = " ".join(text.split())

    return [f"{key}: {piece}" for key in keys for piece in _flatten(entry.get(key)) if piece not in seen]


def _run_a_finished_batch():
    group = frame_batch_fixtures.build_group({**frame_batch_fixtures.FRAME, "frame": 0}, {**frame_batch_fixtures.FRAME, "frame": 1})
    questions = open_questions._batch_message([open_questions.RETRY_QUESTION])
    answers = open_questions._batch_message([], [f"{open_questions.RETRY_QUESTION} → Jonas."])
    ctx = frame_batch_fixtures.load_run_context(
        fakes.scripted(group, open_questions.NOTHING_TO_CHANGE, questions, answers), count=2)
    frames_file = phase_file_service.read_phase_file(ctx.paths, 2)
    for number, item in enumerate(frames_file["items"]):
        item.update({"frameNumber": 120 + number, "score": 0.5 + number / 10, "box": [0, 10, 100, 90],
                     "screenBox": [0, 5, 100, 95], "transcriptBefore": f"words before frame {number}"})
    phase_file_service.write_phase_file(ctx.paths, 2, frames_file)
    frame_batch_fixtures.run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)

    return ctx


def test_a_frame_file_holds_everything_its_json_entry_holds():
    ctx = _run_a_finished_batch()
    items = phase_file_service.read_phase_file(ctx.paths, 2)["items"]
    for item in items:
        path = record_file_service.build_record_path(ctx.paths, item["slug"])
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        missing = _find_missing(text, item, FRAME_KEYS)
        harness.check(f"{item['slug']}.md holds everything its entry in `02_frames.json` holds", missing == [],
                      "missing from the file: " + "; ".join(missing))


def test_a_batch_summary_file_holds_everything_its_json_entry_holds():
    ctx = _run_a_finished_batch()
    batch = batch_summaries_service.read_batch_summaries(ctx.paths)[0]
    path = record_file_service.build_record_path(ctx.paths, batch_summaries_service.build_summary_slug(open_questions.BATCH_SLUG))
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    missing = _find_missing(text, batch, BATCH_KEYS)
    harness.check("the batch's summary file holds everything its entry in `03_batch_summaries.json` holds", missing == [],
                  "missing from the file: " + "; ".join(missing))
