"""Phase 7's blocks, batches and edits: a worker cleans up or rewrites the published document
by numbered blocks, a batch of at least `documentReshaping.batchTokens` at a time."""

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx
import openai
from langchain_core.messages import AIMessage, HumanMessage

from support import fakes, frame_batch_fixtures, harness

from agent_client.agent_errors import ControlStop
import config
from graph.nodes import reshape_document_node
from models import output_contract
from models.run_paths import RunPaths
from services import document_reshaping_service
from services import document_folder_service
from services import document_pass_service
from services import host_model_service
from services import phase_file_service
from services import run_artifact_service
from services import settings_service
from services import web_api_service
from utils import cleanup_block_utils
from utils import native_stderr_utils

DOCUMENT = """# Gateway guide

Created by Jonas, last updated by Jonas

The router picks the ledger shard from the tenant id.

```
route --tenant 7
```

## Cache

![the cache](resources/figure-01.png)
*Figure 1: the cache*

Responses are cached for an hour.

- Like · Share · Watch
"""


def test_a_document_is_cut_into_headings_paragraphs_and_whole_code_blocks():
    texts = [block["text"] for block in cleanup_block_utils.list_blocks(DOCUMENT)]
    harness.check("each heading, paragraph and code block is a block", texts == [
        "# Gateway guide", "Created by Jonas, last updated by Jonas",
        "The router picks the ledger shard from the tenant id.", "```\nroute --tenant 7\n```", "## Cache",
        "![the cache](resources/figure-01.png)\n*Figure 1: the cache*", "Responses are cached for an hour.",
        "- Like · Share · Watch"], str(texts))
    harness.check("the blocks join back into the document", cleanup_block_utils.join_blocks(
        cleanup_block_utils.list_blocks(DOCUMENT)) == DOCUMENT.strip() + "\n")


def test_a_batch_holds_its_least_size_and_ends_before_the_next_heading():
    blocks = cleanup_block_utils.list_blocks(DOCUMENT)
    # 60, so neither section passes twice the size, where a stretch with no heading is cut anyway
    batches = cleanup_block_utils.split_batches(blocks, 60)
    harness.check("the first batch runs to the heading after 60 characters", batches == [[1, 2, 3, 4], [5, 6, 7, 8]],
                  str(batches))
    harness.check("a large least size is one batch", cleanup_block_utils.split_batches(blocks, 100_000) ==
                  [[1, 2, 3, 4, 5, 6, 7, 8]])


def test_a_long_stretch_with_no_heading_is_cut_at_twice_the_batch_size_defect():
    # Defect: a knowledge pass's part 14 held 1,221 blocks, about 265,000 tokens, since a part ended only before a
    # heading; its first call was refused by a model loaded with 183,040 and the run ended. Fix: past twice the size, cut
    blocks = [{"id": 1, "text": "# Glossary"}, *[{"id": number, "text": "A term and what it means." * 4}
                                                 for number in range(2, 202)], {"id": 202, "text": "## Next"}]
    batches = cleanup_block_utils.split_batches(blocks, 1_000)
    sizes = [sum(len(blocks[block_id - 1]["text"]) for block_id in batch) for batch in batches]
    harness.check("no batch passes twice its size by more than one block", max(sizes) <= 2_000 + 100, str(sizes))
    harness.check("every block stays, in order", [block_id for batch in batches for block_id in batch]
                  == list(range(1, 203)), str(batches))
    harness.check("a heading after the least size still starts a batch", batches[-1][0] == 202 or 202 in batches[-1],
                  str(batches[-1]))


def test_edits_remove_replace_and_insert_and_each_refused_edit_says_why():
    blocks = cleanup_block_utils.list_blocks(DOCUMENT)
    edits = [{"action": "remove", "block": "B2"},
             {"action": "replace", "block": "B6", "last_block": "B7", "text": "Responses are cached."},
             {"action": "insert_after", "block": "B3", "text": "Shards are numbered from 1."},
             {"action": "remove", "block": "B2"},
             {"action": "replace", "block": "B1", "text": ""}]
    edited, refused, next_id = cleanup_block_utils.apply_edits(blocks, [1, 2, 3, 4, 5, 6, 7, 8], edits, 9)
    texts = [block["text"] for block in edited]
    harness.check("a replace that drops a figure line is refused, the blocks kept",
                  any("figure line" in line for line in refused) and "Responses are cached for an hour." in texts,
                  str(refused))
    harness.check("the noise block is removed", "Created by Jonas, last updated by Jonas" not in texts)
    harness.check("the new block stands after its block with the next id",
                  texts[texts.index("The router picks the ledger shard from the tenant id.") + 1]
                  == "Shards are numbered from 1." and next_id == 10, str(texts))
    harness.check("an edit of a removed block is refused by name",
                  any("B2 was already removed" in line for line in refused), str(refused))
    harness.check("a replace with no text is refused", any("needs the text" in line for line in refused), str(refused))


