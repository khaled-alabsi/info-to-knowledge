"""Can each phase's model do what the phase's calls need? Asked of the host, before a run starts.

A model downloads, lists and answers a plain prompt whether or not it can see an image or call a
function, so a phase handed the wrong one fails at its first real call, after whatever the run did
before it. Measured: of two vision models pulled on one host, one listed `tools` and one did not,
and only the first could answer a function-calling request at all.

`REQUIRED_CAPABILITIES` is the declaration: what each phase that calls a model needs. A phase is only held to what
this playbook says it needs.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

import config
from models.pipeline_errors import MissingModelCapability
from services import chat_model_service

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


def _read_ollama_capabilities(base_url: str,
                              model: str) -> list[str]:
    """What an Ollama host says this model can do. Raises when the host cannot be asked."""

    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/show",
                                     data=json.dumps({"model": model}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        return list(json.loads(response.read().decode("utf-8")).get("capabilities") or [])


# provider -> (base_url, model) -> capabilities. Only a provider that can be ASKED has a row; a phase
# on any other provider is reported as unchecked, never as capable.
CAPABILITY_READERS: dict[str, Callable[[str, str], list[str]]] = {
    "ollama": _read_ollama_capabilities,
}


def set_capability_reader(provider: str,
                          reader: Callable[[str, str], list[str]] | None) -> Callable[[str, str], list[str]] | None:
    """The test seam: a suite must never need a model host. Returns the reader it replaced."""

    previous = CAPABILITY_READERS.get(provider)
    CAPABILITY_READERS[provider] = reader

    return previous


def list_missing_capabilities(required: tuple[str, ...],
                              capabilities: list[str]) -> list[str]:
    return [name for name in required if name not in capabilities]


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
        if reader is None:
            unchecked.setdefault("this provider cannot be asked", []).append(phase)
            continue
        base_url = chat_model_service.resolve_host_url(config.settings.resolve_model(row["name"]))
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
