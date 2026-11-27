"""The real `mlx_vlm.server` started and stopped by the program, serving phase 2's filter
model in its MLX build (LiquidAI/LFM2.5-VL-3B-MLX-8bit, in the Hugging Face cache) from mlx-vlm's environment: the
setup's own (~/.mlx-vlm/venv) or another environment with mlx-vlm installed. A HEAVY test: it loads a 3B model, so it
runs alone."""

import tempfile
from pathlib import Path

import httpx

from support import harness
from services import mlx_server_service, mlx_setup_service

_MODEL = "LiquidAI/LFM2.5-VL-3B-MLX-8bit"
_BASE_URL = "http://127.0.0.1:59880/v1"


def test_the_program_starts_mlx_vlm_for_a_model_and_stops_it():
    python = next((path for path in (mlx_setup_service.DEFAULT_VENV / "bin" / "python",) if path.is_file()), None)
    # mlx-vlm is an optional host this machine runs no phase on (LM Studio is the default): with it not installed, the
    # test has nothing to start, as a test whose real media are absent
    if python is None:
        print("mlx-vlm is not installed (./i2k setup-mlx): skipped")
        return
    log = Path(tempfile.mkdtemp()) / "mlx-server.log"
    process = mlx_server_service.start_server(_BASE_URL, _MODEL, log, python=python)
    try:
        listed = httpx.get(f"{_BASE_URL}/models", timeout=10.0).json()
        harness.check("the server answers and lists the model", _MODEL in str(listed), str(listed)[:300])
    finally:
        mlx_server_service.stop_server(process)
    harness.check("it is stopped after", process.poll() is not None)