def test_phase_7_is_off_unless_the_run_asks_for_a_clean_up_or_a_rewrite():
    ctx = frame_batch_fixtures.load_run_context(fakes.scripted(), count=1)
    state = reshape_document_node.reshape_document_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("off with no request", state["phases"]["document_reshaping"] == "off", str(state))
    harness.check("nothing is cut", not document_reshaping_service.build_reshaping_dir(ctx.paths).exists())

    document_pass_service.write_document_pass(ctx.paths.output_root, "rewrite", "  remove every Jira link  ")
    harness.check("the request is read back with its wishes", document_pass_service.read_document_pass(
        ctx.paths.output_root) == {"pass": "rewrite", "wishes": "remove every Jira link", "source": "original"})
    state = reshape_document_node.reshape_document_node({"phases": {}}, {"configurable": {"ctx": ctx}})
    harness.check("held while phase 4 has published no document", state["phases"]["document_reshaping"] == "held",
                  str(state))
    harness.check("a pass that never ran is not done", not document_pass_service.is_pass_done(ctx.paths.output_root))
    (ctx.paths.output_root / output_contract.RUN_DOCUMENT_PASS_FILE).write_text('{"pass": "polish"}', encoding="utf-8")
    harness.check("an unknown pass is no request", document_pass_service.read_document_pass(ctx.paths.output_root) is None)


def test_the_tools_are_the_outline_a_read_the_edits_the_close_the_prune_and_the_report():
    names = [spec["function"]["name"] for spec in document_reshaping_service.build_tool_specs()]
    harness.check("six tools", names == ["read_outline", "read_blocks", "edit_blocks", "finish_batch",
                                         "prune_results", "report_problem"], str(names))


def test_a_native_line_on_the_error_stream_is_kept_off_inside_the_block_only():
    # A C library's line written straight onto file 2 (Tesseract's "Image too small to scale!!") reached the console
    child = subprocess.run([sys.executable, "-c", (
        "import os, sys; sys.path.insert(0, %r)\n"
        "from utils import native_stderr_utils\n"
        "with native_stderr_utils.hide_native_stderr():\n"
        "    os.write(2, b'hidden line\\n')\n"
        "os.write(2, b'shown line\\n')\n") % str(native_stderr_utils.__file__.rsplit("/utils/", 1)[0])],
        capture_output=True, text=True, timeout=60)
    harness.check("the line inside is hidden, the one after is shown",
                  "hidden line" not in child.stderr and "shown line" in child.stderr, child.stderr)
    harness.check("this process's error stream is still file 2", os.fstat(2) is not None)


def test_each_pass_keeps_the_wishes_it_was_last_queued_with_and_the_settings_show_them_in_a_box():
    root = Path(tempfile.mkdtemp())
    (root / "default.json").write_text("{}", encoding="utf-8")
    (root / "local.json").write_text("{}", encoding="utf-8")
    previous = config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME = root / "default.json", root / "local.json", ""
    # the suite reaches no model host: none answers when the settings list asks the hosts for their models
    settings_service.set_host_lister(lambda provider, base_url: None)
    try:
        web_api_service._save_document_wishes("cleanup", "  remove every Jira link ")
        web_api_service._save_document_wishes("rewrite", "write lists as tables")
        web_api_service._save_document_wishes("knowledge", "keep the field tables")
        web_api_service._save_document_wishes("", "no pass, nothing saved")
        defaults = web_api_service.read_run_defaults()
        rows = {row["key"]: row for row in settings_service.list_settings()}
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PROFILE_NAME = previous
        settings_service.set_host_lister(host_model_service.list_host_models)
    harness.check("each pass's wishes are saved apart, trimmed", defaults["document_wishes"] == {
        "cleanup": "remove every Jira link", "rewrite": "write lists as tables", "knowledge": "keep the field tables"},
                  str(defaults))
    harness.check("in both default settings files", json.loads((root / "local.json").read_text())["pdf"]
                  == json.loads((root / "default.json").read_text())["pdf"])
    harness.check("the Settings page edits each in a box of its own",
                  rows["pdf.cleanupWishes"]["kind"] == rows["pdf.rewriteWishes"]["kind"]
                  == rows["pdf.knowledgeWishes"]["kind"] == "long-text"
                  and rows["pdf.ocrEngine"]["kind"] == "text", str(rows["pdf.cleanupWishes"]))


