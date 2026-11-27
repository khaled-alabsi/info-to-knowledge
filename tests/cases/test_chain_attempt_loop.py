"""The chain attempt loop, driven by a scripted model. No host, no network, deterministic.

The claim under test is the same one the agent loop rests on: what the model RETURNED decides
nothing. A record is accepted because a validator read it off disk, and a response that parsed to
nothing is a rejected attempt rather than an empty success.
"""

import json
import logging
import tempfile
from itertools import count
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from support import fakes
from support import fixtures as fx
from support import harness

from graph.nodes.chain_attempt_loop import chain_attempt_loop_runner
from models.chain_call_spec import ChainCallSpec
from models.job_retry_context import JobRetryContext, JobRetryHandles
from models.run_context import RunContext
from models.run_identity import RunIdentity
from schemas import review_actions
from schemas import write_actions
from services import call_log_service
from services import chain_dispatch_service
from services import validation_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import json_store_utils
from validators import chain_response_validator

# What the program SHIPS, read before any test below has registered a kind of its own.
SHIPPED_KINDS = {"builders": set(chain_dispatch_service.MESSAGE_BUILDERS),
                 "schemas": set(chain_dispatch_service.SCHEMAS),
                 "readers": set(chain_dispatch_service.MARKDOWN_READERS),
                 "writers": set(chain_dispatch_service.RECORD_WRITERS),
                 "validators": set(validation_service.VALIDATORS)}

SLUG = "item-001"
# The attempts one item gets in these tests
MAX_ATTEMPTS = 3
_KINDS = count(1)
IMAGE_PART = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}


class FixtureRecord(BaseModel):
    """The structure the scripted model is asked for."""

    title: str


def _build_fixture_messages(request) -> list:
    text = f"attempt {request.attempt}; findings: {[f.get('code') for f in request.findings]}"

    return [SystemMessage(content="fixture system text"),
            HumanMessage(content=[{"type": "text", "text": text}, IMAGE_PART])]


def _build_fixture_record_path(paths) -> Path:
    """The fixture kind's own record, as JSON: phase 3's record files are markdown with a shape of their own."""

    return paths.output_root / "fixture-records" / f"{SLUG}.json"


def _write_fixture_record(request, parsed: dict) -> Path:
    path = _build_fixture_record_path(request.paths)
    json_store_utils.write_json(path, parsed)

    return path


def _validate_fixture(ctx, request, snapshot):
    slug = str(request.inputs["slug"])
    report = chain_response_validator.validate_chain_response(
        call_log_service.build_call_log_path(request.paths, slug), slug)
    if report.ok and not _build_fixture_record_path(request.paths).is_file():
        report.add("RECORD_MISSING", "the record was not written", fix="write it")

    return report


def _register_fixture_kind() -> str:
    kind = f"fixture-{next(_KINDS)}"
    chain_dispatch_service.MESSAGE_BUILDERS[kind] = _build_fixture_messages
    chain_dispatch_service.SCHEMAS[kind] = FixtureRecord
    chain_dispatch_service.RECORD_WRITERS[kind] = _write_fixture_record
    validation_service.VALIDATORS[kind] = _validate_fixture

    return kind


def _remove_fixture_kind(kind: str) -> None:
    for table in (chain_dispatch_service.MESSAGE_BUILDERS, chain_dispatch_service.SCHEMAS,
                  chain_dispatch_service.RECORD_WRITERS, validation_service.VALIDATORS):
        table.pop(kind, None)


def _load_run_context(model,
                      kind: str) -> tuple[RunContext, JobRetryContext]:
    tmp = Path(tempfile.mkdtemp())
    paths = fx.make_paths(tmp)
    run_dir = tmp / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(paths.repository),
                                        module=paths.module, output_root=str(paths.output_root)),
                            budgets={"maxAttempts": MAX_ATTEMPTS}, models=[])
    store.seed_items([{"slug": SLUG, "status": "pending"}])
    ctx = RunContext(repository=paths.repository, module=paths.module, paths=paths, store=store,
                     run_dir=run_dir, log=logging.getLogger("test"),
                     ignore=IgnoreList(), model=model)
    job = JobRetryContext(job_kind=kind, phase="frames_processing",
                          snapshot_targets=[_build_fixture_record_path(paths),
                                            call_log_service.build_call_log_path(paths, SLUG)],
                          inputs={"slug": SLUG}, handles=JobRetryHandles())

    return ctx, job


