"""Run tags: the tags the owner gives a run folder, kept in its `run-tags.json`, changed from the detail view and from
`i2k tags` through one backend operation, and shown in the run's summary."""

from __future__ import annotations

import argparse
import logging
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from cli import tags_command
from cli import run_folder_argument_builder
from models import output_contract
from services import run_status_service
from services import run_tags_service
from services import web_api_service


class LogLinesHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _build_run_folder() -> Path:
    folder = Path(tempfile.mkdtemp()) / "output" / "Gateway-review"
    folder.mkdir(parents=True)

    return folder


def test_tags_are_trimmed_split_on_commas_and_kept_once_whatever_their_letter_case():
    folder = _build_run_folder()
    harness.check("a folder with no tags file has none", run_tags_service.read_tags(folder) == [], "")
    tags = run_tags_service.apply_tag_changes(folder, ["  client  Acme , demo", "Demo", ""], [])
    harness.check("trimmed, split on the comma, the second spelling of one tag dropped", tags == ["client Acme", "demo"],
                  str(tags))
    harness.check("read back as written", run_tags_service.read_tags(folder) == ["client Acme", "demo"], "")
    tags = run_tags_service.apply_tag_changes(folder, ["Q3"], ["DEMO"])
    harness.check("one taken off whatever its case, a new one put last", tags == ["client Acme", "Q3"], str(tags))
    run_tags_service.write_tags(folder, [])
    harness.check("no tag left removes the file", not (folder / output_contract.RUN_TAGS_FILE).exists(), "")


def test_the_command_adds_takes_off_and_sets_and_refuses_a_folder_that_does_not_exist():
    folder = _build_run_folder()
    logger = logging.getLogger(f"test-tags-{id(object())}")
    handler = LogLinesHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def run(**changes) -> int:
        values = {"output_root": folder, "logger": logger, "added_tags": [], "removed_tags": [], "replacement_tags": None, **changes}
        return tags_command.cmd_tags(argparse.Namespace(**values))

    harness.check("add, exit 0", run(added_tags=["demo", "client Acme"]) == 0
                  and run_tags_service.read_tags(folder) == ["demo", "client Acme"], "\n".join(handler.lines))
    harness.check("take one off", run(removed_tags=["demo"]) == 0 and run_tags_service.read_tags(folder) == ["client Acme"], "")
    harness.check("set replaces them all", run(replacement_tags=["Q3", "budget"]) == 0
                  and run_tags_service.read_tags(folder) == ["Q3", "budget"], "")
    harness.check("the tags are printed", handler.lines[-1].endswith(": Q3, budget"), handler.lines[-1])
    harness.check("a folder that does not exist: exit 2", run(output_root=folder.parent / "missing") == 2, "")


def test_the_page_sets_a_run_s_tags_and_the_run_s_summary_shows_them():
    folder = _build_run_folder()
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = folder.parent
    try:
        client = TestClient(web_api_service.build_app())
        answer = client.put(f"/api/runs/output/{folder.name}/tags", json={"tags": ["demo", "Demo", "client Acme"]})
        harness.check("saved, exit 0", answer.status_code == 200 and answer.json()["exit_code"] == 0, answer.text)
        listed = next(run for run in client.get("/api/runs").json() if run["name"] == folder.name)
        harness.check("the run list shows its tags", listed["tags"] == ["demo", "client Acme"], str(listed))
        missing = client.put("/api/runs/output/missing/tags", json={"tags": ["demo"]})
        harness.check("an unknown run folder is 404", missing.status_code == 404, missing.text)
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)


def test_a_run_folder_given_relative_or_with_a_tilde_is_read_resolved():
    parser = argparse.ArgumentParser()
    run_folder_argument_builder.add_run_folder_argument(parser)
    harness.check("`--output` is read as the folder's resolved path",
                  parser.parse_args(["--output", "~/runs/x"]).output_root == (Path.home() / "runs" / "x").resolve())


