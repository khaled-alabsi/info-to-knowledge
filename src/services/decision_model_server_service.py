"""A small server for Liquid AI's decision model d1-2B, for a person who wants it to run natively on the host (fast on a
Mac, where a container has no GPU) while i2k runs in a container: `i2k serve-models` starts it, and `providers.decision.host`
points at it. It answers two requests and no other:

- `POST /v1/classifications`: a JSON body with `images` (a list of `data:` URLs) and `question`; the reply is `results`,
  one `{"decision": "keep" or "drop", "score": the probability the answer is yes}` for each image, in the order sent.
- `GET /v1/models`: the one model it serves, in the shape the standard servers answer.

The model is asked in `ask(image, question)`, which the server is handed: `load_model_asker` gives the real one, which
loads d1-2B from the Hugging Face cache (`HF_HOME`) on CUDA, Apple's GPU or the processor, in that order; a test hands in
a stand-in and loads nothing. One request is answered at a time, since one model holds the card.
"""

from __future__ import annotations

import base64
import binascii
import importlib
import io
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image
from PIL import UnidentifiedImageError

__all__ = ["MODEL_ID", "KEEP_SCORE", "FRAME_WIDTH", "build_server", "load_model_asker"]

MODEL_ID = "LiquidAI/d1-2B"
KEEP_SCORE = 0.5                   # an answer at least this likely to be yes is a "keep"
FRAME_WIDTH = 640                  # the width a frame is asked at, as `tools/d1-knowledge-filter/filter_frames.py` asks it

# What answers one question about one image: the probability that the answer is yes
Asker = Callable[[Image.Image, str], float]


class _BadRequest(Exception):
    """The request cannot be answered; its text goes back to the sender with HTTP 400."""


class _ModelAsker:
    """d1-2B loaded once, answering one yes-or-no question about one image at a time."""

    def __init__(self,
                 model) -> None:
        self._model = model

    def __call__(self,
                 image: Image.Image,
                 question: str) -> float:
        if image.width > FRAME_WIDTH:
            image = image.resize((FRAME_WIDTH, round(image.height * FRAME_WIDTH / image.width)))
        questions = {"answer": {"type": "noul", "instructions": question}}
        answers = self._model.system_one(None, questions, media=[image])["answers"]

        return float(answers["answer"]["noul"])


def load_model_asker() -> Asker:
    """Load d1-2B and give me what asks it; a missing `torch` or `transformers` is an error that names the file to install.
    They are optional installs, so they are imported here and not when the command line starts."""

    try:
        torch = importlib.import_module("torch")
        AutoModel = importlib.import_module("transformers").AutoModel
    except ImportError as error:
        raise RuntimeError(f"the decision model needs torch and transformers ({error}): install "
                           f"tools/d1-knowledge-filter/requirements.txt into this environment") from error
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.float32 if device == "cpu" else torch.bfloat16
    model = AutoModel.from_pretrained(MODEL_ID, trust_remote_code=True, dtype=dtype).to(device)

    return _ModelAsker(model=model)


def _read_image(data_url: object) -> Image.Image:
    """Give me the image a `data:` URL holds, in RGB; a request whose image cannot be read is a bad request."""

    if not isinstance(data_url, str) or "," not in data_url:
        raise _BadRequest("an image is not a data: URL")
    try:
        return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1], validate=True))).convert("RGB")
    except (binascii.Error, UnidentifiedImageError, OSError) as error:
        raise _BadRequest(f"an image cannot be read ({type(error).__name__})") from error


class _DecisionModelServer(ThreadingHTTPServer):
    """A server that answers `POST /v1/classifications` with `ask` and `GET /v1/models`."""

    daemon_threads = True

    def __init__(self,
                 address: tuple[str, int],
                 ask: Asker) -> None:
        super().__init__(address, _DecisionHandler)
        self.ask = ask
        self.ask_lock = threading.Lock()

    def build_reply(self,
                    body: dict) -> dict:
        """Give me the reply to a `POST /v1/classifications` whose JSON body is `body`."""

        data_url_list, question = body.get("images"), body.get("question")
        if not isinstance(data_url_list, list) or not data_url_list or not isinstance(question, str) or not question:
            raise _BadRequest("the body needs 'images', a list of data: URLs, and 'question', a text")
        image_list = [_read_image(data_url=data_url) for data_url in data_url_list]
        with self.ask_lock:
            score_list = [self.ask(image, question) for image in image_list]

        return {"model": MODEL_ID,
                "results": [{"decision": "keep" if score >= KEEP_SCORE else "drop", "score": round(score, 4)}
                            for score in score_list]}


class _DecisionHandler(BaseHTTPRequestHandler):

    def _write_json(self,
                    status: int,
                    reply: dict) -> None:
        body = json.dumps(reply).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_error(self,
                     status: int,
                     message: str) -> None:
        self._write_json(status=status, reply={"error": {"message": message}})

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self._write_error(status=404, message=f"no route GET {self.path}")
            return
        self._write_json(status=200, reply={"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path != "/v1/classifications":
            self._write_error(status=404, message=f"no route POST {self.path}")
            return
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise _BadRequest("the body is not a JSON object")
            reply = self.server.build_reply(body=body)
        except json.JSONDecodeError:
            self._write_error(status=400, message="the body is not JSON")
            return
        except _BadRequest as refused:
            self._write_error(status=400, message=str(refused))
            return
        except Exception as failed:
            self._write_error(status=500, message=f"the decision model failed: {type(failed).__name__}: {failed}")
            return
        self._write_json(status=200, reply=reply)

    def log_message(self,
                    format: str,
                    *args) -> None:
        # a request line for every frame would bury the console; a failure is answered with its message
        return


def build_server(host: str,
                 port: int,
                 ask: Asker) -> ThreadingHTTPServer:
    """Give me the server bound to `host` and `port` (0 takes a free one), answering with `ask`; the caller runs it."""

    return _DecisionModelServer((host, port), ask)
