"""Nothing the meeting established is missing from the document, REQUIREMENTS.md R8.

What the program decides is tested here: that the web addresses, action items and questions a frame noted become
entries the document is written from and no outline may set aside, how a cut-off web address is completed by one
rule and never guessed, that a secret in a web address's query never reaches the document, that a part the model
could not write up keeps its entries, and that the coverage table accounts for every entry.
"""

import json
import logging
import tempfile
from pathlib import Path

from support import fakes
from support import fixtures as fx
from support import harness
from support import section_fixtures
from support import merge_inputs
from support import write_mode

from graph import graph_state
from graph.nodes import write_document_node
from models import output_contract
from models.attempt_request import AttemptRequest
from models.document_spec import DocumentChapterSpec, DocumentSpec, DocumentTopicSpec
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import call_log_service
from services import chain_dispatch_service
from services import document_file_service
from services import phase_file_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from services.run_store_service import RunStore
from utils import frame_merge_utils
from utils import document_plan_utils
from utils import document_text_utils
from validators import document_validator

FINALIZE = output_contract.PHASE_NUMBERS["finalize"]
FRAMES = {"items": [{"slug": "frame-000", "index": 0, "timestamp": 4.0}, {"slug": "frame-001", "index": 1, "timestamp": 9.0},
                    {"slug": "frame-002", "index": 2, "timestamp": 15.0}]}


def _merge(records: list[dict]) -> dict:
    return frame_merge_utils.merge_frame_records({"segments": []}, FRAMES, records, {})


def test_web_addresses_action_items_and_questions_become_entries_of_their_own_kind():
    records = [{"slug": "frame-000", "index": 0, "timestamp": 4.0, "facts": ["The shop sells tea."],
                "urls": ["https://shop.example.com/orders?page=2 | the order list"],
                "action_items": ["The support team answers the open tickets by Monday."],
                "questions_asked": ["Can a refund be split? Yes, into at most three parts."]}]
    kinds = {entry["kind"]: entry["text"] for entry in _merge(records)["entries"]}
    harness.check("each list a frame noted is an entry of its own kind",
                  kinds.get("url", "").startswith("https://shop.example.com/orders")
                  and "support team" in kinds.get("action_item", "") and "refund" in kinds.get("question", ""), str(kinds))
    integration = {"entries": _merge(records)["entries"], "unreadSpeech": []}
    harness.check("the document is written from all three", {"url", "action_item", "question"} <= {
        entry["kind"] for entry in document_plan_utils.select_document_entries(integration)})


def test_a_cut_off_web_address_is_completed_only_by_a_longer_one_it_starts():
    records = [{"slug": "frame-000", "index": 0, "timestamp": 4.0, "urls": ["https://docs.example.com/guide/inst… | the guide"]},
               {"slug": "frame-001", "index": 1, "timestamp": 9.0,
                "urls": ["https://docs.example.com/guide/install/linux | the install guide"]},
               {"slug": "frame-002", "index": 2, "timestamp": 15.0,
                "urls": ["https://api.example.com/v1/… | the API", "https://api.example.com/v1/users | users",
                         "https://api.example.com/v1/orders | orders", "https://cdn.example.com/a…/logo.png | a logo",
                         "https://status.example.com | the status page"]}]
    urls = {entry["text"]: entry for entry in _merge(records)["entries"] if entry["kind"] == "url"}
    harness.check("a cut-off address that starts exactly one longer one is that one, seen in both frames",
                  "https://docs.example.com/guide/inst… | the guide" not in urls
                  and urls["https://docs.example.com/guide/install/linux | the install guide"]["frames"] == [0, 1],
                  str(sorted(urls)))
    harness.check("one that starts two different longer ones is kept as it is: the program never picks",
                  "https://api.example.com/v1/… | the API" in urls, str(sorted(urls)))
    harness.check("one cut in the middle, and one not cut at all, are kept as they are",
                  "https://cdn.example.com/a…/logo.png | a logo" in urls and "https://status.example.com | the status page" in urls,
                  str(sorted(urls)))


