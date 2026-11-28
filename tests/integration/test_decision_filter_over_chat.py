"""Phase 2's knowledge filter with a chat model chosen in the Settings page's Decision filter menu
(`providers.decision.libraryModel` naming a model of `models` on a host that lists only the `chat` kind): rendered
scenes (a slide, a call gallery) and a made blank frame go through the code phase 2 runs (`read_decisions`, then
`apply_decisions` at the setting's cut, then `knowledge_filter_service.filter_frames`). A stand-in chat server answers
each frame and question with a number, as a filter model would; the slide is kept and the two empty frames dropped,
each naming the kind of empty frame it scored highest."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PIL import Image

import config
from support import fake_model_server
from support import harness
from services import decision_filter_service, knowledge_filter_service

SCENES = Path(__file__).resolve().parents[1] / "resources" / "scenes"
CHAT_PATH = "/v1/chat/completions"
MODEL = "test-filter"
# Each frame's answers by question, as a filter model would give them
SCORES = {
    "frame-001.jpg": {"knowledge": 0.93, "people": 0.02, "desktop": 0.03, "blank": 0.01, "joining": 0.02},
    "frame-002.jpg": {"knowledge": 0.06, "people": 0.91, "desktop": 0.04, "blank": 0.02, "joining": 0.3},
    "frame-003.jpg": {"knowledge": 0.01, "people": 0.03, "desktop": 0.2, "blank": 0.97, "joining": 0.05},
}


def _build_frames(frames_dir: Path) -> None:
    """The slide scene, the call gallery scene and a flat white frame, saved as the run's frames 1 to 3."""

    Image.open(SCENES / "held-slide.png").convert("RGB").save(frames_dir / "frame-001.jpg", quality=95)
    Image.open(SCENES / "call-gallery.png").convert("RGB").save(frames_dir / "frame-002.jpg", quality=95)
    Image.new("RGB", (1280, 720), (255, 255, 255)).save(frames_dir / "frame-003.jpg", quality=95)


def test_a_chat_model_chosen_in_the_decision_filter_menu_drops_the_empty_frames_and_keeps_the_slide_defect():
    # The Decision filter menu offered only the program's own way, never the filter model a chat host serves.
    # Now that model answers the questions over chat, and phase 2 keeps and drops frames by its numbers.
    folder = Path(tempfile.mkdtemp())
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    _build_frames(frames_dir)
    url_to_frame = {decision_filter_service._build_frame_data_url(frame_path=path): path.name
                    for path in sorted(frames_dir.glob("frame-*.jpg"))}

    def answer(request: fake_model_server.RecordedRequest) -> dict:
        content = request.read_json()["messages"][0]["content"]
        frame = url_to_frame[next(part["image_url"]["url"] for part in content if part["type"] == "image_url")]
        text = next(part["text"] for part in content if part["type"] == "text")
        name = next(name for name, question in decision_filter_service.QUESTIONS.items() if text.startswith(question))
        return {"choices": [{"message": {"role": "assistant", "content": f"{SCORES[frame][name]}"}}]}

    route = ("POST", CHAT_PATH)
    kept_route = fake_model_server.ROUTE_DICT[route]
    fake_model_server.ROUTE_DICT[route] = answer
    kept_settings = config.settings
    server = fake_model_server.FakeModelServer()
    try:
        with fake_model_server.serving(server):
            config.settings = config.Settings(**{
                "hosts": {fake_model_server.TEST_HOST: {"url": server.build_url(), "kinds": ["chat"]}},
                "models": {MODEL: {"spec": f"{fake_model_server.TEST_HOST}:qwen-filter", "suits": ["filtering"]}},
                "providers": {"decision": {"libraryModel": MODEL}}})
            settings = config.settings.frames
            decisions = decision_filter_service.read_decisions(frames_dir, settings)
            items = [{"slug": name, "index": index, "frameNumber": index, "timestamp": float(index),
                      "path": f"frames/{name}"} for index, name in enumerate(sorted(SCORES), start=1)]
            tags = {name: {"frame": name, "tag": "other", "peopleScore": 0.0, "boardScore": 0.0} for name in SCORES}
            decided = decision_filter_service.apply_decisions(tags, decisions, settings.decision_filter_cut)
            kept, dropped = knowledge_filter_service.filter_frames(items, frames_dir, decided)
    finally:
        config.settings = kept_settings
        fake_model_server.ROUTE_DICT[route] = kept_route
    sent_models = {request.read_json().get("model") for request in server.request_list if request.path == CHAT_PATH}
    shutil.rmtree(folder)
    harness.check("the slide is kept", [item["slug"] for item in kept] == ["frame-001.jpg"], str(kept))
    harness.check("the call gallery is dropped as people and the blank frame as blank",
                  {entry["slug"]: entry["dropClass"] for entry in dropped}
                  == {"frame-002.jpg": "people", "frame-003.jpg": "blank"}, str(dropped))
    harness.check("every call names the model by its name on the host", sent_models == {"qwen-filter"}, str(sent_models))
