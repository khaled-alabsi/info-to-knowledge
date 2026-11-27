"""A settings file's values MERGE over the defaults in code, and a key the settings do not know is refused. These tests
build the settings from a dict, as a file's contents; the order the files are read in is test_settings_files.py's.

Every value below differs from its default on purpose: an overlay test written with a default value
passes whether or not the overlay works. The archived program had the other behaviour, a user file
that REPLACED the packaged one, so a key missing from the user file was simply gone.
"""

from pydantic import ValidationError

from support import harness

import config

OVERLAY = {
    "frames": {"mode": "scene", "sceneThreshold": 0.17, "minSceneSeconds": 2.5, "maxCount": 41, "perMinute": 9},
    "audio": {"whisperModel": "medium", "language": "de", "fasterWhisper": {"beamSize": 3}},
    "prompt": {"userPrompt": "What is said about fuel prices?", "transcriptBeforeSeconds": 45},
    "models": {"document": {"spec": "lmstudio:qwen3-30b", "temperature": 0.2}},
    "budgets": {"maxAttempts": 2},
}


def _is_refused(overlay: dict,
                *words: str) -> bool:
    try:
        config.Settings(**overlay)
    except ValidationError as exc:
        return all(word in str(exc) for word in words)

    return False


def test_every_overlaid_value_binds_and_every_other_default_survives():
    settings = config.Settings(**OVERLAY)
    harness.check("frames.mode binds", settings.frames.selection_mode == "scene")
    harness.check("a camelCase frames key binds", (settings.frames.scene_threshold, settings.frames.min_scene_seconds,
                                                  settings.frames.max_count, settings.frames.per_minute)
                  == (0.17, 2.5, 41, 9), str(settings.frames))
    harness.check("audio binds", (settings.audio.whisper_model, settings.audio.language,
                                 settings.audio.faster_whisper.beam_size)
                  == ("medium", "de", 3), str(settings.audio))
    harness.check("the user prompt and the transcript cap bind",
                  (settings.prompt.user_prompt, settings.prompt.transcript_before_seconds)
                  == ("What is said about fuel prices?", 45), str(settings.prompt))
    harness.check("one configured model is swapped with no code edit",
                  settings.resolve_model("document").spec == "lmstudio:qwen3-30b")
    harness.check("a model the overlay does not name keeps the default model",
                  settings.resolve_model("frame").spec == config.Settings().resolve_model("frame").spec)
    harness.check("a budget binds", settings.budgets.max_attempts == 2)
    harness.check("a key the overlay does not name keeps its default: the VAD silence",
                  settings.audio.faster_whisper.vad_min_silence_ms == 500)
    harness.check("and the device", settings.audio.faster_whisper.device == "cpu")
    harness.check("and the engine, so an existing run does not change backend",
                  settings.audio.engine == "faster-whisper", settings.audio.engine)


def test_the_defaults_are_the_ones_the_requirements_state():
    settings = config.Settings()
    harness.check("interval stays the default frame mode", settings.frames.selection_mode == "interval")
    harness.check("the default frame model is local", settings.resolve_model("frame").spec.startswith("lmstudio:"))
    harness.check("the frame model keeps the model's thinking, so a thinking-only reply can be read",
                  settings.resolve_model("frame").reasoning is True)
    harness.check("phase 3 calls the frame model and every other model phase the document model",
                  {phase: settings.resolve_phase_model_name(phase) for phase in config.PHASE_DEFAULT_MODELS}
                  == {"frames_preparing": "document", "frames_processing": "frame", "finalize": "document",
                      "review": "document", "critique_collection": "document", "document_reshaping": "document"})


