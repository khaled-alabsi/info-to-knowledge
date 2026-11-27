# <!-- sync: twin/services.retrying_chat_client v1 -->
"""Chat clients that send a dropped request again instead of losing the attempt around it.

The retry wraps the ONE method a chat model funnels a request through (`_generate`, the path of `invoke`), so it
sits under `create_agent`, under the graph, under everything - the caller never learns a request was sent twice,
and nothing above has to know a host can drop a socket. A streamed call goes through `_stream`, which is not
wrapped: a socket dropped mid-stream is not sent again (phase 3 streams its calls).

Why here and not in the agent loop: by the time an exception reaches the loop, the ATTEMPT is the
unit that fails, and the attempt holds every read, write and line of reasoning the worker has built.
The request is the thing that failed, so the request is the thing that is sent again.

There is no mixin. Each provider subclasses its own base and the shared decision - what counts as a
transport failure, how long to wait - lives once, in `model_retry_service`.
"""

from __future__ import annotations

from typing import Any

from langchain_ollama import ChatOllama

from services import model_retry_service
from services.openai_compatible_client import OpenAICompatibleChatModel

__all__ = ["RetryingOllamaChatModel", "RetryingOpenAICompatibleChatModel"]


class RetryingOllamaChatModel(ChatOllama):
    """`ChatOllama`, sending a dropped request again. Left unset, it behaves exactly as the base."""

    # Declared as a field because the base is a pydantic model and refuses an attribute it has not
    # been told about.
    retry_settings: Any = None

    def _generate(self, *args, **kwargs):
        if self.retry_settings is None:
            return super()._generate(*args, **kwargs)

        return model_retry_service.call_with_transport_retry(
            lambda: ChatOllama._generate(self, *args, **kwargs), self.retry_settings)


class RetryingOpenAICompatibleChatModel(OpenAICompatibleChatModel):
    """The same, for a local host speaking the OpenAI shape - LM Studio and its neighbours."""

    retry_settings: Any = None

    def _generate(self, *args, **kwargs):
        if self.retry_settings is None:
            return super()._generate(*args, **kwargs)

        return model_retry_service.call_with_transport_retry(
            lambda: OpenAICompatibleChatModel._generate(self, *args, **kwargs), self.retry_settings)