def test_a_pass_asked_again_starts_from_the_original_or_the_current_document_as_asked():
    # The owner: a second pass read the document the last pass left, with no say; it now starts from the original
    # (the document before phase 7's first pass) unless the current document is asked for
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(DOCUMENT, encoding="utf-8")
    document_reshaping_service.start_pass(paths=paths, asked={"pass": "knowledge", "wishes": "", "source": "original"})
    knowledge = "# Gateway guide\n\n## Caching\n\nThe gateway keeps each response for sixty minutes.\n"
    document.write_text(knowledge, encoding="utf-8")
    document_pass_service.build_report_path(folder).write_text("done\n", encoding="utf-8")

    from_original = document_reshaping_service.start_pass(paths=paths, asked={"pass": "cleanup", "wishes": "",
                                                                            "source": "original"})
    harness.check("the original: the export's text, the knowledge document set aside",
                  "Like · Share" in cleanup_block_utils.join_blocks(from_original["blocks"])
                  and "sixty minutes" not in cleanup_block_utils.join_blocks(from_original["blocks"]),
                  str(from_original["blocks"][:3]))
    document.write_text(knowledge, encoding="utf-8")
    document_pass_service.build_report_path(folder).write_text("done\n", encoding="utf-8")
    from_current = document_reshaping_service.start_pass(paths=paths, asked={"pass": "rewrite", "wishes": "",
                                                                           "source": "current"})
    harness.check("the current: the document the last pass left",
                  cleanup_block_utils.join_blocks(from_current["blocks"]).strip() == knowledge.strip(),
                  str(from_current["blocks"]))
    # each finished pass keeps the document it left, so the versions list the knowledge document after it
    versions = document_pass_service.list_document_versions(output_root=folder, document=document)
    harness.check("the versions: the original, then each pass's own result",
                  [version["label"] for version in versions]
                  == ["original", "after knowledge reshaping", "after clean up", "after rewrite (not finished)"]
                  and "sixty minutes" in (folder / versions[1]["path"]).read_text(encoding="utf-8"), str(versions))
    document_pass_service.write_document_pass(folder, "cleanup", "", "")
    harness.check("a request naming no source starts from the original",
                  document_pass_service.read_document_pass(folder)["source"] == "original")


def test_each_version_of_the_document_is_listed_and_downloaded_with_its_figures():
    # Phase 7 kept the document before each pass, but nothing listed or offered the earlier versions; now the run's
    # detail lists every version and each, or all of them, downloads as a zip with the figures
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(DOCUMENT, encoding="utf-8")
    document = document_folder_service.build_document_path(paths)
    (document.parent / "resources").mkdir(parents=True)
    (document.parent / "resources" / "figure-01.png").write_bytes(b"png")
    document.write_text(DOCUMENT, encoding="utf-8")
    document_reshaping_service.start_pass(paths=paths, asked={"pass": "cleanup", "wishes": ""})
    document.write_text(DOCUMENT.replace("- Like · Share · Watch\n", ""), encoding="utf-8")
    document_pass_service.build_report_path(folder).write_text("done\n", encoding="utf-8")
    document_reshaping_service.start_pass(paths=paths, asked={"pass": "rewrite", "wishes": ""})
    document.write_text("# Gateway guide\n\nRewritten.\n\n![the cache](resources/figure-01.png)\n", encoding="utf-8")

    versions = document_pass_service.list_document_versions(output_root=folder, document=document)
    harness.check("the original, after the clean up, after the unfinished rewrite",
                  [version["label"] for version in versions]
                  == ["original", "after clean up", "after rewrite (not finished)"], str(versions))
    first = (folder / versions[0]["path"]).read_text(encoding="utf-8")
    second = (folder / versions[1]["path"]).read_text(encoding="utf-8")
    harness.check("each version holds its own text", "Like · Share" in first and "Like · Share" not in second
                  and versions[2]["path"] == document.relative_to(folder).as_posix())
    data, name = run_artifact_service.build_versions_download(folder, None)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = sorted(archive.namelist())
    stem = document.stem
    harness.check("all three versions beside the figure they link", names == sorted([
        f"{stem}-1-knowledge-original.md", f"{stem}-2-after-clean-up.md", f"{stem}-3-after-rewrite-not-finished.md",
        "resources/figure-01.png"]) and name == f"{stem}-versions.zip", str(names))
    data, name = run_artifact_service.build_versions_download(folder, 2)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = sorted(archive.namelist())
    harness.check("one version alone, with its figure", names == [f"{stem}-2-after-clean-up.md", "resources/figure-01.png"]
                  and name == f"{stem}-2-after-clean-up.zip", str(names))