def test_a_phase_calls_the_configured_model_phase_models_names():
    models = {"frame": {"spec": "lmstudio:big"}, "document": {"spec": "lmstudio:big"},
              "small": {"spec": "lmstudio:small-vl"}}
    settings = config.Settings(models=models, phase_models={"frames_processing": "small", "review": "small"})
    harness.check("each named phase resolves to the named model, every other phase to its default",
                  {phase: settings.resolve_phase_model(phase).spec for phase in config.PHASE_DEFAULT_MODELS}
                  == {"frames_preparing": "lmstudio:big", "frames_processing": "lmstudio:small-vl",
                      "finalize": "lmstudio:big", "review": "lmstudio:small-vl",
                      "critique_collection": "lmstudio:big", "document_reshaping": "lmstudio:big"})
    harness.check("a phase that calls no model is refused by name", _is_refused(
        {"models": models, "phaseModels": {"transcribe": "small"}}, "transcribe"))
    unconfigured = config.Settings(models=models, phase_models={"finalize": "missing"})
    try:
        unconfigured.resolve_phase_model("finalize")
        harness.check("a name no model is configured under is refused", False)
    except ValueError as refused:
        harness.check("a name no model is configured under is refused by name, never the default model",
                      "missing" in str(refused) and "small" in str(refused), str(refused))
    # An empty name is what the Settings page writes for a phase left on its default, so it reads as that default
    empty = config.Settings(models=models, phase_models={"review": ""})
    harness.check("an empty name reads as the phase's default model, as the Settings page writes it",
                  empty.resolve_phase_model_name("review") == config.PHASE_DEFAULT_MODELS["review"],
                  empty.resolve_phase_model_name("review"))


def test_phase_3_takes_its_models_own_frames_a_call_when_the_entry_sets_one():
    models = {"frame": {"spec": "lmstudio:big"}, "small": {"spec": "lmstudio:small-vl", "framesPerCall": 5}}
    on_big = config.Settings(models=models, frames_processing={"framesPerCall": 8})
    on_small = config.Settings(models=models, frames_processing={"framesPerCall": 8},
                               phase_models={"frames_processing": "small"})
    harness.check("a model with no frames a call of its own takes framesProcessing.framesPerCall",
                  on_big.resolve_frames_per_call() == 8, str(on_big.resolve_frames_per_call()))
    harness.check("a model that sets one takes its own while it runs phase 3",
                  on_small.resolve_frames_per_call() == 5, str(on_small.resolve_frames_per_call()))
    harness.check("a model's frames a call of 0 is refused",
                  _is_refused({"models": {"small": {"spec": "lmstudio:small-vl", "framesPerCall": 0}}}, "framesPerCall"))


def test_an_unknown_key_is_refused_at_every_level():
    harness.check("an unknown top-level key is refused by name", _is_refused({"framez": {}}, "framez"))
    harness.check("an unknown nested key is refused by name",
                  _is_refused({"frames": {"sceneTreshold": 0.2}}, "sceneTreshold"))


def test_a_frame_mode_that_does_not_exist_is_refused():
    harness.check("an unknown mode is refused", _is_refused({"frames": {"mode": "keyframes"}}, "mode"))


def test_mps_is_refused_with_the_reason():
    harness.check("`device: mps` is refused naming the device, the cause and the working value",
                  _is_refused({"audio": {"fasterWhisper": {"device": "mps"}}}, "mps", "CTranslate2", "cpu"))
    harness.check("`device: cpu` is accepted",
                  config.Settings(audio={"fasterWhisper": {"device": "cpu"}}).audio.faster_whisper.device == "cpu")
    harness.check("the device is not a top-level audio key any more, so a stale overlay is refused",
                  _is_refused({"audio": {"device": "cpu"}}, "device"))


def test_a_degenerate_frame_setting_is_refused_not_looped_on():
    harness.check("a zero frame budget is refused, naming the key as it was typed",
                  _is_refused({"frames": {"maxCount": 0}}, "maxCount"))
    harness.check("a zero sampling rate is refused", _is_refused({"frames": {"perMinute": 0}}, "perMinute"))
    harness.check("a threshold outside 0..1 is refused",
                  _is_refused({"frames": {"sceneThreshold": 1.5}}, "sceneThreshold"))
