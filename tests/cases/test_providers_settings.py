"""The `providers` settings: one server per kind of model, every URL empty until a person sets it, read from the settings
files and from `PB_PROVIDERS__...` environment variables, and a wrong key refused by its name."""

from __future__ import annotations

import os

from support import harness

import config

KIND_LIST = ["chat", "speech", "diarization", "embeddings", "ocr", "detection", "decision"]
TIMEOUT_S_BY_KIND = {"chat": 600, "speech": 3600, "diarization": 3600, "embeddings": 600, "ocr": 600, "detection": 120,
                     "decision": 600}


def _find_refusal(settings_values: dict) -> str:
    """Give me what `Settings` says when it refuses `settings_values`; "" when it accepts them."""

    try:
        config.Settings(**settings_values)
    except ValueError as refused:
        return str(refused)

    return ""


def test_every_kind_of_model_has_a_provider_that_is_empty_until_a_person_sets_it():
    providers = config.Settings().providers
    harness.check("the kinds are the seven the program calls", list(type(providers).model_fields) == KIND_LIST,
                  str(list(type(providers).model_fields)))
    for kind in KIND_LIST:
        provider = getattr(providers, kind)
        harness.check(f"{kind} has no URL, so the program keeps its own way", provider.url == "", provider.url)
        harness.check(f"{kind} has no key variable", provider.api_key_env == "", provider.api_key_env)
        harness.check(f"{kind} waits {TIMEOUT_S_BY_KIND[kind]} seconds for one request",
                      provider.timeout_s == TIMEOUT_S_BY_KIND[kind], str(provider.timeout_s))
    harness.check("chat has no model name: each phase names its own", not hasattr(providers.chat, "model"))
    for kind in KIND_LIST[1:]:
        harness.check(f"{kind} has a model name that is empty", getattr(providers, kind).model == "")


def test_the_shipped_default_file_leaves_every_url_empty():
    no_local_file = config.DEFAULT_CONFIG_FILE.with_name("playbook.no-such-local.json")
    shipped = config.Settings(**config.read_settings_files(config.DEFAULT_CONFIG_FILE, no_local_file))

    for kind in KIND_LIST:
        harness.check(f"the default file sets no URL for {kind}", getattr(shipped.providers, kind).url == "")


def test_a_provider_is_set_from_a_settings_file_and_from_the_environment():
    from_file = config.Settings(**{"providers": {"speech": {"url": "http://host:8791/v1", "apiKeyEnv": "I2K_KEY",
                                                           "model": "whisper-large", "timeoutS": 30}}})
    speech = from_file.providers.speech
    harness.check("the speech provider holds the file's four values",
                  (speech.url, speech.api_key_env, speech.model, speech.timeout_s) == ("http://host:8791/v1", "I2K_KEY",
                                                                                      "whisper-large", 30), str(speech))
    harness.check("a kind the file leaves out keeps its defaults", from_file.providers.ocr.url == ""
                  and from_file.providers.detection.timeout_s == 120)
    os.environ["PB_PROVIDERS__SPEECH__URL"] = "http://env:9/v1"
    try:
        from_environment = config.Settings()
    finally:
        del os.environ["PB_PROVIDERS__SPEECH__URL"]
    harness.check("the environment variable PB_PROVIDERS__SPEECH__URL sets the speech URL",
                  from_environment.providers.speech.url == "http://env:9/v1", from_environment.providers.speech.url)
    harness.check("the environment leaves the other kinds empty", from_environment.providers.chat.url == "")


def test_a_wrong_key_under_providers_is_refused_by_its_name():
    harness.check("a misspelled kind is refused by its name",
                  "speach" in _find_refusal({"providers": {"speach": {"url": "http://x/v1"}}}))
    harness.check("a misspelled field is refused by its name",
                  "urll" in _find_refusal({"providers": {"speech": {"urll": "http://x/v1"}}}))
    harness.check("a model name under chat is refused: each phase names its own",
                  "model" in _find_refusal({"providers": {"chat": {"url": "http://x/v1", "model": "qwen"}}}))
    harness.check("a timeout of zero is refused by its name",
                  "timeoutS" in _find_refusal({"providers": {"speech": {"timeoutS": 0}}})
                  or "timeout_s" in _find_refusal({"providers": {"speech": {"timeoutS": 0}}}))
    harness.check("an accepted block gives no refusal", _find_refusal({"providers": {"speech": {"url": "http://x/v1"}}}) == "")
