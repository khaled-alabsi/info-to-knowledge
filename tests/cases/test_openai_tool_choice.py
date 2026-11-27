# <!-- sync: seed/cases.test_openai_tool_choice v1 -->
"""A structured answer from an OpenAI-compatible host: its one tool is forced in a form every such host accepts.

LangChain forces the tool of a structured call by NAME, `tool_choice={"type": "function", "function": {"name": ...}}`.
LM Studio refuses that form before the model runs: 400, "Invalid tool_choice type: 'object'. Supported string
values: none, auto, required" (measured 2026-09-26, `qwen3.6-35b-a3b-mtp`). With one tool bound, "required" asks for
the same thing: the model must call a tool, and there is only that one. With several, the name is the choice, and
it is kept.
"""

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from support import harness

from services.openai_compatible_client import OpenAICompatibleChatModel


class Answer(BaseModel):
    text: str


class Other(BaseModel):
    note: str


def _build_model() -> OpenAICompatibleChatModel:
    return OpenAICompatibleChatModel(model="m", base_url="http://localhost:1/v1", api_key="not-needed")


def _read_sent_tool_choice(model: OpenAICompatibleChatModel,
                           bound_kwargs: dict):
    """The `tool_choice` of the request body the host would receive."""

    payload = model._get_request_payload([HumanMessage("hi")], **bound_kwargs)

    return payload.get("tool_choice"), [tool["function"]["name"] for tool in payload.get("tools", [])]


def test_a_structured_call_asks_for_its_one_tool_as_required():
    model = _build_model()
    structured = model.with_structured_output(Answer, method="function_calling", include_raw=True)
    raw = structured.steps[0].steps__["raw"]
    choice, tools = _read_sent_tool_choice(model, raw.kwargs)
    harness.check("the structured call sends tool_choice 'required', which LM Studio accepts", choice == "required",
                  str(choice))
    harness.check("and its one tool is the schema", tools == ["Answer"], str(tools))


def test_a_named_choice_among_several_tools_keeps_its_name():
    model = _build_model()
    choice, tools = _read_sent_tool_choice(model, model.bind_tools([Answer, Other], tool_choice="Answer").kwargs)
    harness.check("with two tools, 'required' would let the model pick the other one: the name stays",
                  choice == {"type": "function", "function": {"name": "Answer"}} and tools == ["Answer", "Other"],
                  str(choice))


def test_auto_and_none_are_sent_as_they_are():
    model = _build_model()
    for asked in ("auto", "none"):
        choice, _ = _read_sent_tool_choice(model, model.bind_tools([Answer], tool_choice=asked).kwargs)
        harness.check(f"'{asked}' is sent as '{asked}'", choice == asked, str(choice))


def test_the_output_limit_is_sent_as_max_tokens_which_every_compatible_host_reads():
    """The OpenAI library sends the limit as `max_completion_tokens`; mlx_vlm.server reads only `max_tokens` and
    capped every answer at its default of 2,048 tokens, a batch of ten frames cut off with no thinking."""

    model = OpenAICompatibleChatModel(model="acme-model", base_url="http://localhost:9/v1", api_key="n", max_tokens=40960)
    payload = model._get_request_payload([HumanMessage(content="hello")])
    harness.check("the limit travels as max_tokens", payload.get("max_tokens") == 40960, str(payload))
    harness.check("and not twice, under the OpenAI-only name", "max_completion_tokens" not in payload, str(payload))