def test_an_edit_outside_the_batch_is_refused_defect():
    # A worker of the first batch edited a block of the next batch, which another conversation then read changed
    # under it; an edit names a block of its own batch only
    blocks = cleanup_block_utils.list_blocks(DOCUMENT)
    edited, refused, _next_id = cleanup_block_utils.apply_edits(blocks, [1, 2, 3, 4],
                                                                [{"action": "remove", "block": "B8"}], 9)
    harness.check("refused, nothing changed", refused and len(edited) == len(blocks), str(refused))


def test_a_pass_lists_its_parts_with_status_calls_and_time_for_the_progress_bar_defect():
    # Defect: phase 7's progress said "this phase records no steps to count", though its parts are cut before it starts.
    # Fix: each part is listed with its status, blocks, model calls and time
    root = Path(tempfile.mkdtemp())
    cleanup = root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    (cleanup / "requests").mkdir(parents=True)
    (cleanup / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1, 2], [3], [4, 5, 6]],
                                                    "closed": {"1": "two sections"}}), encoding="utf-8")
    rows = [{"at": 100.0, "call": 1, "text": "", "tools": [{"name": "read_knowledge_outline"}]},
            {"at": 110.0, "call": 1, "tool": "read_knowledge_outline", "result": "(no section)"},
            {"at": 160.0, "call": 2, "text": "", "tools": [{"name": "finish_batch"}]}]
    (cleanup / "batch-001.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (cleanup / "batch-002.jsonl").write_text(json.dumps({"at": 200.0, "call": 1, "text": "", "tools": []}) + "\n",
                                             encoding="utf-8")

    live = document_pass_service.describe_parts(root, is_live=True, now=260.0)
    harness.check("part 1 done, part 2 running, part 3 waiting, each with its blocks, calls and time",
                  [(part["number"], part["status"], part["blocks"], part["calls"], part["seconds"]) for part in live]
                  == [(1, "done", 2, 2, 60.0), (2, "running", 1, 1, 60.0), (3, "waiting", 3, 0, 0.0)], str(live))
    stopped = document_pass_service.describe_parts(root, is_live=False, now=999.0)
    harness.check("with no run going, a started part not closed is stopped, timed to its last row",
                  (stopped[1]["status"], stopped[1]["seconds"]) == ("stopped", 0.0), str(stopped[1]))
    harness.check("no pass cut yet, no part", document_pass_service.describe_parts(Path(tempfile.mkdtemp()), True, 1.0) == [])


def test_a_part_is_sized_in_tokens_and_sections_defect():
    # Defect: the parts table showed each part's size as "Blocks", a word the person sees nowhere else on the page
    # Fix: each part carries its estimated tokens and its sections, read from the document the pass started from
    root = Path(tempfile.mkdtemp())
    cleanup = root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    cleanup.mkdir(parents=True)
    (cleanup / "before.md").write_text("# Gateway\n\n" + "a" * 70 + "\n\n## Routing\n\n" + "b" * 35 + "\n",
                                       encoding="utf-8")
    (cleanup / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1, 2], [3, 4]], "closed": {}}),
                                        encoding="utf-8")
    parts = document_pass_service.describe_parts(root, is_live=False, now=1.0)
    per_token = config.settings.document_reshaping.chars_per_token
    harness.check("each part's tokens and its one section",
                  [(part["tokens"], part["sections"]) for part in parts]
                  == [(round(79 / per_token), 1), (round(45 / per_token), 1)], str(parts))


