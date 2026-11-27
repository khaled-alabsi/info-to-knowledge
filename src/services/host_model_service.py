"""The models a local model host has, asked of the host itself: what the setup wizard reports as installed, and what the
Settings page offers as a model's spec. Its own module, since both read it and the settings code is read by the
wizard's.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

__all__ = ["list_host_models", "HOST_TIMEOUT_S"]

HOST_TIMEOUT_S = 3  # seconds a host may take to list its models before it counts as not answering


def list_host_models(provider: str,
                     base_url: str) -> list[str] | None:
    """The model ids the host at `base_url` has, None when it does not answer."""

    root = base_url.rstrip("/").removesuffix("/v1")
    url = {"ollama": f"{root}/api/tags", "lmstudio": f"{root}/api/v0/models"}.get(provider, f"{base_url.rstrip('/')}/models")
    try:
        with urllib.request.urlopen(url, timeout=HOST_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        # a host that is off, or answers something else: reported as not answering, which is what its readers show
        return None

    return [entry["model"] for entry in payload.get("models", [])] if provider == "ollama" \
        else [entry["id"] for entry in payload.get("data", [])]