def test_a_tags_file_that_does_not_read_is_refused_by_name_and_not_written_over():
    from services import run_operations_service

    folder = _build_run_folder()
    path = folder / output_contract.RUN_TAGS_FILE
    path.write_text('{"tags": ["lecture", "week 4', encoding="utf-8")
    shown = run_tags_service.read_tags(folder)
    result = run_operations_service.set_run_tags(folder, ["new"], [])
    harness.check("the detail view still shows no tags for it", shown == [], str(shown))
    harness.check("a change is refused with the file named, exit code 2", result.exit_code == 2
                  and output_contract.RUN_TAGS_FILE in " ".join(result.lines), f"{result.exit_code} {result.lines}")
    harness.check("and the file is left as it was", path.read_text(encoding="utf-8").endswith("week 4"),
                  path.read_text(encoding="utf-8"))
    path.write_text('{"tags": "abc"}', encoding="utf-8")
    harness.check("a tags value that is no list is no tags, never the letters a, b, c", run_tags_service.read_tags(folder) == [],
                  str(run_tags_service.read_tags(folder)))


def test_a_recording_is_tagged_by_the_naming_rules_its_name_matches():
    import tempfile
    from pathlib import Path
    from unittest import mock

    import config
    from services import run_operations_service
    from services import run_tags_service

    rules = [config.TagRule(prefix="acmesync", tag="acmesync"), config.TagRule(prefix="38c3", tag="ccc-congress")]
    with mock.patch.object(config.settings.tags, "name_rules", rules):
        harness.check("a name starting with a rule's prefix, letter case ignored, takes its tag",
                      run_tags_service.list_rule_tags("AcmeSync-20250701_180144-Besprechungsaufzeichnung.mp4") == ["acmesync"])
        harness.check("a name matching no rule takes none", run_tags_service.list_rule_tags("daily.mp4") == [])
        root = Path(tempfile.mkdtemp())
        for name in ("AcmeSync-20250812-meeting", "38c3-gateway-talk", "daily"):
            (root / name).mkdir()
        run_tags_service.write_tags(root / "AcmeSync-20250812-meeting", ["gateway"])
        result = run_operations_service.apply_tag_rules([root])
        harness.check("every run folder gets its rules' tags after the ones it has; one matching none is left as it is",
                      run_tags_service.read_tags(root / "AcmeSync-20250812-meeting") == ["gateway", "acmesync"]
                      and run_tags_service.read_tags(root / "38c3-gateway-talk") == ["ccc-congress"]
                      and run_tags_service.read_tags(root / "daily") == [] and result.exit_code == 0, str(result.lines))


def test_a_naming_rule_that_names_a_client_is_read_from_the_file_kept_out_of_git():
    import json
    import tempfile
    from pathlib import Path
    from unittest import mock

    from services import run_tags_service

    local = Path(tempfile.mkdtemp()) / "tag-rules.local.json"
    local.write_text(json.dumps({"nameRules": [{"prefix": "AcmeSync", "tag": "acmesync"}]}), encoding="utf-8")
    with mock.patch.object(run_tags_service, "LOCAL_RULES_FILE", local):
        harness.check("a rule from the local file tags a matching name", run_tags_service.list_rule_tags("acmesync-weekly.mp4") == ["acmesync"])
    with mock.patch.object(run_tags_service, "LOCAL_RULES_FILE", local.with_name("missing.json")):
        harness.check("no local file, no rule from it", run_tags_service.list_rule_tags("acmesync-weekly.mp4") == [])


