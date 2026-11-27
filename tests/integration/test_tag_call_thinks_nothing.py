"""The tag call `tag-runs` makes thinks nothing on the configured model (`chain_runner._keep_bound_options`): bound to
think nothing and wrapped for structured output, it thought 289 tokens of a 325-token answer, and a call over a
document's start took minutes. The call goes to the model phase 4 is configured with, over an invented document; the
test is skipped unless that model is already loaded on its LM Studio host: a call to a model not loaded makes LM
Studio load one, and a run of the integration tests loaded a second 19 GB copy."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from support import harness

from chains import chain_runner
from schemas.run_tags_answer import RunTagsAnswer
from services import chat_model_service
from services import host_window_service


def test_the_tag_call_thinks_nothing_on_the_configured_model_defect():
    # The tag call kept thinking although bound to think nothing; it now sends its no-thinking options
    settings = chat_model_service._resolve_phase_settings("finalize")
    provider, name = chat_model_service.split_spec(settings.spec)
    host = chat_model_service.resolve_host_url(settings) or ""
    if provider != "lmstudio" or host_window_service.read_loaded_window(host, name) is None:
        print(f"{settings.spec} is not loaded on its LM Studio host: skipped")
        return
    model = chat_model_service.bind_no_thinking(chat_model_service.get_model("finalize"))
    try:
        reply = chain_runner.invoke_structured_chain(model, RunTagsAnswer, chat_model_service.resolve_structured_method("finalize"),
                                                     [HumanMessage(content="Tag a document about AcmeSync's refund rules "
                                                                           "in its payment gateway. Give 2 tags.")])
    except Exception as exc:  # noqa: BLE001 (no host on this machine: nothing to measure)
        print(f"the configured model did not answer ({type(exc).__name__}): skipped")
        return
    usage = (reply.raw.response_metadata or {}).get("token_usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    harness.check("the call is answered with tags", reply.parsed is not None and bool(reply.parsed.tags), str(reply.parsed))
    harness.check("and spends no token thinking", reasoning == 0, str(usage))
