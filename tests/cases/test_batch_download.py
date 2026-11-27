"""Batch download: the Manager tab's Download zips the ticked runs' finished documents with their resources folders, as
phase 4 published them."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from models.run_paths import RunPaths
from services import batch_download_service
from services import document_folder_service
from services import run_status_service
from services import web_api_service


def _build_published_run(root: Path,
                         name: str,
                         title: str) -> Path:
    """A run folder with phase 4's document and its published folder: the document, two figures and provenance."""

    folder = root / name
    folder.mkdir(parents=True)
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    paths.summary.write_text(f"# {title}\n\nThe gateway refunds within a day.\n", encoding="utf-8")
    published = document_folder_service.build_document_dir(paths)
    (published / "resources").mkdir(parents=True)
    (published / f"{published.name}.md").write_text(f"# {title}\n", encoding="utf-8")
    (published / "resources" / "figure-01.png").write_bytes(b"png one")
    (published / "resources" / "figure-02.png").write_bytes(b"png two")
    (published / "provenance.json").write_text("{}", encoding="utf-8")

    return folder


def test_the_zip_holds_each_document_with_its_resources_and_names_the_runs_left_out():
    root = Path(tempfile.mkdtemp()) / "output"
    first = _build_published_run(root, "Gateway-review", "Gateway review")
    twin = _build_published_run(root, "Gateway-review-2", "Gateway review")
    empty = root / "Jonas-call"
    empty.mkdir()
    data, skipped = batch_download_service.build_documents_zip([first, twin, empty])
    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    top = min((name.split("/")[0] for name in names), key=len)
    harness.check("the document and its two figures, under the document's own folder name",
                  f"{top}/{top}.md" in names and f"{top}/resources/figure-01.png" in names
                  and f"{top}/resources/figure-02.png" in names, str(names))
    harness.check("provenance is not part of it", not any(name.endswith("provenance.json") for name in names), str(names))
    harness.check("a second run whose document has the same name is told apart by its run folder",
                  any(name.startswith(f"{top} (Gateway-review-2)/") for name in names), str(names))
    harness.check("a run with no published document is left out and named", skipped == ["Jonas-call"], str(skipped))


def test_a_run_with_several_versions_downloads_the_latest_the_original_or_all_as_asked():
    # The owner: the list's download gave one document per run with no word of which version; a run reshaped by phase 7
    # now lists its versions, and the zip holds the latest, the original or every version as asked
    root = Path(tempfile.mkdtemp()) / "output"
    reshaped = _build_published_run(root, "Gateway-review", "Gateway review")
    plain = _build_published_run(root, "Jonas-call", "Jonas call")
    cleanup = reshaped / "07_cleanup"
    cleanup.mkdir()
    (cleanup / "before.md").write_text("# Gateway review\n\nThe export as converted.\n", encoding="utf-8")
    (cleanup / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1]]}), encoding="utf-8")
    (cleanup / "report.md").write_text("# Document knowledge reshaping\n", encoding="utf-8")
    published = next(reshaped.glob("*/resources")).parent
    (published / f"{published.name}.md").write_text("# Gateway review\n\n## Refunds\n\nWithin a day.\n", encoding="utf-8")

    harness.check("the reshaped run lists two versions, the plain run one",
                  [len(batch_download_service.list_versions(folder)) for folder in (reshaped, plain)] == [2, 1])

    def read_zip(version: str) -> dict[str, str]:
        data, _skipped = batch_download_service.build_documents_zip([reshaped, plain], version)
        archive = zipfile.ZipFile(io.BytesIO(data))
        return {name: archive.read(name).decode("utf-8", "replace") for name in archive.namelist() if name.endswith(".md")}

    top = published.name
    latest, original, every = read_zip("latest"), read_zip("original"), read_zip("all")
    harness.check("the latest: the reshaped document under its own name",
                  "## Refunds" in latest.get(f"{top}/{top}.md", ""), str(sorted(latest)))
    harness.check("the original: the document before any change, named as the original",
                  "The export as converted." in original.get(f"{top}/{top}-1-knowledge-original.md", ""), str(sorted(original)))
    harness.check("all: every version, numbered with its label, and the plain run's one document as it is",
                  {f"{top}/{top}-1-knowledge-original.md", f"{top}/{top}-2-after-knowledge-reshaping.md"} <= set(every)
                  and "jonas-call/jonas-call.md" in every,
                  str(sorted(every)))


def test_the_page_downloads_the_ticked_runs_and_refuses_when_nothing_can_be_downloaded():
    root = Path(tempfile.mkdtemp()) / "output"
    _build_published_run(root, "Gateway-review", "Gateway review")
    (root / "Jonas-call").mkdir()
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = root
    try:
        client = TestClient(web_api_service.build_app())
        parts = client.get("/api/download/parts").json()
        harness.check("one part offered, the document", [part["key"] for part in parts] == ["document"], str(parts))
        answer = client.post("/api/download/documents", json={"parts": ["document"], "runs": [
            {"root": "output", "name": "Gateway-review"}, {"root": "output", "name": "Jonas-call"}]})
        harness.check("a zip of one document, the run left out named in a header",
                      answer.status_code == 200 and answer.headers["X-Skipped-Runs"] == "Jonas-call"
                      and "documents-1.zip" in answer.headers["Content-Disposition"], answer.text[:200])
        none = client.post("/api/download/documents", json={"parts": ["document"],
                                                              "runs": [{"root": "output", "name": "Jonas-call"}]})
        harness.check("no ticked run with a document: 409", none.status_code == 409, none.text)
        unknown = client.post("/api/download/documents", json={"parts": ["frames"],
                                                                 "runs": [{"root": "output", "name": "Gateway-review"}]})
        harness.check("an unknown part: 409", unknown.status_code == 409, unknown.text)
        versions = client.post("/api/download/document-versions", json={"runs": [
            {"root": "output", "name": "Gateway-review"}, {"root": "output", "name": "Jonas-call"}]}).json()
        harness.check("each run's versions, none for a run with no document",
                  [len(run["versions"]) for run in versions] == [1, 0], str(versions))
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)