def _answer(title: str,
            thinking: str = "") -> AIMessage:
    message = fakes.tool_call("FixtureRecord", title=title)
    message.additional_kwargs["reasoning_content"] = thinking
    message.response_metadata.update({"done_reason": "stop", "prompt_eval_count": 1200, "eval_count": 300,
                                      "prompt_eval_duration": 2_000_000_000, "eval_duration": 6_000_000_000})

    return message


def _run(model) -> tuple[RunContext, object]:
    kind = _register_fixture_kind()
    try:
        ctx, job = _load_run_context(model, kind)
        outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=SLUG,
                                                                   max_attempts=MAX_ATTEMPTS)
    finally:
        _remove_fixture_kind(kind)

    return ctx, outcome


def test_the_shipped_dispatch_tables_agree_with_each_other():
    """A kind the loop can dispatch has a row in EVERY table, checked on what the program ships."""

    harness.check("the program ships at least one chain kind", bool(SHIPPED_KINDS["builders"]),
                  str(SHIPPED_KINDS))
    harness.check("every shipped kind has a schema, or reads a markdown answer, never both",
                  SHIPPED_KINDS["builders"] == SHIPPED_KINDS["schemas"] | SHIPPED_KINDS["readers"]
                  and not SHIPPED_KINDS["schemas"] & SHIPPED_KINDS["readers"], str(SHIPPED_KINDS))
    harness.check("every shipped kind has a record writer",
                  SHIPPED_KINDS["builders"] <= SHIPPED_KINDS["writers"], str(SHIPPED_KINDS))
    # A kind answered through a conversation's submit tool (R12, R16) has a writer and a validator, and no message
    # builder or schema of its own: the submit tool's schema is its schema, and the conversation is its message.
    submitted = {tool.job_kind for tools in (write_actions.SUBMIT_TOOLS, review_actions.SUBMIT_TOOLS)
                 for tool in tools.values()}
    harness.check("every record writer serves a dispatched kind or a conversation's submit tool",
                  SHIPPED_KINDS["writers"] <= SHIPPED_KINDS["builders"] | submitted,
                  str(SHIPPED_KINDS["writers"] - SHIPPED_KINDS["builders"] - submitted))
    # The final follow-up's answer is written as its two parts, the title and the summary, each a kind of its own.
    harness.check("every submitted kind is written and judged",
                  submitted - {"final"} <= SHIPPED_KINDS["writers"] & SHIPPED_KINDS["validators"],
                  str(submitted - SHIPPED_KINDS["writers"]))
    harness.check("every shipped kind has a validator",
                  SHIPPED_KINDS["builders"] <= SHIPPED_KINDS["validators"], str(SHIPPED_KINDS))


def test_an_unknown_kind_raises_rather_than_defaulting():
    try:
        chain_dispatch_service.build_messages(type("Request", (), {"job_kind": "no-such-kind"})())
    except KeyError as exc:
        harness.check("the refusal names the kind", "no-such-kind" in str(exc), str(exc))
    else:
        harness.check("an unknown chain kind raises", False)


def test_a_call_of_a_kind_with_no_schema_raises_rather_than_reading_as_a_refused_call():
    spec = ChainCallSpec("frames_processing", "no-such-kind")
    try:
        result = chain_dispatch_service.invoke_chain(fakes.scripted(fakes.says("anything")), spec, [HumanMessage("x")])
        outcome = f"returned {result.outcome}: {result.detail}"
    except KeyError as exc:
        outcome = f"raised {exc}"
    harness.check("the wiring fault raises, naming the kind", outcome.startswith("raised") and "no-such-kind" in outcome,
                  outcome)


