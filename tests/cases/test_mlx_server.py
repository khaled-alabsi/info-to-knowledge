"""mlx-vlm started by the program for a run whose models it serves: which servers are
started, a server's ready line awaited, and a server that ends or never starts refused by name."""

import sys
import tempfile
from pathlib import Path

from support import harness

from services import mlx_server_service

_ROWS = [{"phase": "frames_processing", "name": "frame-mlx", "provider": "openai-compatible", "model": "acme/vl-3b"},
         {"phase": "finalize", "name": "document", "provider": "lmstudio", "model": "qwen"},
         {"phase": "review", "name": "remote", "provider": "openai-compatible", "model": "acme/remote"}]
_URLS = {"frame-mlx": "http://127.0.0.1:59871/v1", "document": "http://127.0.0.1:1234/v1",
         "remote": "http://203.0.113.7:8080/v1"}


def test_only_a_model_on_a_local_openai_compatible_address_gets_a_server():
    servers = mlx_server_service.list_servers_to_start(_ROWS, _URLS.get)
    harness.check("the local mlx model alone, an LM Studio model and a remote address left alone",
                  servers == [("http://127.0.0.1:59871/v1", "acme/vl-3b")], str(servers))


def _fake_server(lines: str) -> Path:
    """A stand-in for `python -m mlx_vlm.server`: a script that prints `lines` and then waits or ends."""

    folder = Path(tempfile.mkdtemp())
    script = folder / "python"
    # a ready server waits for its stop signal, as the real one does; any other ends at once
    script.write_text(f"#!{sys.executable}\nimport signal, sys\nprint({lines!r}, flush=True)\n"
                      f"signal.pause() if 'Uvicorn' in {lines!r} else sys.exit(3)\n", encoding="utf-8")
    script.chmod(0o755)

    return script


def test_a_server_is_ready_at_its_ready_line_and_stopped_after():
    log = Path(tempfile.mkdtemp()) / "mlx-server.log"
    process = mlx_server_service.start_server("http://127.0.0.1:59872/v1", "acme/vl-3b", log,
                                              python=_fake_server("INFO:     Uvicorn running on http://127.0.0.1:59872"))
    harness.check("returned once the ready line came, the server still running", process.poll() is None)
    mlx_server_service.stop_server(process)
    harness.check("stopped", process.poll() is not None)
    harness.check("its output is in the log", "Uvicorn running" in log.read_text(encoding="utf-8"))


def test_a_server_that_ends_before_its_ready_line_is_refused_by_name():
    log = Path(tempfile.mkdtemp()) / "mlx-server.log"
    try:
        mlx_server_service.start_server("http://127.0.0.1:59873/v1", "acme/vl-3b", log,
                                        python=_fake_server("model not found"))
    except RuntimeError as error:
        harness.check("refused, naming the model and the log", "acme/vl-3b" in str(error) and str(log) in str(error),
                      str(error))
        return
    harness.check("refused", False)


def test_a_missing_mlx_environment_names_the_setup_command():
    try:
        mlx_server_service.start_server("http://127.0.0.1:59874/v1", "acme/vl-3b", Path(tempfile.mkdtemp()) / "l.log",
                                        python=Path("/no/such/python"))
    except FileNotFoundError as error:
        harness.check("the setup command is named", "i2k setup-mlx acme/vl-3b" in str(error), str(error))
        return
    harness.check("refused", False)