def test_the_worker_s_tags_take_the_inventory_s_spelling_and_stop_at_the_limit():
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    from unittest import mock

    import config
    from services import run_tags_service
    from services import write_conversation_service

    root = Path(tempfile.mkdtemp())
    for name, tags in (("gateway-review", ["screen-recording", "gateway"]), ("refund-talk", ["gateway"])):
        (root / name).mkdir()
        run_tags_service.write_tags(root / name, tags)
    with mock.patch.object(config.settings.tags, "name_rules", []):
        inventory = run_tags_service.list_tag_inventory([root])
    harness.check("the inventory counts each tag, the most used first", inventory == [("gateway", 2), ("screen-recording", 1)],
                  str(inventory))
    with mock.patch.object(config.settings.tags, "name_rules", []), \
            mock.patch.object(run_tags_service, "_read_local_rules", return_value=[]):
        matched = run_tags_service.match_inventory_tags(["Gateways", "Screen recording", "refund rules", "audit"],
                                                        ["gateway", "screen-recording"], 3)
    harness.check("a near copy takes the inventory's spelling, a new tag stays, and no more than the limit",
                  matched == ["gateway", "screen-recording", "refund rules"], str(matched))
    run_folder = root / "new-run"
    run_folder.mkdir()
    ctx = SimpleNamespace(paths=SimpleNamespace(output_root=run_folder), log=mock.MagicMock())
    with mock.patch.object(config, "DOCUMENT_ROOT", root), mock.patch.object(config.settings.tags, "name_rules", []):
        write_conversation_service._add_worker_tags(ctx, ["Gateway", "refund rules"])
    harness.check("the final answer's tags are written onto the run folder", run_tags_service.read_tags(run_folder)
                  == ["gateway", "refund rules"], str(run_tags_service.read_tags(run_folder)))


def test_tags_are_kept_in_step_with_the_published_document_s_metadata_both_ways():
    import os
    import tempfile

    from pathlib import Path

    from services import run_tags_service

    folder = Path(tempfile.mkdtemp()) / "gateway-review"
    published = folder / "gateway-review-jonas"
    published.mkdir(parents=True)
    (published / "provenance.json").write_text("{}", encoding="utf-8")
    document = published / "gateway-review-jonas.md"
    document.write_text('---\ntopic: "Refund rules"\nevent: "Gateway review"\nspeakers: ["Jonas"]\ndate: ""\n---\n\n# Refunds\n',
                        encoding="utf-8")
    run_tags_service.write_tags(folder, ["gateway", "refunds"])
    text = document.read_text(encoding="utf-8")
    harness.check("the run's tags are written into the document's metadata block, the block kept",
                  'tags: ["gateway", "refunds"]' in text and 'topic: "Refund rules"' in text and text.endswith("# Refunds\n"), text)
    harness.check("the version it replaced is kept", any((folder / ".edits").rglob("gateway-review-jonas.md")))
    document.write_text(text.replace('tags: ["gateway", "refunds"]', "tags:\n  - gateway\n  - audit log"), encoding="utf-8")
    later = (folder / "run-tags.json").stat().st_mtime + 5
    os.utime(document, (later, later))
    harness.check("a block changed by hand after the tags file is read back as the run's tags",
                  run_tags_service.read_tags(folder) == ["gateway", "audit log"]
                  and run_tags_service._read_stored_tags(folder) == ["gateway", "audit log"], str(run_tags_service.read_tags(folder)))


def test_a_finished_run_with_no_worker_tags_gets_them_and_one_with_them_is_left():
    import tempfile
    from pathlib import Path
    from unittest import mock

    import config
    from services import run_tagging_service
    from services import run_tags_service

    root = Path(tempfile.mkdtemp())
    for name, tags in (("38c3-gateway-talk", ["ccc-congress"]), ("refund-review", ["gateway"]), ("unpublished", [])):
        folder = root / name
        (folder / f"{name}-doc").mkdir(parents=True)
        if name != "unpublished":
            (folder / f"{name}-doc" / "provenance.json").write_text("{}", encoding="utf-8")
            (folder / f"{name}-doc" / f"{name}-doc.md").write_text("---\ntopic: \"t\"\n---\n\n# Refunds\n", encoding="utf-8")
        run_tags_service.write_tags(folder, tags)
    rules = [config.TagRule(prefix="38c3", tag="ccc-congress")]
    with mock.patch.object(config.settings.tags, "name_rules", rules), \
            mock.patch.object(run_tagging_service, "_read_model_tags", return_value=["Gateways", "refund rules"]) as asked:
        reported = []
        result = run_tagging_service.add_finished_run_tags([root], reported.append)
    harness.check("the run holding only its rules' tags is asked, once, and gets the tags after them",
                  asked.call_count == 1 and run_tags_service.read_tags(root / "38c3-gateway-talk")
                  == ["ccc-congress", "gateway", "refund rules"], str((asked.call_count, result.lines)))
    harness.check("a run already holding other tags, and one with no document, are left",
                  run_tags_service.read_tags(root / "refund-review") == ["gateway"] and run_tags_service.read_tags(root / "unpublished") == [])


