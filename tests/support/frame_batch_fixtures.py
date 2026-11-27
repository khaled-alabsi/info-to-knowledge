"""A run of phase 3 for tests: a run context with frames on disk, the frame loop driven to the end of its queue, and
the records and batch answers the scripted model gives back. Shared by every case file that runs a batch."""

import logging
import tempfile
from pathlib import Path

from support import fakes
from support import fixtures as fx

import config as app_config
from graph import graph_state
from graph.nodes import process_frames_node
from models import output_contract
from models.run_context import RunContext
from models.run_identity import RunIdentity
from services import phase_file_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore

FRAMES_PROCESSING = output_contract.PHASE_NUMBERS["frames_processing"]
ATMOSPHERE = ("The presenter speaks steadily and takes two questions about the figure. Nobody disagrees, and the room "
              "is quiet between the questions.")
BATCH_SUMMARY = ("After the survey's method, the presenter sets out to show the result of a poll on fuel prices. They "
                 "bring up the slide with the headline figure. They read out the share of voters who want cheaper fuel. "
                 "They add that the question was asked the same way as in the year before. Nobody asks about it. The "
                 "batch ends with the figure still on screen.")
RECORD = {"screen_kind": "slide", "facts": ["89 % of voters want cheaper fuel"], "diagram": "",
          "screen": '- slide: white, the whole frame\n  - text "89 %": centre',
          "speech_visual_alignment": "the figure is being read out", "builds_on": [],
          "contradicts_earlier": [], "open_questions": [],
          "third_person_description": "The presenter points at the figure on the slide and reads it out.",
          "tags": ["refund policy", "reference"], "urls": [], "action_items": [], "questions_asked": [], "screenshots": []}
EMPTY_RECORD = {**RECORD, "facts": [], "screen": '', "diagram": "", "speech_visual_alignment": ""}
SUMMARY = ("The presenter sets out to show how a reseller's order limit works, following the earlier look at the "
           "reseller's account. They open a test account's billing settings and point at the monthly limit, which "
           "caps what the reseller may order without a manual approval. They raise the limit and save it, and the page "
           "confirms the new value. Then they place a large order, which now goes through with no manual approval. A "
           "participant asks who may change the limit; the presenter answers that only account administrators can. The "
           "batch ends with the limit raised and the large order placed.")
DESCRIPTION = "The presenter opens the billing settings and points at the monthly limit."
FRAME = {**RECORD, "third_person_description": DESCRIPTION}
EMPTY_FRAME = {**EMPTY_RECORD, "third_person_description": DESCRIPTION}


def load_run_context(model=None,
                     count: int = 3,
                     speech: dict[int, str] | None = None) -> RunContext:
    """A run of `count` frames on disk, its store seeded and its transcript and frames files written; `speech` gives a
    frame other words than its four placeholder words."""

    tmp = Path(tempfile.mkdtemp())
    paths = fx.make_paths(tmp)
    run_dir = tmp / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(paths.repository), module=paths.module,
                                        output_root=str(paths.output_root)),
                            budgets={"maxAttempts": 2}, models=[])
    ctx = RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store, run_dir=run_dir,
                     log=logging.getLogger("test"), ignore=IgnoreList(),
                     model=model)
    items = []
    for index in range(count):
        frame = paths.output_root / "frames" / f"frame-{index:03d}.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(f"frame {index}".encode())
        items.append({"slug": f"frame-{index:03d}", "index": index, "timestamp": index * 6.0,
                      "path": f"frames/frame-{index:03d}.jpg", "transcriptBefore": "",
                      "transcriptCurrent": (speech or {}).get(index, f"words of frame {index}")})
    store.seed_items(items)
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"], {
        "segments": [{"text": " ".join(item["transcriptCurrent"] for item in items),
                      "start": 0.0, "end": count * 6.0, "words": []}]})
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})

    return ctx


def run_frames_processing(ctx: RunContext,
                          queue: list[str],
                          frames_per_call: int,
                          repair_calls: int = 1,
                          single_frame_attempts: int | None = None) -> dict:
    """Drive the frame loop to the end of the queue, as the graph's loop-back edge would."""

    work = app_config.settings.frames_processing
    previous, previous_repairs, previous_alone = work.frames_per_call, work.repair_calls, work.single_frame_attempts
    work.frames_per_call, work.repair_calls = frames_per_call, repair_calls
    # The scenarios ask an unfinished frame alone, whatever this machine's playbook.local.json sets
    if single_frame_attempts is not None or previous_alone == 0:
        work.single_frame_attempts = 1 if single_frame_attempts is None else single_frame_attempts
    state = {**graph_state.new_run_state(FRAMES_PROCESSING, FRAMES_PROCESSING), "queue": queue}
    written: list[str] = []
    try:
        while state.get("cursor", 0) < len(queue):
            update = process_frames_node.process_frames_node(state, {"configurable": {"ctx": ctx}})
            written += update.get("written", [])
            state.update(update)
    finally:
        work.frames_per_call, work.repair_calls, work.single_frame_attempts = previous, previous_repairs, previous_alone

    return {**state, "written": written}


def run_two_frame_batch(*script) -> RunContext:
    """Two frames run as one batch against the scripted answers, both critique switches on: the run a test of a
    batch's follow-ups counts the calls of."""

    ctx = load_run_context(fakes.scripted(*script), count=2)
    with fx.critique_switches():
        run_frames_processing(ctx, ["frame-000", "frame-001"], frames_per_call=2)

    return ctx


def build_group(*records,
                summary: str = SUMMARY,
                atmosphere: str = ATMOSPHERE):
    """A batch answer of these records with its account and its mood."""

    return fakes.frame_notes(frames=list(records), batch_summary=summary, atmosphere=atmosphere)