def test_a_running_part_shows_its_thinking_so_far_defect():
    # Defect: a part's model call thought 12 minutes with nothing on the page, and the owner could not tell it from a
    # stuck one. Fix: the running part names its thinking file, its words and how long since its last step
    root = Path(tempfile.mkdtemp())
    cleanup = root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    (cleanup / "thinking").mkdir(parents=True)
    (cleanup / "thinking" / "part-001.md").write_text("I compare the router section with the new facts", encoding="utf-8")
    (cleanup / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1], [2]], "closed": {}}),
                                        encoding="utf-8")
    (cleanup / "batch-001.jsonl").write_text(json.dumps({"at": 100.0, "call": 1, "text": "", "tools": []}) + "\n",
                                             encoding="utf-8")
    parts = document_pass_service.describe_parts(root, is_live=True, now=820.0)
    harness.check("part 1 thinks: its file, 9 words, 12 minutes since its last step",
                  (parts[0]["thinking"], parts[0]["thinking_words"], parts[0]["thinking_seconds"])
                  == ("07_reshaping/thinking/part-001.md", 9, 720.0), str(parts[0]))
    harness.check("a waiting part has no thinking", parts[1]["thinking"] == "", str(parts[1]))


def test_a_part_reply_streams_its_thinking_to_the_part_file_defect():
    # Defect: a part's reply was asked whole, so nothing of its thinking reached the disk while it ran. Fix: the reply
    # is streamed and its thinking file made at the call's start
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    model = fakes.ScriptedChatModel(script=[AIMessage(content="done")])
    reply = document_reshaping_service.stream_reply(paths, model, [HumanMessage(content="go")], 3,
                                                  logging.getLogger("test"))
    harness.check("the reply comes back and the part's thinking file exists",
                  "done" in str(reply.content)
                  and document_pass_service.build_part_thinking_path(folder, 3).is_file(), str(reply))


def test_a_part_s_thinking_and_every_draft_are_listed_for_the_reader_defect():
    # Defect: the reader opens only listed files, and a part's thinking and the last part's draft were not listed, so
    # their links in the table of parts opened nothing. Fix: both are listed
    folder = Path(tempfile.mkdtemp())
    (folder / "07_reshaping" / "thinking").mkdir(parents=True)
    (folder / "07_reshaping" / "parts").mkdir(parents=True)
    (folder / "07_reshaping" / "thinking" / "part-001.md").write_text("thinking", encoding="utf-8")
    (folder / "07_reshaping" / "parts" / "part-001.md").write_text("# Gateway\n", encoding="utf-8")
    listed = [item["path"] for group in run_artifact_service.list_artifacts(folder) for item in group["items"]]
    harness.check("the thinking and the draft are listed",
                  {"07_reshaping/thinking/part-001.md", "07_reshaping/parts/part-001.md"} <= set(listed), str(listed))


