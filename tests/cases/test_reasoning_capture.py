# <!-- sync: seed/cases.test_reasoning_capture v1 -->
"""A thinking model's thinking reaches `messages.jsonl`, beside what it answered.

`langchain_openai.ChatOpenAI` targets the official OpenAI schema and DROPS every extra field a
third-party host returns - `reasoning_content` among them - so a local OpenAI-compatible host serving
a thinking model produced logs holding the answer and no trace of the route to it. Nothing failed:
the run was green, the transcript was just half a transcript. These tests pin all three links in the
chain, because each one is silent on its own.
"""

import json
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from support import harness

from config import ModelSettings
from services import chat_model_service
from services.attempt_log_service import AttemptLog
from utils import message_content_utils
from services.openai_compatible_client import OpenAICompatibleChatModel


# One LM Studio answer, in the shape its API really returns it: thinking in `reasoning_content`,
# answer in `content`, both on the same message. Measured, not invented - see the creator's
# references/verified-api.md.
_RESPONSE = {
    "id": "chatcmpl-1",
    "model": "a-thinking-model",
    "choices": [{
        "index": 0,
        "finish_reason": "stop",
        "message": {
            "role": "assistant",
            "content": "51",
            "reasoning_content": "17 * 3 = 51. The user asked for the number only.",
            "tool_calls": [],
        },
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
}


def _client(cls):
    return cls(model="a-thinking-model", base_url="http://localhost:1234/v1",
               api_key="not-needed", temperature=0)


def test_openai_compatible_client_keeps_reasoning_and_content():
    message = _client(OpenAICompatibleChatModel)._create_chat_result(_RESPONSE).generations[0].message
    harness.check("reasoning survives the client",
                  message.additional_kwargs.get("reasoning_content", "").startswith("17 * 3"),
                  repr(message.additional_kwargs))
    harness.check("content survives beside it", message.content == "51", repr(message.content))


def test_plain_chat_openai_drops_reasoning():
    """The library behaviour this subclass exists for, pinned so an upgrade that fixes it shows."""

    message = _client(ChatOpenAI)._create_chat_result(_RESPONSE).generations[0].message
    harness.check("plain ChatOpenAI still drops reasoning_content",
                  "reasoning_content" not in message.additional_kwargs,
                  repr(message.additional_kwargs))


def test_streamed_reasoning_delta_is_kept():
    chunk = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "",
                                                "reasoning_content": "thinking..."}}]}
    generation = _client(OpenAICompatibleChatModel)._convert_chunk_to_generation_chunk(
        chunk, AIMessage, None)
    harness.check("reasoning survives a stream chunk",
                  generation is not None
                  and generation.message.additional_kwargs.get("reasoning_content") == "thinking...",
                  repr(generation))


def test_openai_compatible_provider_builds_the_preserving_client():
    """The wiring, not just the class: an lmstudio role must not resolve to plain `ChatOpenAI`."""

    model = chat_model_service._build(ModelSettings(spec="lmstudio:a-thinking-model",
                                                    base_url="http://localhost:1234/v1"))
    harness.check("lmstudio builds OpenAICompatibleChatModel",
                  isinstance(model, OpenAICompatibleChatModel), type(model).__name__)


def test_attempt_log_writes_thinking_beside_content():
    log = AttemptLog(Path(tempfile.mkdtemp()))
    log.write_messages([AIMessage(content="the answer",
                                  additional_kwargs={"reasoning_content": "the route"})])
    entry = json.loads((log.directory / "messages.jsonl").read_text().splitlines()[0])
    harness.check("messages.jsonl carries the thinking", entry.get("reasoning") == "the route",
                  repr(entry))
    harness.check("messages.jsonl carries the answer too", entry.get("content") == "the answer",
                  repr(entry))


def test_reasoning_is_read_whatever_the_provider_calls_it():
    """One reader, every spelling. A reader that knows one host logs an empty turn against the next."""

    for label, message, expected in (
            ("ollama-style reasoning_content",
             AIMessage(content="", additional_kwargs={"reasoning_content": "because A"}), "because A"),
            ("a plain `reasoning` key",
             AIMessage(content="", additional_kwargs={"reasoning": "because B"}), "because B"),
            ("a wrapped {'content': ...}",
             AIMessage(content="", additional_kwargs={"reasoning": {"content": "because C"}}), "because C"),
            ("response_metadata",
             AIMessage(content="", response_metadata={"reasoning_content": "because D"}), "because D"),
            ("a typed thinking block",
             AIMessage(content=[{"type": "thinking", "thinking": "because E"},
                                {"type": "text", "text": "answer"}]), "because E"),
            ("reasoning off is empty, not a crash", AIMessage(content="just an answer"), "")):
        harness.check(f"read_reasoning reads {label}",
                      message_content_utils.read_reasoning(message) == expected,
                      repr(message_content_utils.read_reasoning(message)))
    harness.check("the answer is not swallowed by the thinking block",
                  message_content_utils.read_message_text(
                      [{"type": "thinking", "thinking": "because E"},
                       {"type": "text", "text": "answer"}]) == "answer")


def test_a_text_holding_a_closing_thinking_tag_alone_is_split_there_defect():
    # A reply's planning ran into its text and ended in a closing tag with no opening one, and the whole of it was kept
    # as said text. Now the text up to the last closing tag is thinking; a text with no tag is said as it stands
    from utils import message_content_utils

    thinking, said = message_content_utils.split_think_block("I will refund the Gateway order first.\n</think>\nDone.")
    harness.check("the planning is thinking, the rest said",
                  thinking == "I will refund the Gateway order first." and said == "Done.", f"{thinking!r} {said!r}")
    harness.check("a text with no tag is said as it stands",
                  message_content_utils.split_think_block("The refund is split.") == ("", "The refund is split."), "")
