# <!-- sync: seed/cases.test_model_sampling v1 -->
"""A role's sampling reaches the host as its maker recommends it, and a setting the provider cannot take is refused.

A client that sends only a temperature leaves top-p, top-k, min-p and the penalties to the host's defaults, which are
generic chat values. Measured 2026-09-27 on a 24-frame test (`qwen3.6-35b-a3b-mtp` on LM Studio): the host's defaults
scored 47% to 81% over seven runs, the model card's thinking set 72% to 84% over five. Each provider is sent only the
forms it accepts: an OpenAI-compatible host the OpenAI fields plus the rest in the request body. A setting a provider
cannot take raises, naming it, rather than being dropped. A provider the program once built a client of its own for
(Ollama, Anthropic) is refused by name, with how to reach it now.
"""

import argparse
import contextlib
import io

from langchain_core.messages import HumanMessage

from support import harness

import config
from cli import setup_commands
from config import ModelSettings
from models.run_errors import ModelUnavailable
from services import chat_model_service

CARD_SET = {"top_k": 20, "min_p": 0.0, "repeat_penalty": 1.0, "presence_penalty": 0.0}


def _read_sent_body(settings: ModelSettings) -> dict:
    """The request body an OpenAI-compatible host would receive for this role."""

    model = chat_model_service._build(settings)

    return model._get_request_payload([HumanMessage("hi")])


def test_an_openai_compatible_host_is_sent_the_whole_set():
    body = _read_sent_body(ModelSettings(spec="lmstudio:a-model", temperature=0.6, top_p=0.95, sampling=CARD_SET))
    harness.check("top_p and presence_penalty go as the OpenAI fields they are",
                  body.get("top_p") == 0.95 and body.get("presence_penalty") == 0.0, str(body))
    harness.check("top_k, min_p and repeat_penalty go in the request body the host reads",
                  body.get("extra_body") == {"top_k": 20, "min_p": 0.0, "repeat_penalty": 1.0}, str(body.get("extra_body")))
    harness.check("the temperature is kept", body.get("temperature") == 0.6, str(body.get("temperature")))


def test_a_role_without_sampling_sends_nothing_extra():
    body = _read_sent_body(ModelSettings(spec="lmstudio:a-model", temperature=0.6))
    harness.check("no top_p, and LM Studio is sent no extra body: how long it keeps a model is its own setting",
                  "top_p" not in body and not body.get("extra_body"), str(body))
    other =_read_sent_body(ModelSettings(spec="openai-compatible:a-model", temperature=0.6, host="lmstudio"))
    harness.check("another OpenAI-compatible host is sent no extra body", not other.get("extra_body"), str(other))


def test_no_model_is_asked_to_stay_loaded_or_to_unload_defect():
    # Every chat request carried LM Studio's `ttl` (24 hours, 5 minutes for phase 2's filter model): a field of LM
    # Studio alone, which another server ignores or refuses. Now no request of any model carries it, and the filter's
    # model is built like every other
    from services import frame_redundancy_service

    saved = dict(config.settings.models)
    config.settings.models.update({"small-vl": ModelSettings(spec="lmstudio:small-vl", temperature=0.1)})
    chat_model_service.get_named_model.cache_clear()
    try:
        small = frame_redundancy_service.build_filter_model("small-vl")._get_request_payload([HumanMessage("hi")])
    finally:
        config.settings.models.clear()
        config.settings.models.update(saved)
        chat_model_service.get_named_model.cache_clear()
    harness.check("the filter's model is sent no unload timer", not small.get("extra_body"), str(small))
    harness.check("the program names no load time any more", not hasattr(chat_model_service, "LMSTUDIO_LOAD_SECONDS"))


def test_a_provider_the_program_no_longer_builds_is_refused_naming_how_to_reach_it():
    for spec in ("ollama:a-model", "anthropic:a-model"):
        try:
            chat_model_service._build(ModelSettings(spec=spec))
            harness.check(f"{spec} is refused", False)
        except ModelUnavailable as refused:
            harness.check(f"{spec} is refused, naming the provider and the OpenAI-compatible way",
                          spec.split(":")[0] in str(refused) and "openai-compatible:a-model" in str(refused), str(refused))


def test_a_setting_the_provider_cannot_take_is_refused_by_name():
    for spec, key in (("lmstudio:a-model", "mirostat"), ("openai:a-model", "top_k"),
                      ("openai-compatible:a-model", "mirostat")):
        try:
            chat_model_service._build(ModelSettings(spec=spec, api_key="k", sampling={key: 1.0}))
            harness.check(f"{spec} with {key} is refused", False)
        except ModelUnavailable as refused:
            harness.check(f"{spec} with {key} is refused, naming the setting and the provider",
                          key in str(refused) and spec.split(":")[0] in str(refused), str(refused))


def test_the_models_command_shows_the_sampling():
    row = chat_model_service.describe_phase_models()[0]
    harness.check("each phase's row carries its model's name, top_p and the sampling set",
                  "name" in row and "top_p" in row and "sampling" in row, str(row))


def test_the_models_line_prints_the_sampling_when_set():
    """A setting written is only trusted once it is read back: `models` shows what will be sent."""

    phase = "frames_processing"
    saved = dict(config.settings.models)
    printed = io.StringIO()
    try:
        config.settings.models[config.PHASE_DEFAULT_MODELS[phase]] = ModelSettings(spec="lmstudio:a-model", temperature=0.6, top_p=0.95,
                                                     sampling={"top_k": 20, "min_p": 0.0})
        with contextlib.redirect_stdout(printed):
            setup_commands.cmd_models(argparse.Namespace())
    finally:
        config.settings.models.clear()
        config.settings.models.update(saved)
    line = next((text for text in printed.getvalue().splitlines() if text.strip().startswith(phase)), "")
    harness.check("the phase's line carries top_p and each sampling key",
                  "top_p=0.95" in line and "top_k=20" in line and "min_p=0" in line, printed.getvalue())



def test_the_request_timeout_of_a_model_reaches_every_provider_s_client():
    compatible = chat_model_service._build(ModelSettings(spec="lmstudio:a-model", request_timeout_s=123))
    openai = chat_model_service._build(ModelSettings(spec="openai:a-model", api_key="k", request_timeout_s=123))
    harness.check("the OpenAI-compatible client waits at most that long", compatible.request_timeout == 123,
                  str(compatible.request_timeout))
    harness.check("the OpenAI client waits at most that long", openai.request_timeout == 123, str(openai.request_timeout))


def test_a_misspelt_provider_is_named_as_the_cause_even_when_the_model_sets_sampling():
    try:
        chat_model_service._build(ModelSettings(spec="ollma:a-model", sampling={"top_k": 20}))
        outcome = "built"
    except ModelUnavailable as refused:
        outcome = str(refused)
    harness.check("the refusal says the provider is unknown, not that its sampling cannot be sent",
                  "unknown provider" in outcome and "ollma" in outcome, outcome)


def test_a_misspelt_provider_is_refused_by_name_when_the_model_sets_its_structured_method_too():
    cfg = ModelSettings(spec="ollma:a-model", structured_method="function_calling")
    try:
        chat_model_service._pick_structured_method(cfg)
        outcome = "picked"
    except ModelUnavailable as refused:
        outcome = str(refused)
    harness.check("the method is not picked for a provider nothing can build",
                  "unknown provider" in outcome and "ollma" in outcome, outcome)
