"""A knowledge part run through `document_knowledge_service.run_batch` on a made document (every word invented), with the
run's real control file: the page's Stop, written there while the part works, ends it between two calls with its
conversation saved, and a second start goes on from that turn. Phase 7 read no signal, so Stop and Pause did nothing."""

import logging
import tempfile
from pathlib import Path

from agent_client.agent_errors import ControlStop
from support import fakes, harness

from models.run_paths import RunPaths
from services import control_signal_service
from services import document_folder_service
from services import document_knowledge_service
from services import document_reshaping_service

DOCUMENT = "# Gateway guide\n\nThe router picks the ledger shard from the tenant id.\n"


def _start_part() -> tuple[RunPaths, dict, Path]:
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True)
    document.write_text(DOCUMENT, encoding="utf-8")
    run_dir = folder / "logs" / "run-1"
    run_dir.mkdir(parents=True)

    return paths, document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""}), run_dir


def test_the_page_s_stop_ends_a_phase_7_part_between_two_calls_and_a_resume_goes_on_from_there_defect():
    # Defect: the page's Stop ("stop at the next check") was written into the run's control file and phase 7 never read
    # it, so a knowledge pass ran on for hours. Fix: each part reads it before every call and ends with its turn saved
    paths, state, run_dir = _start_part()
    write = fakes.tool_call("write_sections", edits=[{"action": "new_section", "heading": "Shards",
                                                      "text": "The router picks the ledger shard from the tenant id."}])
    model = fakes.scripted(write, fakes.tool_call("finish_batch", summary="shards written"))

    def check_and_stop_after_first_call() -> str | None:
        # the person presses Stop on the page while the part's first call runs
        if len(model.calls) == 1:
            control_signal_service.request(run_dir, control_signal_service.STOP, by="ui")
        return control_signal_service.check(run_dir)

    try:
        document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=model, log=logging.getLogger("test"),
                                             control_check=check_and_stop_after_first_call)
        stopped = ""
    except ControlStop as stop:
        stopped = str(stop)
    harness.check("the stop ends the part before its second call", stopped == "stopped before call 2 of part 1", stopped)
    harness.check("the model was called once", len(model.calls) == 1, str(len(model.calls)))
    saved = document_reshaping_service.read_conversation(paths, 1)
    harness.check("the first turn's write is saved", saved is not None
                  and document_reshaping_service.count_calls(saved[0]) == 1, str(saved and saved[0]))

    control_signal_service.clear(run_dir)
    resumed = fakes.scripted(fakes.tool_call("finish_batch", summary="shards written"))
    is_closed, _, _ = document_knowledge_service.run_batch(paths=paths, state=state, number=1, model=resumed,
                                                           log=logging.getLogger("test"),
                                                           control_check=lambda: control_signal_service.check(run_dir))
    harness.check("a second start goes on from the saved turn and closes the part", is_closed
                  and document_reshaping_service.count_calls(resumed.calls[0]) == 1, str(resumed.calls))