def test_a_secret_in_a_web_address_never_reaches_the_document():
    masked = document_text_utils.hide_secret_query_values(
        "Sign-in returns to https://app.example.com/callback?code=abc123&state=xyz&lang=de#access_token=tok9 at once.")
    harness.check("the code and the token are masked, the address and its ordinary parameters kept",
                  masked == "Sign-in returns to https://app.example.com/callback?code=…&state=xyz&lang=de#access_token=… "
                            "at once.", masked)
    ordinary = "See https://shop.example.com/orders?page=2&sort=date, and [the guide](https://docs.example.com/a?x=1)."
    harness.check("an address with no secret is left exactly as it was, punctuation and link syntax included",
                  document_text_utils.hide_secret_query_values(ordinary) == ordinary,
                  document_text_utils.hide_secret_query_values(ordinary))
    harness.check("a parameter that only contains a secret word as part of another word is spared",
                  document_text_utils.hide_secret_query_values("https://a.example/x?keyboard=de&monkey=1")
                  == "https://a.example/x?keyboard=de&monkey=1")
    paths = fx.make_paths()
    request = AttemptRequest(job_kind="section", attempt=1, repository=paths.repository, module=paths.module,
                             paths=paths, ignore=IgnoreList(), inputs={"slug": "heading-001-1"})
    chain_dispatch_service.write_record(request, {
        "markdown": "The callback is https://app.example.com/cb?client_secret=s3cr3t&page=1. [S0001]\n"})
    written = document_file_service.read_section(paths, "heading-001-1")["paragraphs"][0]["text"]
    harness.check("the program masks it in what a section wrote, before it is stored",
                  written == "The callback is https://app.example.com/cb?client_secret=…&page=1.", written)


def _write_part(paths,
                slug: str,
                parsed: dict) -> None:
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, slug),
                                    {"response": {"outcome": "completed", "parsed": parsed, "finishReason": "stop"}})
    document_file_service.write_outline(paths, slug, parsed)


def test_an_outline_may_not_set_aside_a_web_address_an_action_item_or_a_question():
    paths = fx.make_paths()
    entries = [{"id": "S0001", "kind": "facts", "text": "a"}, {"id": "F0.1", "kind": "url", "text": "https://x.example"},
               {"id": "F0.2", "kind": "speech", "text": "Hello."}]
    outline = {"topics": [{"title": "Shop", "first_entry_id": "S0001", "entry_ids": ["S0001", "F0.1"]}],
               "set_aside_entry_ids": ["F0.2"],
               "not_established": []}
    _write_part(paths, "outline-01", outline)
    harness.check("a greeting set aside is accepted", document_validator.validate_outline(
        paths, "outline-01", [entry["id"] for entry in entries], kept_ids=["F0.1"]).ok)
    _write_part(paths, "outline-01", {**outline, "topics": [{"title": "Shop", "first_entry_id": "S0001", "entry_ids": ["S0001"]}],
                                      "set_aside_entry_ids": ["F0.1", "F0.2"]})
    codes = harness.codes(document_validator.validate_outline(paths, "outline-01", [entry["id"] for entry in entries],
                                                               kept_ids=["F0.1"]))
    harness.check("a web address set aside is DOCUMENT_SET_ASIDE_KEPT_KIND", codes == {"DOCUMENT_SET_ASIDE_KEPT_KIND"},
                  str(codes))


ENTRIES = [
    {"id": "S0001", "kind": "speech", "text": "Hello and welcome.", "start": 0.0, "end": 3.0, "timestamps": [0.0],
     "source": "transcript"},
    {"id": "F0.1", "kind": "facts", "text": "The shop sells tea in three sizes.", "timestamps": [4.0], "frames": [0],
     "source": "frame"},
    {"id": "F0.2", "kind": "speech_visual_alignment", "text": "The sign names the shop; nobody reads it out.",
     "timestamps": [4.0], "frames": [0], "source": "frame"},
    {"id": "F1.1", "kind": "facts", "text": "Orders ship within two days.", "timestamps": [9.0], "frames": [1],
     "source": "frame"},
    {"id": "F1.2", "kind": "facts", "text": "Frame 3 shows the parcel label printer.", "timestamps": [9.0],
     "frames": [1], "source": "frame"},
    {"id": "S0002", "kind": "speech", "text": "As you can see here.", "start": 9.5, "end": 11.0, "timestamps": [9.5],
     "source": "transcript"}]
