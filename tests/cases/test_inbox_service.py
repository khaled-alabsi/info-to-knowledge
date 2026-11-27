"""Which dropped videos count as new, and what the `inbox` command would do with them.

The whole point of the command is the word NEW, so these cases are about the boundary: a finished
item is skipped, a half-finished one is not, and a folder that is not a video is neither.
"""

import argparse
import logging
import tempfile
from pathlib import Path

from support import harness

import config
from cli import build_parser
from cli import inbox_command
from models import output_contract
from services import inbox_service


def _make_inbox() -> tuple[Path, Path]:
    """An inbox with three videos and one file that is not one, and an empty output root."""

    root = Path(tempfile.mkdtemp())
    inbox_root, output_root = root / "inbox", root / "output"
    inbox_root.mkdir()
    output_root.mkdir()
    for name in ("alpha.mp4", "beta.MOV", "gamma.mkv", "alpha.srt", ".hidden.mp4"):
        (inbox_root / name).write_text("fixture", encoding="utf-8")
    (inbox_root / "a-folder.mp4").mkdir()

    return inbox_root, output_root


def _finish(output_root: Path,
            stem: str) -> None:
    """Write the one file that makes an item finished: the last phase's document."""

    folder = output_root / stem
    folder.mkdir(parents=True, exist_ok=True)
    (folder / output_contract.SUMMARY_FILE).write_text("# document\n", encoding="utf-8")


def test_only_video_files_are_items():
    inbox_root, _ = _make_inbox()
    names = [path.name for path in inbox_service.list_inbox_videos(inbox_root)]
    harness.check("the three videos, `.MOV` included, in name order", names == ["alpha.mp4", "beta.MOV", "gamma.mkv"], str(names))
    harness.check("a subtitle beside a recording is not an item", "alpha.srt" not in names, str(names))
    harness.check("a dot file is not an item", ".hidden.mp4" not in names, str(names))
    harness.check("a DIRECTORY named like a video is not an item", "a-folder.mp4" not in names, str(names))


def test_a_missing_inbox_is_empty_not_an_error():
    inbox_root, output_root = _make_inbox()
    gone = inbox_root.parent / "no-such-folder"
    harness.check("a missing inbox lists nothing", inbox_service.list_inbox_videos(gone) == [])
    harness.check("and has nothing new", inbox_service.list_new_videos(gone, output_root) == [])


def test_an_item_is_new_until_its_document_exists():
    inbox_root, output_root = _make_inbox()
    harness.check("everything is new at first",
                  [p.name for p in inbox_service.list_new_videos(inbox_root, output_root)]
                  == ["alpha.mp4", "beta.MOV", "gamma.mkv"])
    _finish(output_root, "alpha")
    names = [path.name for path in inbox_service.list_new_videos(inbox_root, output_root)]
    harness.check("the finished item drops out", names == ["beta.MOV", "gamma.mkv"], str(names))
    harness.check("and it reports as finished", inbox_service.is_item_finished(output_root, inbox_root / "alpha.mp4"))


def test_a_half_finished_run_is_still_new():
    """A run folder with earlier phase files but no document has not delivered anything."""

    inbox_root, output_root = _make_inbox()
    folder = output_root / "beta"
    folder.mkdir(parents=True)
    (folder / output_contract.TRANSCRIPT_FILE).write_text("{}", encoding="utf-8")
    (folder / output_contract.LOOP_ITEMS_FILE).write_text("{}", encoding="utf-8")
    names = [path.name for path in inbox_service.list_new_videos(inbox_root, output_root)]
    harness.check("an item with a transcript but no document is still new", "beta.MOV" in names, str(names))


def test_the_run_folder_is_named_by_the_video_stem():
    inbox_root, output_root = _make_inbox()
    folder = inbox_service.build_run_folder(output_root, inbox_root / "alpha.mp4")
    harness.check("one folder per video, named by its stem", folder == output_root / "alpha", str(folder))


def test_the_command_line_defaults_to_the_project_folders():
    args = build_parser().parse_args(["inbox"])
    harness.check("the inbox defaults to the project's own", args.inbox_root == config.INBOX_DIR, str(args.inbox_root))
    harness.check("and the output root to output/", args.documents_root == config.DOCUMENT_ROOT,
                  str(args.documents_root))
    harness.check("running nothing is not the default", args.is_listing_only is False)
    harness.check("and neither is a dry run", args.is_dry_run is False)