def test_each_run_tag_runs_gives_tags_is_reported_as_it_is_tagged_defect():
    # `tag-runs` printed nothing for an hour over 74 run folders: its lines came at its end. Each run's tags now go to
    # the report the moment it has them
    import tempfile
    from pathlib import Path
    from unittest import mock

    from services import run_tagging_service
    from services import run_tags_service

    root = Path(tempfile.mkdtemp())
    reported = []
    for name in ("gateway-talk", "refund-review"):
        (root / name / f"{name}-doc").mkdir(parents=True)
        (root / name / f"{name}-doc" / "provenance.json").write_text("{}", encoding="utf-8")
        (root / name / f"{name}-doc" / f"{name}-doc.md").write_text("# Refunds\n", encoding="utf-8")

    def _ask(document):
        reported.append(f"asked {document.parent.parent.name}")
        return ["gateway"]

    with mock.patch.object(run_tagging_service, "_read_model_tags", side_effect=_ask):
        result = run_tagging_service.add_finished_run_tags([root], reported.append)
    harness.check("the first run is reported before the second is asked",
                  len(reported) == 4 and reported[0] == "asked gateway-talk" and "gateway-talk" in reported[1]
                  and reported[2] == "asked refund-review", str(reported))
    harness.check("the result holds the count", result.lines == ["2 finished run(s) given worker tags"], str(result.lines))


def test_a_finished_run_s_tag_call_is_asked_the_final_answer_s_tags_field():
    from schemas.document_final import DocumentFinal
    from schemas.run_tags_answer import RunTagsAnswer

    harness.check("the tag call's field says what phase 4's final answer says of its tags",
                  RunTagsAnswer.model_fields["tags"].description == DocumentFinal.model_fields["tags"].description)
    harness.check("its answer is a list of tags", RunTagsAnswer(tags=["gateway"]).tags == ["gateway"])


def test_every_run_carries_the_format_of_the_file_it_was_made_from():
    import json
    import tempfile
    from pathlib import Path

    from services import run_operations_service
    from services import run_tags_service

    harness.check("a video, an audio file and a PDF name their format",
                  [run_tags_service.describe_format_tag(Path(name)) for name in ("talk.mp4", "call.m4a", "spec.pdf")]
                  == ["from-video", "from-audio", "from-pdf"])
    harness.check("a medium of the frames file names the same, a text-only PDF too",
                  [run_tags_service.describe_medium_format_tag(medium) for medium in ("video", "audio", "pdf-text-only", "")]
                  == ["from-video", "from-audio", "from-pdf", ""])
    root = Path(tempfile.mkdtemp())
    folder = root / "refund-review"
    folder.mkdir()
    (folder / "02_frames.json").write_text(json.dumps({"items": [], "medium": "audio"}), encoding="utf-8")
    run_operations_service.apply_tag_rules([root])
    harness.check("a run whose recording is gone takes its format from its frames file",
                  run_tags_service.read_tags(folder) == ["from-audio"], str(run_tags_service.read_tags(folder)))
    harness.check("the format tag is the program's, never a worker's", run_tags_service.is_format_tag("From-Video"))