INTEGRATION = {"schemaVersion": 1, "entries": ENTRIES,
               "frames": [{"index": 0, "timestamp": 4.0, "screenKind": "slide"}, {"index": 1, "timestamp": 9.0, "screenKind": "slide"}],
               "contradictions": [], "gaps": [], "unreadSpeech": [{"start": 0.0, "end": 4.0, "why": "before the first frame"}],
               "counts": {"frames": 2, "framesAccepted": 2}}


def _answer(schema: str,
            **fields):
    # A section is stated by its parts and answered as the markdown the agent writes.
    fields = section_fixtures.build_section_answer(fields) if schema == "DocumentSection" else fields
    message = fakes.tool_call(schema, **fields)
    message.response_metadata.update({"done_reason": "stop"})

    return message


def _load_run_context(model) -> RunContext:
    tmp = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=tmp, module="meeting.mp4", output_root_override=tmp / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = tmp / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(tmp), module="meeting.mp4",
                                        output_root=str(paths.output_root)), budgets={"maxAttempts": 1}, models=[])
    merge_inputs.write_merge_inputs(paths, INTEGRATION)

    return RunContext(repository=tmp, module="meeting.mp4", paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("test"), ignore=IgnoreList(),
                      model=model)


def test_a_section_that_could_not_be_written_stops_the_phase():
    parts = _answer("DocumentParts", announced_agenda=[],
                    points=[{"title": "The tea shop", "first_moment": 1, "covers_announced": []}])
    outline = _answer("DocumentOutline", not_established=[], set_aside_entry_ids=["S0001"],
                      topics=[{"title": "Range", "first_entry_id": "F0.1"}, {"title": "Shipping", "first_entry_id": "F1.1"}])
    title = _answer("DocumentContents", title="The tea shop ships every order within two days")
    range_section = _answer("DocumentSection", heading="Range", not_established=[],
                            paragraphs=[{"text": "The shop sells tea in three sizes.", "entry_ids": ["F0.1"]}])
    bad_section = _answer("DocumentSection", heading="Shipping", not_established=[],
                          paragraphs=[{"text": "The video shows the shipping.", "entry_ids": ["F1.1", "F1.2"]}])
    summary = _answer("DocumentSummary", agenda=[{"part_number": 1, "text": "Introduces the tea shop"}],
                      summary=[{"text": "The shop sells tea in three sizes.", "entry_ids": ["F0.1"]}])
    ctx = _load_run_context(fakes.scripted(parts, outline, title, range_section, bad_section, summary))
    update = write_mode.run_phase_4_in_calls_mode(graph_state.new_run_state(FINALIZE, FINALIZE),
                                                       {"configurable": {"ctx": ctx}})
    harness.check("the phase is blocked", update["phases"]["finalize"] == "blocked", str(update))
    harness.check("no document is written, and the summary call after the block was never asked",
                  not ctx.paths.summary.is_file() and ctx.model.call_count < 6, f"calls={ctx.model.call_count}")


def test_a_web_address_without_its_scheme_keeps_no_secret_either():
    harness.check("an address copied without https:// has its code hidden",
                  document_text_utils.hide_secret_query_values("Open portal.example.com/callback?code=abc123&lang=de.")
                  == "Open portal.example.com/callback?code=…&lang=de.")
    harness.check("so does a parameter after an escaped ampersand",
                  document_text_utils.hide_secret_query_values("a.example/x?page=1&amp;token=abc")
                  == "a.example/x?page=1&amp;token=…")
