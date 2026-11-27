"""Phase 2's knowledge filter decided by the decision model d1-2B (`frames.knowledgeFilterModel` "liquidai-d1-2B"): its answers turned into the classifier's tags, the run's choice kept in its
folder, and a frame it drops naming the kind of empty frame it is."""

import tempfile
from pathlib import Path

from support import harness

from services import decision_filter_service, knowledge_filter_service

_TAGS = {
    "frame-001.jpg": {"frame": "frame-001.jpg", "tag": "text", "peopleScore": 0.10, "boardScore": 0.0},
    "frame-002.jpg": {"frame": "frame-002.jpg", "tag": "other", "peopleScore": 0.30, "boardScore": 0.0},
    "frame-003.jpg": {"frame": "frame-003.jpg", "tag": "people", "peopleScore": 0.90, "boardScore": 0.0},
}
_DECISIONS = {
    "frame-001.jpg": {"frame": "frame-001.jpg", "pKnowledge": 0.93, "classes": {"people": 0.01, "blank": 0.0}},
    "frame-002.jpg": {"frame": "frame-002.jpg", "pKnowledge": 0.08, "classes": {"people": 0.2, "desktop": 0.7}},
    "frame-003.jpg": {"frame": "frame-003.jpg", "pKnowledge": 0.55, "classes": {"people": 0.6, "desktop": 0.0}},
}


def test_the_decision_model_decides_which_frames_hold_no_knowledge():
    decided = decision_filter_service.apply_decisions(_TAGS, _DECISIONS, 0.20)
    harness.check("a frame under the cut is tagged people, with the empty frame's class it scored highest",
                  decided["frame-002.jpg"]["tag"] == knowledge_filter_service.PEOPLE
                  and decided["frame-002.jpg"]["dropClass"] == "desktop", str(decided["frame-002.jpg"]))
    harness.check("a frame the classifier tagged people that d1-2B finds knowledge on is kept, tagged other",
                  decided["frame-003.jpg"]["tag"] == "other", str(decided["frame-003.jpg"]))
    harness.check("a kept frame keeps the classifier's tag", decided["frame-001.jpg"]["tag"] == "text",
                  str(decided["frame-001.jpg"]))


def test_a_frame_the_decision_model_returned_nothing_for_stops_phase_2():
    try:
        decision_filter_service.apply_decisions(_TAGS, {"frame-001.jpg": _DECISIONS["frame-001.jpg"]}, 0.20)
    except RuntimeError as error:
        harness.check("refused by the frame's name", "frame-002.jpg" in str(error), str(error))
        return
    harness.check("refused", False)


def test_a_frame_the_decision_model_drops_names_its_class_and_score():
    frames_dir = Path(tempfile.mkdtemp())
    items = []
    for number in (1, 2, 3):
        (frames_dir / f"frame-00{number}.jpg").write_bytes(b"jpg")
        items.append({"slug": f"frame-00{number}", "index": number, "frameNumber": number * 30,
                      "timestamp": float(number), "path": f"frames/frame-00{number}.jpg"})
    decided = decision_filter_service.apply_decisions(_TAGS, _DECISIONS, 0.20)
    kept, dropped = knowledge_filter_service.filter_frames(items, frames_dir, decided)
    harness.check("only the frame under the cut is dropped", [entry["slug"] for entry in dropped] == ["frame-002"],
                  str(dropped))
    harness.check("its entry names the class and the share of no knowledge as its drop score",
                  dropped[0]["dropClass"] == "desktop" and dropped[0]["dropScore"] == 0.92, str(dropped[0]))
    harness.check("the frame the classifier would have dropped is kept", [item["slug"] for item in kept] == ["frame-001", "frame-003"])


def test_the_run_line_and_the_queue_carry_the_knowledge_filter_model():
    from cli import build_parser
    from models.run_job import RunJobRequest
    from services import job_queue_service

    args = build_parser().parse_args(["run", "--video", "acmesync-demo.mp4", "--output", "out",
                                      "--knowledge-filter-model", "liquidai-d1-2B"])
    harness.check("the run line takes the model by its full name", args.knowledge_filter_model == "liquidai-d1-2B",
                  str(args.knowledge_filter_model))
    request = RunJobRequest(video=Path("/inbox/acmesync-demo.mp4"), output_root=Path("/output/acmesync-demo"),
                            knowledge_filter_model="liquidai-d1-2B")
    command = job_queue_service.build_run_command(request)
    harness.check("the queued line carries it", "--knowledge-filter-model" in command
                  and command[command.index("--knowledge-filter-model") + 1] == "liquidai-d1-2B", str(command))
    harness.check("the request keeps it through its JSON form",
                  RunJobRequest.from_dict(request.as_dict()).knowledge_filter_model == "liquidai-d1-2B")


def test_the_run_s_knowledge_filter_choice_is_kept_in_its_folder():
    folder = Path(tempfile.mkdtemp())
    harness.check("no choice reads the settings' model",
                  decision_filter_service.read_chosen_model(folder) == decision_filter_service.CLASSIFIER_MODEL)
    decision_filter_service.write_chosen_model(folder, decision_filter_service.DECISION_MODEL)
    harness.check("a written choice is read back",
                  decision_filter_service.read_chosen_model(folder) == decision_filter_service.DECISION_MODEL)
    try:
        decision_filter_service.write_chosen_model(folder, "d1-2b")
    except ValueError as error:
        harness.check("a model not in the list is refused by name", "liquidai-d1-2B" in str(error), str(error))
        return
    harness.check("refused", False)