def test_the_tag_call_is_offered_and_keeps_no_tag_the_program_writes_itself_defect():
    # Offered the format and naming-rule tags, the worker gave a congress talk "lecture" and most runs "from-video"
    # again. The inventory and the picked tags now leave out every tag the program writes itself
    import tempfile
    from pathlib import Path
    from unittest import mock

    import config
    from prompts import write_messages
    from services import run_tags_service

    root = Path(tempfile.mkdtemp())
    for name, tags in (("38c3-gateway-talk", ["ccc-congress", "from-video", "gateway"]),
                       ("Lecture 3 refunds", ["lecture", "from-video", "refund rules"])):
        (root / name).mkdir()
        run_tags_service.write_tags(root / name, tags)
    rules = [config.TagRule(prefix="38c3", tag="ccc-congress"), config.TagRule(prefix="Lecture", tag="lecture")]
    with mock.patch.object(config.settings.tags, "name_rules", rules), mock.patch.object(config, "DOCUMENT_ROOT", root):
        inventory = run_tags_service.list_tag_inventory([root])
        picked = run_tags_service.match_inventory_tags(["Lecture", "from-video", "Gateways", "AcmeSync"],
                                                       [tag for tag, _ in inventory], 3)
        ask = write_messages.render_tag_ask()
    harness.check("the inventory holds the subjects alone", inventory == [("gateway", 1), ("refund rules", 1)], str(inventory))
    harness.check("a program's tag the worker gives is dropped, its subjects kept", picked == ["gateway", "AcmeSync"],
                  str(picked))
    harness.check("the ask names no program tag and asks for the subject", "lecture (" not in ask
                  and "from-video" not in ask and "Never its form" in ask, ask[-300:])


def test_a_structured_call_keeps_the_no_thinking_its_model_was_bound_with_defect():
    # A tag call bound to think nothing thought 289 tokens of a 325-token answer, minutes a run: wrapped for structured
    # output, a bound model lost its options. The structured call now sets them on a copy of the chat model
    from langchain_openai import ChatOpenAI

    from chains import chain_runner
    from services import chat_model_service

    model = ChatOpenAI(model="qwen-test", api_key="none", base_url="http://localhost:1/v1")
    kept = chain_runner._keep_bound_options(chat_model_service.bind_no_thinking(model))
    harness.check("the copy holds no thinking and its sampling", isinstance(kept, ChatOpenAI)
                  and kept.reasoning_effort == "none" and kept.temperature == 0.7 and kept.top_p == 0.8,
                  f"{type(kept).__name__} {getattr(kept, 'reasoning_effort', None)} {getattr(kept, 'temperature', None)}")
    harness.check("a model bound with nothing is passed as it is", chain_runner._keep_bound_options(model) is model)


def test_the_tag_ask_lists_no_other_run_s_tags_defect():
    # Shown other runs' tags, a call with no thinking copied the most used ones onto unrelated runs ("automation" on a
    # photosynthesis article), each copy raising their count; the ask now lists none, and the program writes a near
    # copy of an existing tag in its spelling
    from unittest import mock

    from prompts import write_messages
    from services import run_tags_service

    with mock.patch.object(run_tags_service, "list_tag_inventory", return_value=[("automation", 34), ("gateway", 2)]):
        ask = write_messages.render_tag_ask()
    harness.check("no other run's tag is named", "automation" not in ask and "gateway" not in ask, ask)
    harness.check("a near copy still takes the existing tag's spelling",
                  run_tags_service.match_inventory_tags(["Gateways", "photosynthesis"], ["gateway"], 3)
                  == ["gateway", "photosynthesis"])


def test_a_tags_write_that_fails_part_way_leaves_the_old_tags_readable_defect():
    # Two jobs writing one run's tags left a file holding both, which no reader read, and the job stopped on it. The
    # file is now written whole beside it and put in its place at once
    import tempfile
    from pathlib import Path
    from unittest import mock

    from services import run_tags_service
    from utils import json_store_utils

    folder = Path(tempfile.mkdtemp())
    run_tags_service.write_tags(folder, ["gateway"])
    with mock.patch.object(json_store_utils.json, "dump", side_effect=OSError("disk gone")):
        try:
            run_tags_service.write_tags(folder, ["gateway", "refund rules"])
        except OSError:
            pass
    harness.check("the old tags still read, and no half-written file is left", run_tags_service.read_tags(folder)
                  == ["gateway"] and not list(folder.glob("*.tmp")), str(list(folder.iterdir())))
