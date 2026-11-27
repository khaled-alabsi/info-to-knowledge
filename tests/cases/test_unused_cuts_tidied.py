"""The cuts no figure uses are moved out of the way, and the cuts a figure uses are not.

A run that finishes leaves the cut files its chapters made in `04_writing/images/cuts/`. Measured over eleven run
folders: 152 of them were named by no section and no block, most undone by the worker, the rest cut again and never
placed. The run's last step moves those into the run folder's `.trash/cuts/` and records each move in the store.

The one thing that must never happen is moving a cut a figure rests on, so each test below places a cut and checks it
is still where the document expects it."""

import argparse
import json
import tempfile
import types
from pathlib import Path

from fastapi.testclient import TestClient

from support import harness

from cli import tidy_command
from models import output_contract
from models.run_paths import RunPaths
from services import cut_tidy_service
from services import run_status_service
from services import web_api_service

CUTS_DIR = "04_writing/images/cuts"


def _build_run(cuts: dict[str, dict], blocks: list[dict], sections: dict[str, str]) -> RunPaths:
    """A run folder holding an image store, the cut files it names, and the section markdown of a written chapter."""

    root = Path(tempfile.mkdtemp()) / "run"
    (root / CUTS_DIR).mkdir(parents=True)
    (root / "04_writing" / "sections").mkdir(parents=True, exist_ok=True)
    for cut_id, cut in cuts.items():
        (root / str(cut["file"])).write_bytes(b"png")
    for name, text in sections.items():
        (root / "04_writing" / "sections" / f"{name}.md").write_text(text, encoding="utf-8")
    (root / "04_writing" / "images.json").write_text(
        json.dumps({"cuts": cuts, "blocks": blocks, "removedUnreferenced": []}), encoding="utf-8")

    return RunPaths(repository=root, module="run", output_root_override=root)


def _build_cut(cut_id: str, frame: str, discarded: str | None = None) -> dict:
    return {"frame": frame, "file": f"{CUTS_DIR}/{cut_id}.png", **({"discarded": discarded} if discarded else {})}


def test_a_cut_a_section_places_is_kept() -> None:
    paths = _build_run(cuts={"cut-001": _build_cut("cut-001", "frame-010")}, blocks=[],
                       sections={"heading-001-1": "![a form](cut-001 \"the form\")\n[F10.1]\n"})

    moved = cut_tidy_service.set_aside_unused_cuts(paths)

    harness.check("test_unused_cuts_tidied: a placed cut is not moved", moved == [], f"moved {moved}")
    harness.check("test_unused_cuts_tidied: a placed cut's file stays",
                  (paths.output_root / CUTS_DIR / "cut-001.png").is_file())


def test_a_cut_only_a_block_places_is_kept() -> None:
    """A store whose sections were cleared still names its cuts in its blocks, and that is enough to keep them."""

    paths = _build_run(cuts={"cut-002": _build_cut("cut-002", "frame-011")},
                       blocks=[{"kind": "image", "cut": "cut-002"}], sections={})

    moved = cut_tidy_service.set_aside_unused_cuts(paths)

    harness.check("test_unused_cuts_tidied: a cut named by a block is not moved", moved == [], f"moved {moved}")


def test_an_unplaced_and_an_undone_cut_move_to_the_trash() -> None:
    paths = _build_run(cuts={"cut-003": _build_cut("cut-003", "frame-012"),
                             "cut-004": _build_cut("cut-004", "frame-013", discarded="undone"),
                             "cut-005": _build_cut("cut-005", "frame-014", discarded="reset")},
                       blocks=[], sections={})

    moved = cut_tidy_service.set_aside_unused_cuts(paths)

    harness.check("test_unused_cuts_tidied: every unused cut moves", {row["cut"] for row in moved}
                  == {"cut-003", "cut-004", "cut-005"}, f"moved {[row['cut'] for row in moved]}")
    harness.check("test_unused_cuts_tidied: why each moved is recorded",
                  {row["cut"]: row["why"] for row in moved}
                  == {"cut-003": cut_tidy_service.UNPLACED, "cut-004": output_contract.UNDO_DISCARD_REASON,
                      "cut-005": "reset"},
                  str({row["cut"]: row["why"] for row in moved}))
    for cut_id in ("cut-003", "cut-004", "cut-005"):
        harness.check(f"test_unused_cuts_tidied: {cut_id} is in the trash",
                      (paths.output_root / cut_tidy_service.TRASH_DIR_NAME / "cuts" / f"{cut_id}.png").is_file())
        harness.check(f"test_unused_cuts_tidied: {cut_id} left the cuts folder",
                      not (paths.output_root / CUTS_DIR / f"{cut_id}.png").is_file())


