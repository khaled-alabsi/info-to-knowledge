"""`i2k setup-mlx`: make mlx-vlm usable on this Mac, and download a model for it when one is named.

In order: refuse a machine that is not an Apple silicon Mac; look for mlx-vlm in every Python on the machine and in
the venv, and use the first that has it; otherwise read from PyPI which Python versions fit (mlx-vlm's
`requires_python`, and an mlx build for macOS arm64 at this macOS version), pick the newest such Python on the
machine, or offer to install it with Homebrew, then create the venv from it and install mlx-vlm with its
dependencies. A named model is looked up in the Hugging Face cache and downloaded only when it is not there.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from packaging.specifiers import SpecifierSet

from models.mlx_setup_context import MlxSetupContext

__all__ = ["DEFAULT_VENV", "VERSION_SCRIPT", "INSTALLED_SCRIPT", "DOWNLOAD_SCRIPT", "list_supported_versions",
           "build_system_context", "set_up_mlx"]

DEFAULT_VENV = Path.home() / ".mlx-vlm" / "venv"
MLX_VLM_URL = "https://pypi.org/pypi/mlx-vlm/json"
MLX_URL = "https://pypi.org/pypi/mlx/json"
MLX_VLM_SERVER_PORT = 8080                          # the port of the `mlx_vlm.server` start line printed at the end

VERSION_SCRIPT = "import sys; print('%d.%d' % sys.version_info[:2])"
INSTALLED_SCRIPT = "import importlib.metadata as m, mlx_vlm; print(m.version('mlx-vlm'))"
# argv: the model id, then `local` (the cache only) or `remote` (download what is missing).
DOWNLOAD_SCRIPT = ("import sys\n"
                   "from huggingface_hub import snapshot_download\n"
                   "print(snapshot_download(sys.argv[1], local_files_only=sys.argv[2] == 'local'))")

_MAC_WHEEL = re.compile(r"^cp(\d)(\d+)-cp\1\2-macosx_(\d+)_(\d+)_arm64$")
_PYTHON_NAME = re.compile(r"^python3(\.\d+)?$")
_PYTHON_GLOBS = ("/Library/Frameworks/Python.framework/Versions/*/bin/python3", "/opt/homebrew/bin/python3*",
                 "/usr/local/bin/python3*", str(Path.home() / ".pyenv" / "versions" / "*" / "bin" / "python3"))


def _render_version(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


def _read_python_version(python: Path,
                         context: MlxSetupContext) -> tuple[int, int] | None:
    """The interpreter's (major, minor), or None when it does not run."""

    code, output = context.run([str(python), "-c", VERSION_SCRIPT], is_capturing=True)
    match = re.fullmatch(r"(\d+)\.(\d+)", output.strip())
    if code != 0 or match is None:
        return None

    return int(match.group(1)), int(match.group(2))


def _find_installed_python(venv_dir: Path,
                           context: MlxSetupContext) -> Path | None:
    """The first Python, the venv's first, in which mlx-vlm imports; it says which and at what version."""

    candidates = [venv_dir / "bin" / "python", *context.find_pythons()]
    for python in dict.fromkeys(candidates):
        code, output = context.run([str(python), "-c", INSTALLED_SCRIPT], is_capturing=True)
        if code == 0:
            context.say(f"mlx-vlm {output.strip()} is already installed: {python}")
            return python

    return None


def _read_supported_versions(context: MlxSetupContext) -> list[tuple[int, int]]:
    """The Python versions mlx-vlm accepts and mlx ships a build for on this macOS; raises OSError offline."""

    requires_python = context.fetch_json(MLX_VLM_URL)["info"]["requires_python"] or ""
    filenames = [row["filename"] for row in context.fetch_json(MLX_URL)["urls"]]

    return list_supported_versions(requires_python, filenames, context.macos_version)


