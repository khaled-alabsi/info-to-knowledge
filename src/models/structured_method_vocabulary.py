"""The structured-output method each model provider honours when a model names none: measured, not assumed. Read by the
chat model builder and by the Settings page's lines that say what a run uses for a model with no `structuredMethod`."""

from __future__ import annotations

__all__ = ["STRUCTURED_METHOD_BY_PROVIDER"]

STRUCTURED_METHOD_BY_PROVIDER = {
    "lmstudio": "function_calling",
    "openai-compatible": "function_calling",
    "openai": "json_schema",
}