def test_the_store_records_what_moved() -> None:
    paths = _build_run(cuts={"cut-006": _build_cut("cut-006", "frame-015")}, blocks=[], sections={})

    cut_tidy_service.set_aside_unused_cuts(paths)
    store = json.loads((paths.output_root / "04_writing" / "images.json").read_text(encoding="utf-8"))
    recorded = store.get("removedUnreferenced") or []

    harness.check("test_unused_cuts_tidied: the store records the move", len(recorded) == 1, str(recorded))
    harness.check("test_unused_cuts_tidied: the record names where it went",
                  recorded and recorded[0].get("movedTo", "").endswith("cut-006.png"), str(recorded))


def test_moving_twice_changes_nothing() -> None:
    """The run's last step runs on every resume of a done run, and `i2k tidy-cuts` may be called at any time."""

    paths = _build_run(cuts={"cut-007": _build_cut("cut-007", "frame-016")}, blocks=[], sections={})

    first = cut_tidy_service.set_aside_unused_cuts(paths)
    second = cut_tidy_service.set_aside_unused_cuts(paths)
    store = json.loads((paths.output_root / "04_writing" / "images.json").read_text(encoding="utf-8"))

    harness.check("test_unused_cuts_tidied: the first call moves it", len(first) == 1, str(first))
    harness.check("test_unused_cuts_tidied: the second call moves nothing", second == [], str(second))
    harness.check("test_unused_cuts_tidied: it is recorded once",
                  len(store.get("removedUnreferenced") or []) == 1, str(store.get("removedUnreferenced")))
    harness.check("test_unused_cuts_tidied: a moved cut is no longer offered",
                  cut_tidy_service.list_unused_cuts(paths, store) == [],
                  str(cut_tidy_service.list_unused_cuts(paths, store)))


def test_a_run_with_no_cuts_is_left_alone() -> None:
    paths = _build_run(cuts={}, blocks=[], sections={})

    harness.check("test_unused_cuts_tidied: a run with no cuts moves nothing",
                  cut_tidy_service.set_aside_unused_cuts(paths) == [])


def test_the_command_lists_before_it_moves() -> None:
    """`i2k tidy-cuts` without `--yes` says what it would move and moves nothing, as every removing command here does."""

    paths = _build_run(cuts={"cut-008": _build_cut("cut-008", "frame-017")}, blocks=[], sections={})
    said: list[str] = []
    args = argparse.Namespace(output_root=paths.output_root, is_confirmed=False,
                              logger=types.SimpleNamespace(info=said.append, error=said.append))

    code = tidy_command.cmd_tidy_cuts(args)

    harness.check("test_unused_cuts_tidied: the listing succeeds", code == 0, str(code))
    harness.check("test_unused_cuts_tidied: the listing names the cut", any("cut-008" in line for line in said),
                  "\n".join(said))
    harness.check("test_unused_cuts_tidied: the listing moves nothing",
                  (paths.output_root / CUTS_DIR / "cut-008.png").is_file())

    args.is_confirmed = True
    code = tidy_command.cmd_tidy_cuts(args)

    harness.check("test_unused_cuts_tidied: --yes succeeds", code == 0, str(code))
    harness.check("test_unused_cuts_tidied: --yes moves the file",
                  (paths.output_root / cut_tidy_service.TRASH_DIR_NAME / "cuts" / "cut-008.png").is_file())


def test_the_command_refuses_a_folder_that_is_not_there() -> None:
    said: list[str] = []
    args = argparse.Namespace(output_root=Path(tempfile.mkdtemp()) / "nowhere", is_confirmed=True,
                              logger=types.SimpleNamespace(info=said.append, error=said.append))

    harness.check("test_unused_cuts_tidied: a missing run folder is refused by name",
                  tidy_command.cmd_tidy_cuts(args) == 2 and any("is not a run folder" in line for line in said),
                  "\n".join(said))


