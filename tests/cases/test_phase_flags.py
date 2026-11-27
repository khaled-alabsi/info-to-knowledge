"""The command line of a chain pipeline: a file target, an output folder, and which phases to run.

Replaces the seed's entry-point case, which pins a repository target and the chat and tool commands
this profile does not ship. What it pinned that still applies is pinned here: every command has a
handler, every handler is reachable, and a run line with no target cannot be typed.
"""

import contextlib
import io

from support import harness

import cli
from cli import run_command
from graph import routing
from models import output_contract
from models.phase_request import PhaseRequest
from models.run_errors import BadTarget

TARGET_FLAG = "--video"
LAST = max(output_contract.PHASE_FILES)


def _parse(*line: str):
    return cli.build_parser().parse_args(["run", TARGET_FLAG, "in.bin", "--output", "out", *line])


def _is_refused(*line: str) -> bool:
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            cli.build_parser().parse_args(list(line))
        except SystemExit as exc:
            return exc.code == 2

    return False


def test_every_command_is_reachable_from_both_the_parser_and_the_table():
    subparsers = next(action for action in cli.build_parser()._actions if action.dest == "command")
    harness.check("every command the parser accepts has a handler",
                  set(subparsers.choices) == set(cli.HANDLERS), f"{set(subparsers.choices)} vs {set(cli.HANDLERS)}")


def test_a_run_with_no_target_or_no_output_is_refused_rather_than_defaulted():
    harness.check("a run line with no target is refused", _is_refused("run", "--output", "out"))
    harness.check("a run line with no output folder is refused", _is_refused("run", TARGET_FLAG, "in.bin"))
    harness.check("the same line with both parses", not _is_refused("run", TARGET_FLAG, "in.bin", "--output", "out"))


def test_no_phase_flag_selects_every_phase():
    selection = run_command.build_phase_request(_parse())
    harness.check("every phase is selected by default", selection == PhaseRequest(first=1, last=LAST),
                  str(selection))


def test_phase_runs_exactly_one_phase():
    selection = run_command.build_phase_request(_parse("--phase", "2"))
    harness.check("`--phase 2` runs phase 2 and stops", (selection.first, selection.last) == (2, 2), str(selection))


def test_from_and_to_bound_the_range():
    selection = run_command.build_phase_request(_parse("--from", "2"))
    harness.check("`--from 2` runs 2 onward", (selection.first, selection.last) == (2, LAST), str(selection))
    selection = run_command.build_phase_request(_parse("--to", "2"))
    harness.check("`--to 2` stops after 2", (selection.first, selection.last) == (1, 2), str(selection))


def test_a_selection_that_contradicts_itself_is_refused_by_name():
    for line, word in ((("--phase", "2", "--from", "1"), "--phase"),
                       (("--from", "3", "--to", "2"), "--from"),
                       (("--phase", "99"), "99")):
        try:
            run_command.build_phase_request(_parse(*line))
        except BadTarget as exc:
            harness.check(f"`{' '.join(line)}` is refused naming {word}", word in str(exc), str(exc))
        else:
            harness.check(f"`{' '.join(line)}` is refused", False)


def test_one_item_is_selected_by_its_slug_and_only_runs_the_work_phase():
    args = _parse("--worker", "5")
    selection = run_command.build_phase_request(args)
    harness.check("`--worker 5` selects frame 5 by its slug", selection.selected_slugs == ("frame-005",), str(selection))
    harness.check("and runs the item phase alone",
                  selection.first == selection.last == routing.FRAMES_PROCESSING_PHASE_NUMBER, str(selection))


def test_verbose_is_read_before_the_command_as_well_as_after_it():
    parser = cli.build_parser()
    harness.check("`-v run ...` is verbose", parser.parse_args(["-v", "run", "--video", "a.mp4", "--output", "out"]).is_verbose)
    harness.check("`run ... -v` is verbose", parser.parse_args(["run", "--video", "a.mp4", "--output", "out", "-v"]).is_verbose)
    harness.check("no -v is not", not parser.parse_args(["run", "--video", "a.mp4", "--output", "out"]).is_verbose)


def test_profile_is_read_before_the_command_as_well_as_after_it():
    parser = cli.build_parser()
    harness.check("`--profile strict run ...` names the profile",
                  parser.parse_args(["--profile", "strict", "run", "--video", "a.mp4", "--output", "out"]).profile_name
                  == "strict")
    harness.check("`run ... --profile strict` names it",
                  parser.parse_args(["run", "--video", "a.mp4", "--output", "out", "--profile", "strict"]).profile_name
                  == "strict")