def test_a_part_s_live_panel_lists_its_steps_and_thinking_defect():
    # Defect: the owner could see only a part's thinking word count, never what its worker did or thinks. Fix: each
    # part's steps with what the program answered, and its thinking so far
    root = Path(tempfile.mkdtemp())
    cleanup = root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    (cleanup / "thinking").mkdir(parents=True)
    (cleanup / "thinking" / "part-001.md").write_text("I merge the router facts", encoding="utf-8")
    rows = [{"at": 1.0, "call": 1, "text": "", "tools": [{"name": "read_section", "args": {"section": "K1"}}]},
            {"at": 2.0, "call": 1, "tool": "read_section", "result": "K1 ## Router"},
            {"at": 3.0, "call": 2, "text": "", "tools": [{"name": "write_sections", "args": {"edits": []}}]}]
    (cleanup / "batch-001.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    activity = document_pass_service.describe_part_activity(root, 1, now=10.0)
    harness.check("two steps, the first answered, the second not yet, and the thinking",
                  [(step["tool"], step["result"]) for step in activity["steps"]]
                  == [("read_section", "K1 ## Router"), ("write_sections", "")]
                  and activity["thinking"] == "I merge the router facts", str(activity))


def test_a_part_s_speed_is_the_host_s_count_or_else_estimated_defect():
    # Defect: the owner could not see how fast the model writes. Fix: the host's count once a call ends; while it
    # runs, or when the host reported none, an estimate from the characters written so far
    root = Path(tempfile.mkdtemp())
    cleanup = root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    (cleanup / "thinking").mkdir(parents=True)
    (cleanup / "thinking" / "part-001.md").write_text("a" * 700, encoding="utf-8")
    call = cleanup / "thinking" / "part-001.json"
    call.write_text(json.dumps({"started": 100.0}), encoding="utf-8")
    running = document_pass_service.describe_part_activity(root, 1, now=110.0)["speed"]
    per_token = config.settings.document_reshaping.chars_per_token
    harness.check("while it writes: estimated from 700 characters in 10 seconds",
                  running == {"tokens_per_second": round(700 / per_token / 10, 1), "is_estimated": True,
                              "prompt_tokens": 0, "is_writing": True}, str(running))
    reading = document_pass_service.describe_part_activity(root, 1, now=110.0)["doing"]
    harness.check("before the host's first piece the worker reads the prompt",
                  reading == {"what": "reading the prompt", "since": 100.0}, str(reading))
    call.write_text(json.dumps({"started": 100.0, "first_piece": 105.0}), encoding="utf-8")
    thinking = document_pass_service.describe_part_activity(root, 1, now=110.0)["doing"]
    harness.check("after it, the worker thinks", thinking == {"what": "thinking", "since": 105.0}, str(thinking))
    (cleanup / "thinking" / "part-001.md").write_text("a" * 700 + "\n\n[the answer so far]\n{", encoding="utf-8")
    answering = document_pass_service.describe_part_activity(root, 1, now=110.0)["doing"]
    harness.check("once its answer streams, it writes its answer", answering["what"] == "writing its answer",
                  str(answering))
    call.write_text(json.dumps({"started": 100.0, "first_piece": 105.0, "ended": 140.0, "prompt_tokens": 9000,
                                "output_tokens": 320, "tokens_per_second": 8.0}), encoding="utf-8")
    ended = document_pass_service.describe_part_activity(root, 1, now=150.0)
    harness.check("once the call ended, the program runs the tools",
                  ended["doing"] == {"what": "the program runs its tools", "since": 140.0}, str(ended["doing"]))
    harness.check("once it ended: the host's count", ended["speed"] == {
        "tokens_per_second": 8.0, "is_estimated": False, "prompt_tokens": 9000, "is_writing": False}, str(ended["speed"]))


def test_a_new_part_size_cuts_the_parts_not_started_and_keeps_the_running_one_defect():
    # Defect: a part size saved while a pass ran reached none of its parts. Fix: before each part the parts no worker
    # has started are cut again at the size the settings hold now; a part closed or under way stays whole
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    blocks = [{"id": number, "text": ("## Heading\n" if number % 2 else "") + "x" * 350} for number in range(1, 13)]
    state = {"blocks": blocks, "batches": [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]], "closed": {"1": "done"}}
    document_reshaping_service.build_batch_log_path(paths, 2).parent.mkdir(parents=True, exist_ok=True)
    document_reshaping_service.build_batch_log_path(paths, 2).write_text("{}\n", encoding="utf-8")
    per_token = config.settings.document_reshaping.chars_per_token
    is_changed = document_reshaping_service.split_open_parts(paths, state, round(700 / per_token))
    harness.check("parts 1 (closed) and 2 (under way) kept, part 3 cut in two at the new size",
                  is_changed and state["batches"] == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10], [11, 12]], str(state["batches"]))
    harness.check("the same size again changes nothing",
                  not document_reshaping_service.split_open_parts(paths, state, round(700 / per_token)), "")


def test_a_run_made_before_the_rename_keeps_reading_its_phase_7_folder_defect():
    # Defect: phase 7 was renamed document reshaping, its folder `07_reshaping`; a run made before keeps `07_cleanup`
    # and its record names the phase `document_cleanup`. Fix: both are read under the new names
    folder = Path(tempfile.mkdtemp())
    (folder / "07_cleanup").mkdir()
    (folder / "07_cleanup" / "report.md").write_text("# Report\n", encoding="utf-8")
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    harness.check("the old folder is phase 7's folder", document_pass_service.build_reshaping_dir(folder)
                  == folder / "07_cleanup", str(document_pass_service.build_reshaping_dir(folder)))
    harness.check("its report is phase 7's file", phase_file_service.build_phase_file_path(paths, 7)
                  == folder / "07_cleanup" / "report.md", str(phase_file_service.build_phase_file_path(paths, 7)))
    harness.check("the old phase name reads as the new one",
                  output_contract.RETIRED_STEP_NAMES["document_cleanup"] == "document_reshaping", "")
    new = Path(tempfile.mkdtemp())
    harness.check("a new run gets the new folder", document_pass_service.build_reshaping_dir(new) == new / "07_reshaping",
                  str(document_pass_service.build_reshaping_dir(new)))


