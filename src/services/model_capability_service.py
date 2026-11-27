"""Can each phase's model do what the phase's calls need? Asked of the host, before a run starts.

A model downloads, lists and answers a plain prompt whether or not it can see an image or call a
function, so a phase handed the wrong one fails at its first real call, after whatever the run did
before it. Measured: of two vision models pulled on one host, one listed `tools` and one did not,
and only the first could answer a function-calling request at all.

`REQUIRED_CAPABILITIES` is the declaration: what each phase that calls a model needs. A phase is only held to what
this playbook says it needs.
"""

from __future__ import annotations

from typing import Callable

import config
from models.endpoint_errors import EndpointError
from models.pipeline_errors import MissingModelCapability
from services import chat_model_service
from services import endpoint_client

__all__ = ["REQUIRED_CAPABILITIES", "list_missing_capabilities", "check_phase_capabilities",
           "set_capability_reader"]

# phase -> the capabilities its calls need, in the host's own words. Phase 3 sends frames and is answered in markdown,
# with no function call. Phases 4 and 5 call their tools and are shown frames by `look`, so they need both; phases 2
# and 6 are held to the same, as they were when they shared phase 4's model.
# Measured on this machine's Ollama: `qwen3-vl:8b` lists vision and tools; `qwen2.5vl:7b` lists vision
# only and cannot answer a function-calling request; `llama3.2-vision` does not load at all.
REQUIRED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "frames_preparing": ("vision", "tools"),
    "frames_processing": ("vision",),
    "finalize": ("vision", "tools"),
    "review": ("vision", "tools"),
    "critique_collection": ("vision", "tools"),
    # phase 7 reads and edits the document's text through tools, and is shown no picture
    "document_reshaping": ("tools",),
}

_TIMEOUT_S = 10

# provider -> (base_url, model) -> capabilities. The standard requests carry no capability list, so no provider has a row
# until a person registers one (`set_capability_reader`); a phase on a provider with no row is only asked whether its
# server lists the model, and is reported as unchecked for what the model can do, never as capable.
CAPABILITY_READERS: dict[str, Callable[[str, str], list[str]]] = {}

# The providers whose server lists its models at `GET /v1/models`
_LISTING_PROVIDERS = ("lmstudio", "openai-compatible")


def set_capability_reader(provider: str,
                          reader: Callable[[str, str], list[str]] | None) -> Callable[[str, str], list[str]] | None:
    """The test seam: a suite must never need a model host. Returns the reader it replaced."""

    previous = CAPABILITY_READERS.get(provider)
    CAPABILITY_READERS[provider] = reader

    return previous


def list_missing_capabilities(required: tuple[str, ...],
                              capabilities: list[str]) -> list[str]:
    return [name for name in required if name not in capabilities]


def _check_model_is_listed(phase: str,
                           described_model: str,
                           base_url: str,
                           served_name: str,
                           unchecked: dict[str, list[str]]) -> None:
    """Ask the server at `base_url` which models it lists. Raises `MissingModelCapability` when it answers without
    `served_name`; a server that does not answer, or lists it, adds the phase to `unchecked` under that reason."""

    endpoint = chat_model_service.build_chat_endpoint(base_url=base_url, timeout_s=_TIMEOUT_S)
    try:
        listed = endpoint_client.list_served_models(endpoint=endpoint)
    except EndpointError as exc:
        unchecked.setdefault(f"the server did not answer ({exc})", []).append(phase)
        return
    if served_name not in listed:
        raise MissingModelCapability(f"phase {phase!r} uses {described_model}, which the server at {base_url} does not "
                                     f"list. It lists: {', '.join(listed) or 'nothing'}.")
    unchecked.setdefault("the server lists the model and reports no capabilities", []).append(phase)


def check_phase_capabilities() -> list[str]:
    """One line per phase checked. Raises `MissingModelCapability` naming the phase, the model and what
    it lacks. The phases whose host cannot be asked share one line per reason, naming them: the header names each
    phase's model already, and five lines saying the same thing hid the ones that differ."""

    lines = []
    unchecked: dict[str, list[str]] = {}
    rows = {row["phase"]: row for row in chat_model_service.describe_phase_models()}
    for phase, required in REQUIRED_CAPABILITIES.items():
        row = rows.get(phase)
        if row is None:
            raise MissingModelCapability(f"phase {phase!r} is declared in REQUIRED_CAPABILITIES and not in "
                                         f"config.PHASE_DEFAULT_MODELS")
        model = f"{row['name']} ({row['provider']}:{row['model']})"
        reader = CAPABILITY_READERS.get(row["provider"])
        base_url = chat_model_service.resolve_host_url(config.settings.resolve_model(row["name"]))
        if reader is None and row["provider"] in _LISTING_PROVIDERS and base_url:
            _check_model_is_listed(phase, model, base_url, row["model"], unchecked)
            continue
        if reader is None:
            unchecked.setdefault("this provider cannot be asked", []).append(phase)
            continue
        try:
            capabilities = reader(base_url, row["model"])
        except (OSError, ValueError) as exc:
            unchecked.setdefault(f"the host did not answer ({exc})", []).append(phase)
            continue
        missing = list_missing_capabilities(required, capabilities)
        if missing:
            raise MissingModelCapability(
                f"phase {phase!r} uses {model}, which lacks {', '.join(missing)}. "
                f"The host lists: {', '.join(capabilities) or 'nothing'}. Give the phase a model that has "
                f"{', '.join(required)}.")
        lines.append(f"{phase}: {model} has {', '.join(required)}")
    lines += [f"capabilities not checked, {reason}: {', '.join(phases)}" for reason, phases in unchecked.items()]

    return lines