def test_a_cut_a_chapter_reset_set_aside_is_recorded_as_reset_not_undone() -> None:
    paths = _build_run(cuts={"cut-005": _build_cut("cut-005", "frame-015", discarded="reset"),
                             "cut-006": _build_cut("cut-006", "frame-016", discarded="undone")}, blocks=[], sections={})
    store = json.loads((paths.output_root / "04_writing" / "images.json").read_text(encoding="utf-8"))

    why = {row["cut"]: row["why"] for row in cut_tidy_service.list_unused_cuts(paths, store)}

    harness.check("test_unused_cuts_tidied: a reset's cut says reset, an undone one undone",
                  why == {"cut-005": "reset", "cut-006": "undone"}, str(why))


def test_a_second_tidy_never_overwrites_a_trashed_cut_of_the_same_name() -> None:
    paths = _build_run(cuts={"cut-001": _build_cut("cut-001", "frame-010")}, blocks=[], sections={})
    cut_tidy_service.set_aside_unused_cuts(paths)
    # A reset to phase 3 removes the store, so cut numbering starts again and a new cut-001 is made
    (paths.output_root / CUTS_DIR / "cut-001.png").write_bytes(b"second")
    (paths.output_root / "04_writing" / "images.json").write_text(
        json.dumps({"cuts": {"cut-001": _build_cut("cut-001", "frame-020")}, "blocks": [], "removedUnreferenced": []}),
        encoding="utf-8")
    moved = cut_tidy_service.set_aside_unused_cuts(paths)
    trashed = sorted(path.read_bytes() for path in (paths.output_root / ".trash" / "cuts").iterdir())
    harness.check("test_unused_cuts_tidied: both cut-001 files are in the trash", trashed == [b"png", b"second"],
                  f"{trashed} {moved}")


def test_a_cut_put_back_by_hand_and_moved_again_is_recorded_again() -> None:
    paths = _build_run(cuts={"cut-006": _build_cut("cut-006", "frame-015")}, blocks=[], sections={})
    cut_tidy_service.set_aside_unused_cuts(paths)
    trashed = next((paths.output_root / cut_tidy_service.TRASH_CUTS_DIR).glob("cut-006*.png"))
    trashed.rename(paths.output_root / CUTS_DIR / "cut-006.png")

    moved = cut_tidy_service.set_aside_unused_cuts(paths)

    store = json.loads((paths.output_root / "04_writing" / "images.json").read_text(encoding="utf-8"))
    harness.check("test_unused_cuts_tidied: the second move is moved", [row["cut"] for row in moved] == ["cut-006"],
                  str(moved))
    harness.check("test_unused_cuts_tidied: and each move of the cut has its row, so the store names both files",
                  len(store.get("removedUnreferenced") or []) == 2, str(store.get("removedUnreferenced")))


def test_a_move_that_fails_part_way_leaves_the_record_of_the_moves_already_made() -> None:
    paths = _build_run(cuts={"cut-001": _build_cut("cut-001", "frame-010"), "cut-002": _build_cut("cut-002", "frame-011")},
                       blocks=[], sections={})
    real_move = cut_tidy_service.shutil.move
    calls = []

    def move_once_then_fail(source: str, target: str) -> None:
        calls.append(source)
        if len(calls) == 2:
            raise OSError("disk full")
        real_move(source, target)

    cut_tidy_service.shutil.move = move_once_then_fail
    try:
        cut_tidy_service.set_aside_unused_cuts(paths)
    except OSError:
        pass
    finally:
        cut_tidy_service.shutil.move = real_move
    store = json.loads((paths.output_root / "04_writing" / "images.json").read_text(encoding="utf-8"))
    recorded = [row["cut"] for row in store["removedUnreferenced"]]
    harness.check("test_unused_cuts_tidied: the cut moved before the failure has its row", recorded == ["cut-001"], str(recorded))


def test_the_page_sets_aside_a_run_folders_unused_cuts() -> None:
    paths = _build_run(cuts={"cut-003": _build_cut("cut-003", "frame-012")}, blocks=[], sections={})
    folder = paths.output_root
    previous_roots = dict(run_status_service.RUN_ROOTS)
    run_status_service.RUN_ROOTS.clear()
    run_status_service.RUN_ROOTS["output"] = folder.parent
    try:
        answer = TestClient(web_api_service.build_app()).post(f"/api/runs/output/{folder.name}/cuts/tidy")
    finally:
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(previous_roots)
    harness.check("test_unused_cuts_tidied: the page's tidy moves the unplaced cut into the trash",
                  answer.status_code == 200 and answer.json()["exit_code"] == 0
                  and not (folder / CUTS_DIR / "cut-003.png").exists(), answer.text)
