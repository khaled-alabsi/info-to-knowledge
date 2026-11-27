"""The run's cost line, and the check that a role's model can do what the role needs."""

import json
import tempfile
from pathlib import Path

from support import harness
from support import fake_model_server

import config
from models.pipeline_errors import MissingModelCapability
from services import call_log_service
from services import model_capability_service

ROWS = [
    {"phase": "frames_processing", "slug": "item-000", "attempt": 1, "promptTokens": 1200, "outputTokens": 300,
     "promptDurationS": 2.0, "outputDurationS": 6.0, "durationS": 8.4, "finishReason": "stop"},
    {"phase": "frames_processing", "slug": "item-001", "attempt": 1, "promptTokens": 1800, "outputTokens": 320,
     "promptDurationS": 3.0, "outputDurationS": 6.4, "durationS": 9.9, "finishReason": "length"},
]


def test_metrics_rows_survive_a_line_a_kill_cut_in_half():
    run_dir = Path(tempfile.mkdtemp())
    for row in ROWS:
        call_log_service.append_call_metrics(run_dir, row)
    with (run_dir / call_log_service.METRICS_FILE).open("a", encoding="utf-8") as handle:
        handle.write('{"phase": "frames_processing", "slug": "item-0')
    harness.check("every complete row reads back and the torn one is dropped",
                  call_log_service.read_call_metrics(run_dir) == ROWS)


def test_the_exit_line_sums_this_process_calls_in_one_line():
    """The exit printed every call of the run since its first start, about 240 lines of it, with two columns that were
    always empty (the owner, 2026-09-30: "it look like spam"). One line of this process's calls, and the file."""

    line = call_log_service.render_metrics_line(ROWS, "output/run/logs/calls.jsonl")
    harness.check("the count, the tokens, the minutes and the file, in one line",
                  line == "calls this resume: 2 · 3,000 prompt + 620 output tokens · 0.3 min · table in "
                          "output/run/logs/calls.jsonl", line)


def test_no_calls_says_so():
    harness.check("a process that made no call says so",
                  call_log_service.render_metrics_line([], "x") == "calls this resume: none")


def test_the_missing_capabilities_are_the_ones_the_host_does_not_list():
    harness.check("a model with everything lacks nothing",
                  model_capability_service.list_missing_capabilities(("vision", "tools"),
                                                                    ["completion", "vision", "tools"]) == [])
    harness.check("a vision model with no tool calling lacks tools",
                  model_capability_service.list_missing_capabilities(("vision", "tools"),
                                                                    ["completion", "vision"]) == ["tools"])


def _read_vision_only(base_url: str,
                      model: str) -> list[str]:
    return ["completion", "vision"]


def _read_vision_and_tools(base_url: str,
                           model: str) -> list[str]:
    return ["completion", "vision", "tools"]


def test_a_phase_whose_model_lacks_a_capability_is_refused_by_name():
    # The first phase checked: a reader answers for every phase on its provider, so each phase's model is the fixture
    phase = next(iter(model_capability_service.REQUIRED_CAPABILITIES))
    previous_required = dict(model_capability_service.REQUIRED_CAPABILITIES)
    previous_models = dict(config.settings.models)
    previous_reader = model_capability_service.set_capability_reader("lmstudio", _read_vision_only)
    try:
        for name in set(config.PHASE_DEFAULT_MODELS.values()):
            config.settings.models[name] = config.ModelSettings(spec="lmstudio:fixture-vision:1b")
        model_capability_service.REQUIRED_CAPABILITIES[phase] = ("vision", "tools")
        try:
            model_capability_service.check_phase_capabilities()
        except MissingModelCapability as exc:
            harness.check("the refusal names the phase", phase in str(exc), str(exc))
            harness.check("the model", "fixture-vision:1b" in str(exc), str(exc))
            harness.check("and what it lacks", "lacks tools" in str(exc), str(exc))
        else:
            harness.check("a model lacking a capability is refused", False)

        # The other side: the same phase on a model that lists both is reported, not refused.
        model_capability_service.set_capability_reader("lmstudio", _read_vision_and_tools)
        lines = model_capability_service.check_phase_capabilities()
        harness.check("a capable model is reported in a line of its own",
                      any(line.startswith(f"{phase}:") and "has vision, tools" in line for line in lines), str(lines))
    finally:
        model_capability_service.set_capability_reader("lmstudio", previous_reader)
        model_capability_service.REQUIRED_CAPABILITIES.clear()
        model_capability_service.REQUIRED_CAPABILITIES.update(previous_required)
        config.settings.models.clear()
        config.settings.models.update(previous_models)


def _check_with_models_served(served_model: str,
                              listed: str):
    """The lines `check_phase_capabilities` gives (or the refusal it raises) when every phase's model is `served_model`
    on a stand-in server whose model list holds `listed`."""

    server = fake_model_server.FakeModelServer()
    server.set_reply("/v1/models", 200, json.dumps({"object": "list", "data": [{"id": listed, "object": "model"}]}).encode("utf-8"))
    previous_models = dict(config.settings.models)
    previous_hosts = dict(config.settings.hosts)
    previous_phase_models = config.settings.phase_models
    try:
        config.settings.hosts["fixture-server"] = config.HostSettings(url=server.build_url())
        for name in set(config.PHASE_DEFAULT_MODELS.values()):
            config.settings.models[name] = config.ModelSettings(spec=f"openai-compatible:{served_model}",
                                                                host="fixture-server")
        config.settings.phase_models = {}
        with fake_model_server.serving(server):
            try:
                return model_capability_service.check_phase_capabilities()
            except MissingModelCapability as refused:
                return refused
    finally:
        config.settings.phase_models = previous_phase_models
        config.settings.models.clear()
        config.settings.models.update(previous_models)
        config.settings.hosts.clear()
        config.settings.hosts.update(previous_hosts)


def test_the_phases_whose_server_reports_no_capabilities_share_one_line_naming_them():
    lines = _check_with_models_served("fixture-vision:1b", "fixture-vision:1b")
    harness.check("one line, naming every phase in order",
                  lines == ["capabilities not checked, the server lists the model and reports no capabilities: "
                            + ", ".join(config.PHASE_DEFAULT_MODELS)], str(lines))


def test_a_phase_whose_model_the_server_does_not_list_is_refused_by_name_defect():
    # Defect: a run named a model its server did not have, and failed at its first call, after the audio was extracted.
    # Fix: the start of the run asks the server's model list and names the phase, the model and what the server lists
    refused = _check_with_models_served("fixture-vision:1b", "another-model")
    harness.check("the refusal names the model, the phase and what the server lists",
                  isinstance(refused, MissingModelCapability) and "fixture-vision:1b" in str(refused)
                  and "another-model" in str(refused) and "phase" in str(refused), str(refused))


def test_the_shipped_table_declares_every_model_phase_and_only_those():
    declared = set(model_capability_service.REQUIRED_CAPABILITIES)
    harness.check("every phase that calls a model is held to its capabilities, and no other",
                  declared == set(config.PHASE_DEFAULT_MODELS), str(declared))


def test_a_row_appended_after_a_line_a_kill_cut_in_half_is_read():
    run_dir = Path(tempfile.mkdtemp())
    (run_dir / call_log_service.METRICS_FILE).write_text('{"call": 1}\n{"call": 2, "tok', encoding="utf-8")
    call_log_service.append_call_metrics(run_dir, {"call": 3})
    rows = call_log_service.read_call_metrics(run_dir)
    harness.check("the row written after the torn line survives", {"call": 3} in rows, str(rows))