def test_a_part_call_broken_while_the_host_lost_its_model_is_asked_again_defect():
    # Defect: LM Studio restarted under a part with no model; the stream broke and the run crashed. Fix: the call waits
    # for the model to be back and is asked once more; a failure while the model is loaded is raised as before
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    calls = []

    def read_streamed(bound, messages, live, on_first_piece=None):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("peer closed connection")
        return AIMessage(content="done")

    saved = (document_reshaping_service.chain_runner.read_streamed_reply,
             document_reshaping_service.host_return_service.wait_for_lost_model)
    document_reshaping_service.chain_runner.read_streamed_reply = read_streamed
    try:
        document_reshaping_service.host_return_service.wait_for_lost_model = lambda phase, on_wait=None:True
        reply = document_reshaping_service.stream_reply(paths, None, [], 1, logging.getLogger("test"))
        harness.check("asked again once the model is back", reply.content == "done" and len(calls) == 2, str(calls))
        calls.clear()
        document_reshaping_service.host_return_service.wait_for_lost_model = lambda phase, on_wait=None:False
        try:
            document_reshaping_service.stream_reply(paths, None, [], 1, logging.getLogger("test"))
            harness.check("a failure with the model loaded is raised", False, "no error")
        except ConnectionError:
            harness.check("a failure with the model loaded is raised", len(calls) == 1, str(calls))
    finally:
        (document_reshaping_service.chain_runner.read_streamed_reply,
         document_reshaping_service.host_return_service.wait_for_lost_model) = saved


def test_a_host_error_while_the_model_works_is_asked_again_a_bounded_number_of_times_defect():
    # Defect: one engine error on a part's long prompt ended the job at once, though a short call still passed. Fix: the
    # call gets three tries; a host error on each of them is raised, so a prompt that always fails cannot run for ever
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    request = httpx.Request("POST", "http://host.invalid/v1/chat/completions")
    calls = []

    def read_streamed(bound, messages, live, on_first_piece=None):
        calls.append(1)
        if len(calls) < answer_at:
            raise openai.APIError("Compute error", request=request, body=None)
        return AIMessage(content="done")

    saved = (document_reshaping_service.chain_runner.read_streamed_reply,
             document_reshaping_service.host_return_service.wait_for_lost_model)
    document_reshaping_service.chain_runner.read_streamed_reply = read_streamed
    document_reshaping_service.host_return_service.wait_for_lost_model = lambda phase, on_wait=None:False
    try:
        answer_at = 3
        reply = document_reshaping_service.stream_reply(paths, None, [], 1, logging.getLogger("test"))
        harness.check("the third try answers", reply.content == "done" and len(calls) == 3, str(calls))
        calls.clear()
        answer_at = 99
        try:
            document_reshaping_service.stream_reply(paths, None, [], 1, logging.getLogger("test"))
            harness.check("the error of the last try is raised", False, "no error")
        except openai.APIError:
            harness.check("the error of the last try is raised", len(calls) == 3, str(calls))
    finally:
        (document_reshaping_service.chain_runner.read_streamed_reply,
         document_reshaping_service.host_return_service.wait_for_lost_model) = saved


def test_a_part_waiting_for_a_broken_model_says_so_and_not_writing_defect():
    # Defect: LM Studio listed its model READY while every call failed with "Compute error"; the part waited 23 minutes
    # and its row read "writing for 22m 59s, 0 words of thinking". Fix: the wait is written to the part's call file, and
    # the parts table and the live panel say the part waits for the model host, with the host's error
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    cleanup = folder / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    cleanup.mkdir(parents=True)
    (cleanup / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1]], "closed": {}}),
                                        encoding="utf-8")
    (cleanup / "batch-001.jsonl").write_text(json.dumps({"at": 100.0, "call": 1, "text": "", "tools": []}) + "\n",
                                             encoding="utf-8")
    seen, calls = {}, []

    def read_streamed(bound, messages, live, on_first_piece=None):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("Compute error.")
        return AIMessage(content="done")

    def wait(phase, on_wait=None):
        on_wait("the model answers every call with an error: Compute error.")
        seen["part"] = document_pass_service.describe_parts(folder, is_live=True, now=10_000_000_000.0)[0]
        seen["doing"] = document_pass_service.describe_part_activity(folder, 1, now=10_000_000_000.0)["doing"]
        return True

    saved = (document_reshaping_service.chain_runner.read_streamed_reply,
             document_reshaping_service.host_return_service.wait_for_lost_model)
    document_reshaping_service.chain_runner.read_streamed_reply = read_streamed
    document_reshaping_service.host_return_service.wait_for_lost_model = wait
    try:
        reply = document_reshaping_service.stream_reply(paths, None, [], 1, logging.getLogger("test"))
    finally:
        (document_reshaping_service.chain_runner.read_streamed_reply,
         document_reshaping_service.host_return_service.wait_for_lost_model) = saved
    harness.check("the reply comes once the model works", reply.content == "done", str(reply))
    harness.check("while it waits the part names the host's error, not its thinking",
                  "Compute error" in seen["part"].get("host_wait", "") and seen["part"]["thinking"] == "",
                  str(seen.get("part")))
    harness.check("and its live panel says it waits for the model host",
                  seen["doing"]["what"].startswith("waiting for the model host"), str(seen.get("doing")))
    after = document_pass_service.describe_part_activity(folder, 1, now=10_000_000_000.0)["doing"]
    harness.check("once the model works again the wait is gone", not after["what"].startswith("waiting"), str(after))


