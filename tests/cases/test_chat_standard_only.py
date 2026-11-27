"""Chat reaches its model through the standard OpenAI-compatible requests only. REQUIREMENTS.md R12.

The server is the stand-in server on port 0. A chat model whose settings name no address of its own calls the address
`providers.chat.url` holds; the program asks that server nothing but `GET /v1/models` and `POST /v1/chat/completions`,
and it never asks a host to load, unload or keep a model.
"""

import ast
import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

from support import harness
from support import fake_model_server

import config
from models.run_errors import ModelUnavailable

from services import chat_model_service
from services import host_window_service

SRC = Path(__file__).resolve().parents[2] / "src"
CALLED_MODEL = "stand-in-chat-model"
STANDARD_PATHS = {"/v1/models", "/v1/chat/completions"}
# The request fields a host reads to keep a model loaded or to unload it after a time
KEEP_ALIVE_FIELDS = ("ttl", "keep_alive")


@contextlib.contextmanager
def _chat_url_set(server: fake_model_server.FakeModelServer) -> Iterator[None]:
    """`providers.chat.url` pointed at `server` for the block."""

    previous_url = config.settings.providers.chat.url
    config.settings.providers.chat.url = server.build_url()
    try:
        with fake_model_server.serving(server):
            yield
    finally:
        config.settings.providers.chat.url = previous_url


def _build_model_name(provider: str,
                      case: str) -> str:
    """A name in `models` for a model on `provider` with no address of its own. The program builds a model once per name,
    with the address it had then, so each case that serves its own address takes its own name."""

    name = f"chat-standard-only-{case}-{provider}"
    config.settings.models[name] = config.ModelSettings(spec=f"{provider}:{CALLED_MODEL}")

    return name


def test_a_model_with_no_address_of_its_own_calls_the_chat_url_of_the_settings():
    server = fake_model_server.FakeModelServer()
    saved_models = dict(config.settings.models)
    try:
        with _chat_url_set(server):
            for provider in ("openai-compatible", "lmstudio"):
                name = _build_model_name(provider, "address")
                reply = chat_model_service.get_named_model(name).invoke("hello")
                harness.check(f"a {provider} model answers from the server `providers.chat.url` names",
                              reply.content == "stand-in text", str(reply.content))
        paths = [(request.method, request.path) for request in server.request_list]
        harness.check("every chat call is one POST to the standard chat route",
                      paths == [("POST", "/v1/chat/completions")] * 2, str(paths))
    finally:
        config.settings.models.clear()
        config.settings.models.update(saved_models)


def test_the_program_asks_a_chat_server_only_the_standard_requests():
    server = fake_model_server.FakeModelServer()
    saved_models = dict(config.settings.models)
    try:
        name = _build_model_name("openai-compatible", "requests")
        with _chat_url_set(server):
            window = host_window_service.read_loaded_window(server.build_url(), CALLED_MODEL)
            chat_model_service.get_named_model(name).invoke("hello")
        paths = {request.path for request in server.request_list}
        harness.check("a server that reports no window gives none", window is None, str(window))
        harness.check("only the model list and the chat route were asked", paths <= STANDARD_PATHS, str(paths))
        bodies = [request.read_json() for request in server.request_list if request.method == "POST"]
        sent_fields = {field for body in bodies for field in body if field in KEEP_ALIVE_FIELDS}
        harness.check("no request asks the host to keep or free the model", not sent_fields, str(sent_fields))
    finally:
        config.settings.models.clear()
        config.settings.models.update(saved_models)


def test_a_provider_the_program_no_longer_builds_is_refused_with_the_way_out():
    for provider in ("ollama", "anthropic"):
        try:
            chat_model_service.get_named_model(_build_model_name(provider, "retired"))
        except ModelUnavailable as refused:
            harness.check(f"{provider} is refused and the refusal names the address-based model to use",
                          f"openai-compatible:{CALLED_MODEL}" in str(refused) and provider in str(refused), str(refused))
        else:
            harness.check(f"{provider} is refused", False)
        finally:
            config.settings.models.pop(f"chat-standard-only-retired-{provider}", None)


def _read_imported_names(path: Path) -> set[str]:
    """Every module a file imports, by its first name (`langchain_ollama` for `from langchain_ollama import X`)."""

    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    return imported


def test_no_chat_code_imports_an_own_client_for_ollama_or_anthropic_or_starts_a_model_server():
    retired_clients = {"langchain_ollama", "langchain_anthropic", "ollama", "anthropic"}
    found = {}
    for path in sorted((SRC / "services").glob("*.py")) + sorted((SRC / "cli").glob("*.py")):
        names = _read_imported_names(path) & retired_clients
        if names and path.name not in ("setup_check_service.py", "mlx_setup_service.py"):
            found[path.name] = sorted(names)
    harness.check("no module of `services/` or `cli/` imports a retired chat client", not found, str(found))
    gone = [name for name in ("mlx_server_service.py", "retrying_chat_client.py") if (SRC / "services" / name).exists()]
    harness.check("the program's own model server and its retrying client are gone", not gone, str(gone))
