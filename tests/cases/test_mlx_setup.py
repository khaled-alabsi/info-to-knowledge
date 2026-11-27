"""`i2k setup-mlx`: finds mlx-vlm already installed, picks a Python mlx supports, creates the venv, downloads a model."""

from __future__ import annotations

from pathlib import Path

from support import harness

import cli
from cli import mlx_setup_command
from models.mlx_setup_context import MlxSetupContext
from services import mlx_setup_service

VENV = Path("/home/tester/.mlx-vlm/venv")
VENV_PYTHON = VENV / "bin" / "python"
OLD_PYTHON = Path("/usr/bin/python3")
NEW_PYTHON = Path("/opt/homebrew/bin/python3.13")

PYPI = {
    "https://pypi.org/pypi/mlx-vlm/json": {"info": {"version": "<version>", "requires_python": ">=3.10"}},
    "https://pypi.org/pypi/mlx/json": {"urls": [
        {"filename": "mlx-<version>-cp311-cp311-macosx_14_0_arm64.whl"},
        {"filename": "mlx-<version>-cp313-cp313-macosx_14_0_arm64.whl"},
        {"filename": "mlx-<version>-cp313-cp313t-macosx_14_0_arm64.whl"},
        {"filename": "mlx-<version>-cp314-cp314-macosx_26_0_arm64.whl"},
        {"filename": "mlx-<version>-cp312-cp312-manylinux_2_35_x86_64.whl"},
        {"filename": "mlx-<version>-cp39-cp39-macosx_14_0_arm64.whl"},
    ]},
}


class FakeMachine:
    """Pythons with their versions and whether mlx-vlm imports in each; records every command run."""

    def __init__(self, pythons: dict[Path, str], installed: set[Path], cached_models: set[str] = frozenset(),
                 answer: str | None = None, brew: Path | None = None):
        self.pythons = dict(pythons)
        self.installed = set(installed)
        self.cached_models = set(cached_models)
        self.answer = answer
        self.brew = brew
        self.commands: list[list[str]] = []
        self.lines: list[str] = []

    def run(self, arguments: list[str], is_capturing: bool) -> tuple[int, str]:
        self.commands.append(arguments)
        program = Path(arguments[0])
        if arguments[1:3] == ["-m", "venv"]:
            self.pythons[Path(arguments[3]) / "bin" / "python"] = self.pythons[program]
            return 0, ""
        if arguments[1:4] == ["-m", "pip", "install"]:
            if "mlx-vlm" in arguments:
                self.installed.add(program)
            return 0, ""
        if program == self.brew:
            self.pythons[Path("/opt/homebrew/bin/python3.13")] = "3.13"
            return 0, ""
        if program not in self.pythons:
            return 127, "not found"
        script = arguments[2]
        if script == mlx_setup_service.VERSION_SCRIPT:
            return 0, self.pythons[program]
        if script == mlx_setup_service.INSTALLED_SCRIPT:
            return (0, "<mlx-vlm version>") if program in self.installed else (1, "PackageNotFoundError")
        if script == mlx_setup_service.DOWNLOAD_SCRIPT:
            model, is_local_only = arguments[3], arguments[4] == "local"
            if is_local_only and model not in self.cached_models:
                return 1, "LocalEntryNotFoundError"
            self.cached_models.add(model)
            return 0, f"/home/tester/.cache/huggingface/hub/models--{model.replace('/', '--')}"
        return 2, f"unexpected script {script}"

    def context(self, system: str = "Darwin", machine: str = "arm64",
                    macos_version: tuple[int, int] = (15, 3)) -> MlxSetupContext:
        return MlxSetupContext(run=self.run, fetch_json=PYPI.__getitem__,
                                   ask=(lambda question: self.answer) if self.answer is not None else None,
                                   find_pythons=lambda: [path for path in self.pythons if path != VENV_PYTHON],
                                   say=self.lines.append, brew=self.brew, system=system, machine=machine,
                                   macos_version=macos_version)

    def created_venv(self) -> bool:
        return any(arguments[1:3] == ["-m", "venv"] for arguments in self.commands)


def test_the_supported_versions_are_the_ones_mlx_has_a_mac_build_for_and_mlx_vlm_accepts():
    filenames = [row["filename"] for row in PYPI["https://pypi.org/pypi/mlx/json"]["urls"]]
    versions = mlx_setup_service.list_supported_versions(">=3.10", filenames, (15, 3))
    harness.check("3.11 and 3.13 only: 3.9 below the minimum, 3.12 Linux only, 3.14 needs macOS 26",
                  versions == [(3, 11), (3, 13)], str(versions))


def test_mlx_vlm_found_on_the_system_creates_no_venv():
    machine = FakeMachine({OLD_PYTHON: "3.12"}, installed={OLD_PYTHON})
    code = mlx_setup_service.set_up_mlx(None, VENV, machine.context())
    harness.check("exits 0", code == 0, str(code))
    harness.check("no venv is created", not machine.created_venv(), str(machine.commands))
    harness.check("says where it is installed", any(str(OLD_PYTHON) in line and "already installed" in line
                                                    for line in machine.lines), "\n".join(machine.lines))