def test_a_parsed_answer_is_written_by_the_program_and_accepted():
    ctx, outcome = _run(fakes.scripted(_answer("what the frame shows", thinking="I looked at it.")))
    harness.check("a parsed record is accepted", outcome.is_accepted is True, outcome.summary)
    harness.check("in one attempt", outcome.attempts == 1, f"attempts={outcome.attempts}")
    record = json.loads(_build_fixture_record_path(ctx.paths).read_text(encoding="utf-8"))
    harness.check("the record on disk is what the model returned", record == {"title": "what the frame shows"},
                  str(record))
    call = json.loads(call_log_service.build_call_log_path(ctx.paths, SLUG).read_text(encoding="utf-8"))
    harness.check("the call log keeps the thinking", call["response"]["thinking"] == "I looked at it.", str(call))
    harness.check("and how the response ended", call["response"]["finishReason"] == "stop", str(call))
    harness.check("and the rendered prompt, as text", "attempt 1" in call["request"]["human"], str(call["request"]))
    harness.check("with the image named by its size, never pasted", "AAAA" not in json.dumps(call["request"]),
                  str(call["request"]))
    harness.check("the image still reached the model", IMAGE_PART in ctx.model.calls[0][1].content,
                  str(ctx.model.calls[0][1].content))
    rows = call_log_service.read_call_metrics(ctx.run_dir)
    harness.check("one metrics row was appended", len(rows) == 1, str(rows))
    harness.check("carrying the prompt tokens the host reported", rows[0]["promptTokens"] == 1200, str(rows))


def test_thinking_with_no_answer_is_rejected_and_retried():
    """The defect this loop exists for: a model that spent its whole reply thinking."""

    model = fakes.scripted(fakes.cut_off(thinking="planning, planning"), _answer("second time lucky"))
    ctx, outcome = _run(model)
    harness.check("the second attempt is accepted", outcome.is_accepted is True, outcome.summary)
    harness.check("after two attempts", outcome.attempts == 2, f"attempts={outcome.attempts}")
    first = ctx.store.list_attempts_for("frames_processing", SLUG)[0]
    harness.check("the first attempt was rejected for being cut short",
                  "RESPONSE_CUT_AT_LIMIT" in {f["code"] for f in first["validation"]["findings"]}, str(first))
    captured = ctx.run_dir / first["logDir"] / "output-snapshot" / f"{SLUG}.json"
    harness.check("and its thinking is on disk in its own attempt directory",
                  "planning, planning" in captured.read_text(encoding="utf-8"), str(captured))
    retry_text = ctx.model.calls[1][1].content[0]["text"]
    harness.check("the retry was told what the last attempt got wrong", "RESPONSE_CUT_AT_LIMIT" in retry_text,
                  retry_text)


def test_a_model_that_never_answers_blocks_the_item_and_writes_nothing():
    ctx, outcome = _run(fakes.scripted(fakes.says("I have written the record.")))
    harness.check("prose is never accepted as a record", outcome.is_accepted is False)
    harness.check("the budget was spent", outcome.attempts == MAX_ATTEMPTS, f"attempts={outcome.attempts}")
    harness.check("the decision is exhausted", outcome.decision == "exhausted")
    harness.check("the findings name the cause",
                  {f["code"] for f in outcome.findings} == {"MODEL_NO_STRUCTURED_OUTPUT"}, str(outcome.findings))
    harness.check("no record was written", not _build_fixture_record_path(ctx.paths).exists())


def test_a_budget_already_spent_launches_nothing():
    kind = _register_fixture_kind()
    try:
        model = fakes.scripted(_answer("never asked"))
        ctx, job = _load_run_context(model, kind)
        refusal = {"code": "FIXTURE_REFUSED", "detail": "the last attempt's refusal"}
        for number in range(1, MAX_ATTEMPTS + 1):
            ctx.store.record_attempt("frames_processing", SLUG, {"n": number, "validation": {"findings": [refusal]}})
        outcome = chain_attempt_loop_runner.run_chain_attempt_loop(ctx, job, slug=SLUG,
                                                                   max_attempts=MAX_ATTEMPTS)
    finally:
        _remove_fixture_kind(kind)
    harness.check("a spent budget is reported as exhausted", outcome.decision == "exhausted")
    harness.check("with the last attempt's findings, so a blocked frame keeps its refusal", outcome.findings == [refusal],
                  str(outcome.findings))
    harness.check("and the model was never called", model.call_count == 0, f"calls={model.call_count}")