def test_a_done_part_names_the_document_it_left_defect():
    # Defect: a click on a part in the parts table opened what its worker was sent, where the owner wanted the document
    # Fix: each part names the document as it left it, empty until it is done, which the click opens
    root = Path(tempfile.mkdtemp())
    cleanup = root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    (cleanup / "parts").mkdir(parents=True)
    (cleanup / "parts" / "part-001.md").write_text("# Gateway\n", encoding="utf-8")
    (cleanup / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1], [2]], "closed": {"1": "ok"}}),
                                        encoding="utf-8")
    parts = document_pass_service.describe_parts(root, is_live=True, now=1.0)
    harness.check("part 1 names its draft, part 2 none yet",
                  [part["document"] for part in parts] == ["07_reshaping/parts/part-001.md", ""], str(parts))


def test_each_part_counts_its_figures_and_its_worker_s_prunes():
    # The owner asked the parts table for each part's figures and how often its worker pruned its conversation
    root = Path(tempfile.mkdtemp())
    reshaping = root / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    reshaping.mkdir(parents=True)
    (reshaping / document_pass_service.BEFORE_FILE).write_text(
        "# Gateway\n\n![Router](resources/figure-01.png)\n\nThe router picks the shard.\n\n![Shards](resources/figure-02.png)\n",
        encoding="utf-8")
    (reshaping / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1, 2, 3, 4]], "closed": {}}),
                                          encoding="utf-8")
    rows = [{"call": 1, "text": "", "tools": [{"name": "prune_results"}], "at": 1.0},
            {"call": 1, "tool": "prune_results", "result": "dropped", "at": 2.0},
            {"call": 2, "tool": "read_section", "result": "K1", "at": 3.0},
            {"written_tokens": 400, "writing_seconds": 10.0, "at": 3.5},
            {"written_tokens": 200, "writing_seconds": 5.0, "at": 3.6}]
    (reshaping / "batch-001.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    part = document_pass_service.describe_parts(root, is_live=False, now=4.0)[0]
    harness.check("the part counts its two figures and its one prune", (part["figures"], part["prunes"]) == (2, 1), str(part))
    harness.check("its speed is its calls' written tokens over their writing time, 600 in 15 s",
                  part["tokens_per_second"] == 40.0 and part["calls"] == 1, str(part))


def test_a_clean_up_part_ends_at_the_page_s_stop_before_its_next_call_defect():
    # Defect: the page's Stop and Pause were never read in phase 7, so a pass ran on for hours after Stop was pressed.
    # Fix: a part reads the run's control file before each call; Stop ends it there, any other answer lets it go on
    document = "# Gateway guide\n\nThe router picks the ledger shard from the tenant id.\n"
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(document, encoding="utf-8")
    published = document_folder_service.build_document_path(paths)
    published.parent.mkdir(parents=True)
    published.write_text(document, encoding="utf-8")
    state = document_reshaping_service.start_pass(paths=paths, asked={"pass": "cleanup", "wishes": ""})
    model = fakes.scripted(fakes.tool_call("finish_batch", summary="nothing to remove"))
    try:
        document_reshaping_service.run_batch(paths=paths, state=state, number=1, model=model, log=logging.getLogger("t"),
                                             control_check=lambda: "stop")
        stopped = ""
    except ControlStop as stop:
        stopped = str(stop)
    harness.check("Stop ends the part before its first call", stopped == "stopped before call 1 of batch 1"
                  and model.calls == [], stopped)
    document_reshaping_service.run_batch(paths=paths, state=state, number=1, model=model, log=logging.getLogger("t"),
                                         control_check=lambda: None)
    harness.check("with nothing asked the part calls its model", len(model.calls) >= 1, str(model.calls))