def test_a_model_is_downloaded_with_the_python_that_has_mlx_vlm():
    machine = FakeMachine({OLD_PYTHON: "3.12"}, installed={OLD_PYTHON})
    code = mlx_setup_service.set_up_mlx("acme/vision-4bit", VENV, machine.context())
    downloads = [arguments for arguments in machine.commands if mlx_setup_service.DOWNLOAD_SCRIPT in arguments]
    harness.check("exits 0", code == 0, "\n".join(machine.lines))
    harness.check("checks the cache, then downloads, with that Python",
                  [(Path(row[0]), row[4]) for row in downloads] == [(OLD_PYTHON, "local"), (OLD_PYTHON, "remote")],
                  str(downloads))
    harness.check("prints the server line", any("-m mlx_vlm.server --model acme/vision-4bit" in line
                                                for line in machine.lines), "\n".join(machine.lines))


def test_a_model_already_in_the_cache_is_not_downloaded_again():
    machine = FakeMachine({OLD_PYTHON: "3.12"}, installed={OLD_PYTHON}, cached_models={"acme/vision-4bit"})
    mlx_setup_service.set_up_mlx("acme/vision-4bit", VENV, machine.context())
    remote = [arguments for arguments in machine.commands if arguments[-1:] == ["remote"]]
    harness.check("no download", not remote, str(remote))
    harness.check("says it is downloaded already", any("already downloaded" in line for line in machine.lines),
                  "\n".join(machine.lines))


def test_without_mlx_vlm_the_newest_supported_python_gets_a_venv_with_mlx_vlm():
    machine = FakeMachine({OLD_PYTHON: "3.11", NEW_PYTHON: "3.13", Path("/usr/local/bin/python3.9"): "3.9"},
                          installed=set())
    code = mlx_setup_service.set_up_mlx(None, VENV, machine.context())
    harness.check("exits 0", code == 0, "\n".join(machine.lines))
    harness.check("the venv is made from Python 3.13",
                  [NEW_PYTHON, "-m", "venv", str(VENV)] in [[Path(a[0]), *a[1:]] for a in machine.commands],
                  str(machine.commands))
    harness.check("mlx-vlm is installed into the venv",
                  [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "mlx-vlm"] in machine.commands,
                  str(machine.commands))


def test_with_no_supported_python_and_no_terminal_it_names_what_to_install_and_stops():
    machine = FakeMachine({Path("/usr/bin/python3"): "3.9"}, installed=set(), brew=Path("/opt/homebrew/bin/brew"))
    code = mlx_setup_service.set_up_mlx(None, VENV, machine.context())
    harness.check("exits 2", code == 2, str(code))
    harness.check("no venv, no install", not machine.created_venv(), str(machine.commands))
    harness.check("names the brew command for the newest supported version",
                  any("brew install python@3.13" in line for line in machine.lines), "\n".join(machine.lines))


def test_with_no_supported_python_a_yes_installs_it_with_homebrew_and_goes_on():
    brew = Path("/opt/homebrew/bin/brew")
    machine = FakeMachine({Path("/usr/bin/python3"): "3.9"}, installed=set(), answer="y", brew=brew)
    code = mlx_setup_service.set_up_mlx(None, VENV, machine.context())
    harness.check("exits 0", code == 0, "\n".join(machine.lines))
    harness.check("brew installs python@3.13", [str(brew), "install", "python@3.13"] in machine.commands,
                  str(machine.commands))
    harness.check("then the venv is made from it", machine.created_venv(), str(machine.commands))


def test_off_apple_silicon_it_refuses_by_name():
    machine = FakeMachine({OLD_PYTHON: "3.12"}, installed=set())
    code = mlx_setup_service.set_up_mlx(None, VENV, machine.context(system="Linux", machine="x86_64"))
    harness.check("exits 2", code == 2, str(code))
    harness.check("says mlx needs an Apple silicon Mac", any("Apple silicon" in line for line in machine.lines),
                  "\n".join(machine.lines))


def test_the_command_takes_an_optional_model_and_a_venv_path():
    parser = cli.build_parser()
    bare = parser.parse_args(["setup-mlx"])
    named = parser.parse_args(["setup-mlx", "acme/vision-4bit", "--venv", "/home/tester/v"])
    harness.check("the handler is the setup-mlx command",
                  cli.HANDLERS["setup-mlx"] is mlx_setup_command.cmd_setup_mlx, "")
    harness.check("no model by default, the venv under the home folder",
                  bare.model is None and bare.venv_dir == mlx_setup_service.DEFAULT_VENV, str(vars(bare)))
    harness.check("a model and a venv are read", named.model == "acme/vision-4bit"
                  and named.venv_dir == Path("/home/tester/v"), str(vars(named)))
