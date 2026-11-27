# <!-- sync: seed/services.openai_compatible_client v2 -->
"""The OpenAI-compatible chat client, with the fields `ChatOpenAI` throws away put back.

`langchain_openai` targets the official OpenAI schema only, and says so in its own module docstring:
"Non-standard response fields added by third-party providers (e.g. `reasoning_content`,
`reasoning_details`) are **not** extracted or preserved."

Every local OpenAI-compatible host serving a thinking model returns exactly that field, so plain
`ChatOpenAI` against one silently discards the model's thinking: the attempt log then holds the
answer with no trace of how it was reached, which is the half a reader needs precisely when the
answer is wrong. This subclass copies those fields into `additional_kwargs`, where
`attempt_log_service` writes them into `messages.jsonl` beside the content.

Measured against LM Studio serving `qwen3.6-35b-a3b-mtp`: `choices[0].message` carries `content` AND
`reasoning_content`, and `chat_template_kwargs={"enable_thinking": false}` does NOT turn it off. The
Ollama path needs none of this - `ChatOllama(reasoning=True)` already separates it. See the creator's
`references/verified-api.md` for the probe.

A structured call forces its one tool by NAME, `tool_choice={"type": "function", "function": {"name": ...}}`, and
LM Studio refuses that form before the model runs: 400, "Invalid tool_choice type: 'object'. Supported string
values: none, auto, required" (measured on `qwen3.6-35b-a3b-mtp`). With one tool in the request,
"required" asks the same thing, so the request body this client builds says that. A choice among several tools keeps
its name. The body is changed where it is built, so every route to a forced tool is covered, not only
`with_structured_output`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

__all__ = ["OpenAICompatibleChatModel", "PRESERVED_MESSAGE_KEYS"]


# What a third-party host adds to a message that the official schema has no room for. Anything named
# here is copied verbatim into `additional_kwargs` under the same key, so a reader of the log and a
# reader of the host's own API documentation are looking at the same word.
PRESERVED_MESSAGE_KEYS = ("reasoning_content", "reasoning_details")
# The `tool_choice` strings every OpenAI-compatible host accepts; any other value names one tool.
_OPEN_TOOL_CHOICES = ("auto", "none", "required", "any")


def _read_field(source: Any,
                name: str) -> Any:
    """One field off a response that is a dict on one host and a pydantic object on the next."""

    if isinstance(source, dict):
        return source.get(name)

    return getattr(source, name, None)


def _is_named_choice(tool_choice: Any) -> bool:
    """Does this `tool_choice` force one tool by name (or by `True`), the form LM Studio refuses?"""

    if tool_choice is True or isinstance(tool_choice, dict):
        return True

    return isinstance(tool_choice, str) and tool_choice not in _OPEN_TOOL_CHOICES


def _copy_preserved_fields(message: Any,
                           source: Any) -> None:
    """Copy every preserved key present on `source` into the built message's additional_kwargs."""

    for key in PRESERVED_MESSAGE_KEYS:
        value = _read_field(source, key)
        if value:
            message.additional_kwargs[key] = value


class OpenAICompatibleChatModel(ChatOpenAI):
    """`ChatOpenAI` against a non-OpenAI host, keeping the provider fields the base class drops."""

    # The three hooks below are private API. Deliberate, and each is covered by a test that feeds it
    # a raw response or reads the body it builds: a library upgrade that renames or removes one fails
    # the suite, instead of the thinking vanishing from the logs, or LM Studio refusing every call.

    def _get_request_payload(self,
                             input_: Any,
                             *,
                             stop: list[str] | None = None,
                             **kwargs: Any) -> dict:
        """The request body as the base class builds it, except one tool forced by name, which is sent as
        "required": the one form of forcing a tool that every OpenAI-compatible host accepts; and the output limit,
        which the base class names `max_completion_tokens`, sent as `max_tokens`: mlx_vlm.server reads only that name
        and otherwise caps every answer at its default of 2,048 tokens."""

        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        if len(payload.get("tools") or []) == 1 and _is_named_choice(payload.get("tool_choice")):
            payload["tool_choice"] = "required"

        return payload

    def _create_chat_result(self,
                            response: Any,
                            generation_info: dict | None = None) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        choices = _read_field(response, "choices") or []
        for generation, choice in zip(result.generations, choices):
            _copy_preserved_fields(generation.message, _read_field(choice, "message"))

        return result

    def _convert_chunk_to_generation_chunk(self,
                                           chunk: dict,
                                           default_chunk_class: type,
                                           base_generation_info: dict | None) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info)
        if generation_chunk is None:
            return None
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if choices:
            _copy_preserved_fields(generation_chunk.message, _read_field(choices[0], "delta"))

        return generation_chunk