def _choose_python(supported: list[tuple[int, int]],
                   context: MlxSetupContext) -> Path | None:
    """The machine's Python of the newest supported version, or None when none fits."""

    fitting: dict[tuple[int, int], Path] = {}
    for python in context.find_pythons():
        version = _read_python_version(python, context)
        if version in supported:
            fitting.setdefault(version, python)
    if not fitting:
        return None
    newest = max(fitting)
    context.say(f"using Python {_render_version(newest)} at {fitting[newest]}")

    return fitting[newest]


def _install_python(supported: list[tuple[int, int]],
                    context: MlxSetupContext) -> Path | None:
    """Install the newest supported Python with Homebrew when the person says yes; otherwise say how and stop."""

    # 1. Name what is missing.
    newest = _render_version(supported[-1])
    formula = f"python@{newest}"
    command = f"brew install {formula}"
    context.say("no Python on this machine fits mlx-vlm; it needs one of "
                    + ", ".join(_render_version(version) for version in supported))

    # 2. Without Homebrew or a person to ask, say what to install and stop.
    if context.brew is None:
        context.say(f"install Python {newest} from https://www.python.org/downloads/ , then run this again")
        return None
    if context.ask is None:
        context.say(f"install it with `{command}`, then run this again")
        return None
    answer = context.ask(f"Install Python {newest} with Homebrew now ({command})? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        context.say(f"not installed; install it with `{command}`, then run this again")
        return None

    # 3. Install it, then find it like any other Python.
    code, _ = context.run([str(context.brew), "install", formula], is_capturing=False)
    if code != 0:
        context.say(f"`{command}` failed with exit code {code}")
        return None
    python = _choose_python(supported[-1:], context)
    if python is None:
        context.say(f"Homebrew installed python@{newest}, but no python3.{supported[-1][1]} is found on PATH")

    return python


def _create_venv(python: Path,
                 venv_dir: Path,
                 context: MlxSetupContext) -> Path | None:
    """The venv's Python with mlx-vlm installed and importing, or None when a step failed."""

    venv_python = venv_dir / "bin" / "python"
    steps = ([str(python), "-m", "venv", str(venv_dir)],
             [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
             [str(venv_python), "-m", "pip", "install", "--upgrade", "mlx-vlm"])
    context.say(f"creating the venv {venv_dir} and installing mlx-vlm into it")
    for arguments in steps:
        code, _ = context.run(arguments, is_capturing=False)
        if code != 0:
            context.say(f"`{' '.join(arguments)}` failed with exit code {code}")
            return None
    code, output = context.run([str(venv_python), "-c", INSTALLED_SCRIPT], is_capturing=True)
    if code != 0:
        context.say(f"mlx-vlm was installed but does not import in {venv_python}: {output.strip()[-300:]}")
        return None
    context.say(f"mlx-vlm {output.strip()} installed in {venv_dir}")

    return venv_python


def _fetch_model(python: Path,
                 model: str,
                 context: MlxSetupContext) -> int:
    """Download the model into the Hugging Face cache unless it is there; 0 when it is there after."""

    code, output = context.run([str(python), "-c", DOWNLOAD_SCRIPT, model, "local"], is_capturing=True)
    if code == 0:
        context.say(f"{model} is already downloaded: {output.strip()}")
    else:
        context.say(f"downloading {model}")
        code, _ = context.run([str(python), "-c", DOWNLOAD_SCRIPT, model, "remote"], is_capturing=False)
        if code != 0:
            context.say(f"downloading {model} failed with exit code {code}")
            return 1
    context.say(f"start it with: {python} -m mlx_vlm.server --model {model} --port {MLX_VLM_SERVER_PORT}")

    return 0


def _run_command(arguments: list[str],
                 is_capturing: bool) -> tuple[int, str]:
    """Run a command; a captured one hands back its output, a shown one prints straight to the console."""

    try:
        finished = subprocess.run(arguments, capture_output=is_capturing, text=True, check=False)
    except OSError as exc:
        return 127, str(exc)

    return finished.returncode, (finished.stdout + finished.stderr) if is_capturing else ""


def _fetch_json(url: str) -> dict:
    """The URL's JSON body. httpx brings its own certificates, which a python.org Python's urllib lacks."""

    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OSError(str(exc)) from exc

    return response.json()


def _find_python_paths() -> list[Path]:
    """The Python running this command, then every python3 on PATH and in the usual install folders, each once."""

    found: list[Path] = [Path(sys.executable)]
    for name in ["python3", *(f"python3.{minor}" for minor in range(8, 20))]:
        location = shutil.which(name)
        if location:
            found.append(Path(location))
    for pattern in _PYTHON_GLOBS:
        found.extend(path for path in sorted(Path("/").glob(pattern.lstrip("/")))
                     if _PYTHON_NAME.match(path.name))
    unique: dict[Path, Path] = {}
    for path in found:
        unique.setdefault(path.resolve(), path)

    return list(unique.values())


def _read_macos_version() -> tuple[int, int]:
    parts = (platform.mac_ver()[0] or "0.0").split(".")

    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def list_supported_versions(requires_python: str,
                            wheel_filenames: list[str],
                            macos_version: tuple[int, int]) -> list[tuple[int, int]]:
    """Python versions, oldest first, that `requires_python` accepts and that have an mlx macOS arm64 wheel this
    macOS can install. A free-threaded build (`cp313t`) does not count."""

    specifier = SpecifierSet(requires_python)
    versions: set[tuple[int, int]] = set()
    for filename in wheel_filenames:
        tags = "-".join(filename.removesuffix(".whl").split("-")[2:])
        match = _MAC_WHEEL.match(tags)
        if match is None or (int(match.group(3)), int(match.group(4))) > macos_version:
            continue
        version = (int(match.group(1)), int(match.group(2)))
        if specifier.contains(_render_version(version)):
            versions.add(version)

    return sorted(versions)


def build_system_context() -> MlxSetupContext:
    """This machine: real commands, PyPI, the terminal when there is one."""

    brew = shutil.which("brew")

    return MlxSetupContext(run=_run_command, fetch_json=_fetch_json,
                               ask=input if sys.stdin.isatty() else None, find_pythons=_find_python_paths,
                               say=lambda line: print(line, flush=True), brew=Path(brew) if brew else None,
                               system=platform.system(), machine=platform.machine(),
                               macos_version=_read_macos_version() if platform.system() == "Darwin" else (0, 0))


def set_up_mlx(model: str | None,
               venv_dir: Path,
               context: MlxSetupContext) -> int:
    """0 when mlx-vlm imports (and the model, when named, is downloaded); 2 when the machine lacks what it needs;
    1 when an install or download step failed."""

    # 1. mlx runs only on Apple silicon.
    if context.system != "Darwin" or context.machine != "arm64":
        context.say(f"mlx runs only on an Apple silicon Mac; this machine is {context.system} "
                        f"{context.machine}")
        return 2

    # 2. mlx-vlm already installed anywhere on the machine is used as it is.
    python = _find_installed_python(venv_dir, context)

    # 3. Otherwise: a Python that fits, then the venv with mlx-vlm in it.
    if python is None:
        try:
            supported = _read_supported_versions(context)
        except OSError as exc:
            context.say(f"cannot read mlx-vlm's requirements from PyPI: {exc}")
            return 2
        if not supported:
            context.say(f"mlx has no build on PyPI for macOS {_render_version(context.macos_version)}")
            return 2
        base_python = _choose_python(supported, context) or _install_python(supported, context)
        if base_python is None:
            return 2
        python = _create_venv(base_python, venv_dir, context)
        if python is None:
            return 1

    # 4. The model, when one is named.
    if model is None:
        context.say(f"mlx-vlm is ready in {python}; no model named, so nothing downloaded")
        return 0

    return _fetch_model(python, model, context)