def test_each_item_gets_its_own_run_folder_under_the_output_root():
    """The per-item `run` arguments, which are the only thing this command produces itself."""

    inbox_root, output_root = _make_inbox()
    args = build_parser().parse_args(["inbox", "--inbox", str(inbox_root), "--output", str(output_root)])
    args.logger = logging.getLogger("test")
    video = inbox_root / "alpha.mp4"
    run_args = inbox_command._build_run_args(args, video, inbox_service.build_run_folder(output_root, video))
    harness.check("the target is the dropped file", run_args.target_file == video, str(run_args.target_file))
    harness.check("the output is that video's own folder, NOT the root",
                  run_args.output_root == output_root / "alpha", str(run_args.output_root))
    harness.check("every phase runs", (run_args.only_phase, run_args.from_phase, run_args.to_phase) == (None, None, None))
    harness.check("no frame is singled out", run_args.worker_indexes == [])
    harness.check("and it is not a resume", run_args.is_resuming is False)


def test_listing_runs_nothing():
    """`--list` must not be able to start a model call, whatever is in the inbox."""

    inbox_root, output_root = _make_inbox()
    args = build_parser().parse_args(["inbox", "--list", "--inbox", str(inbox_root), "--output", str(output_root)])
    args.logger = logging.getLogger("test")
    code = inbox_command.cmd_inbox(args)
    harness.check("listing exits 0", code == 0, str(code))
    harness.check("and wrote no run folder", list(output_root.iterdir()) == [], str(list(output_root.iterdir())))


def test_an_empty_inbox_exits_zero_without_running_anything():
    root = Path(tempfile.mkdtemp())
    args = build_parser().parse_args(["inbox", "--inbox", str(root / "in"), "--output", str(root / "out")])
    args.logger = logging.getLogger("test")
    harness.check("nothing to do is not a failure", inbox_command.cmd_inbox(args) == 0)


def test_the_run_defaults_carry_every_flag_cmd_run_reads():
    """A missing key would be an `AttributeError` inside the first real item, after the batch started."""

    inbox_root, output_root = _make_inbox()
    args = build_parser().parse_args(["inbox", "--inbox", str(inbox_root), "--output", str(output_root)])
    args.logger = logging.getLogger("test")
    video = inbox_root / "alpha.mp4"
    run_args = inbox_command._build_run_args(args, video, output_root / "alpha")
    wanted = {"target_file", "output_root", "only_phase", "from_phase", "to_phase", "worker_indexes",
              "is_dry_run", "is_resuming", "logger"}
    missing = wanted - set(vars(run_args))
    harness.check("every argument a run line carries is present", not missing, str(sorted(missing)))


def test_one_video_that_raises_does_not_stop_the_batch():
    inbox_root, output_root = _make_inbox()
    args = build_parser().parse_args(["inbox", "--inbox", str(inbox_root), "--output", str(output_root)])
    args.logger = logging.getLogger("test-inbox-raise")
    started: list[str] = []
    original = inbox_command.run_command.cmd_run

    def run_or_raise(run_args: argparse.Namespace) -> int:
        started.append(run_args.target_file.name)
        if len(started) == 1:
            raise RuntimeError("an error nothing caught")
        return 0

    inbox_command.run_command.cmd_run = run_or_raise
    try:
        code = inbox_command.cmd_inbox(args)
    finally:
        inbox_command.run_command.cmd_run = original
    harness.check("every video still starts after the first one raised", len(started) == 3, str(started))
    harness.check("and the batch reports a failure", code == 1, str(code))


def test_a_video_stopped_with_ctrl_c_ends_the_batch():
    inbox_root, output_root = _make_inbox()
    args = build_parser().parse_args(["inbox", "--inbox", str(inbox_root), "--output", str(output_root)])
    args.logger = logging.getLogger("test-inbox-interrupted")
    started: list[str] = []
    original = inbox_command.run_command.cmd_run

    def run_interrupted(run_args: argparse.Namespace) -> int:
        started.append(run_args.target_file.name)

        return 130

    inbox_command.run_command.cmd_run = run_interrupted
    try:
        code = inbox_command.cmd_inbox(args)
    finally:
        inbox_command.run_command.cmd_run = original
    harness.check("no video starts after the one that was stopped", len(started) == 1, str(started))
    harness.check("and the batch ends with 130", code == 130, str(code))
