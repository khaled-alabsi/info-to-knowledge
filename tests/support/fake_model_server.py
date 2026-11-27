"""A stand-in model server for the tests of `services/endpoint_client.py`, answering every route a model server answers.

It is bound on port 0 when it is built, so a request sent before its thread has started is queued by the operating system
and answered once `serve_forever` runs: nothing waits on the clock. It listens on 127.0.0.1 only and loads no model.

The routes and their replies are the ones `docs/models.md` lists. A test changes one answer with `server.set_reply`,
makes the next connections drop with `server.drop_connections`, and reads what the program sent from `server.request_list`.
"""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

MODEL_NAME = "stand-in-model"                                 # the one model `GET /v1/models` lists


class RecordedRequest:
    """One request the stand-in server took: its method, path, headers and body."""

    def __init__(self,
                 method: str,
                 path: str,
                 header_dict: dict[str, str],
                 body: bytes) -> None:
        self.method = method
        self.path = path
        self.header_dict = header_dict
        self.body = body

    def read_json(self) -> dict:
        """Give me the body of a JSON request as an object."""

        return json.loads(self.body)

    def read_multipart_fields(self) -> list[tuple[str, str | None, bytes]]:
        """Give me every part of a `multipart/form-data` body as (field name, file name or None, content)."""

        return parse_multipart(content_type=self.header_dict["Content-Type"], body=self.body)


def parse_multipart(content_type: str,
                    body: bytes) -> list[tuple[str, str | None, bytes]]:
    """Give me every part of a `multipart/form-data` body as (field name, file name or None, content)."""

    boundary = content_type.split("boundary=", 1)[1].encode("utf-8")
    part_list = []
    for chunk in body.split(b"--" + boundary):
        if not chunk.startswith(b"\r\n"):
            continue
        head, _, content = chunk[2:].partition(b"\r\n\r\n")
        content = content[:-2] if content.endswith(b"\r\n") else content
        name = None
        file_name = None
        for line in head.decode("utf-8").split("\r\n"):
            if line.lower().startswith("content-disposition"):
                for field in line.split(";")[1:]:
                    key, _, value = field.strip().partition("=")
                    if key == "name":
                        name = value.strip('"')
                    elif key == "filename":
                        file_name = value.strip('"')
        part_list.append((name, file_name, content))

    return part_list


def _build_chat_reply(request: RecordedRequest) -> dict:
    return {"id": "chat-1", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "stand-in text",
                                     "reasoning_content": "stand-in reasoning"}}]}


def _build_transcription_reply(request: RecordedRequest) -> dict:
    """The `diarized_json` reply when the request asks for it, else `verbose_json` with one word that has no `probability`."""

    if b"diarized_json" in request.body:
        return {"segments": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
                             {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0}]}

    return {"language": "en", "duration": 2.0, "text": "hello world",
            "segments": [{"id": 0, "start": 0.0, "end": 2.0, "text": "hello world"}],
            "words": [{"start": 0.0, "end": 1.0, "text": "hello", "probability": 0.9},
                      {"start": 1.0, "end": 2.0, "text": "world"}]}


def _build_embeddings_reply(request: RecordedRequest) -> dict:
    """Answer the way vLLM does: one vector for a request whose `messages` hold an image, one for each text of `input`."""

    body = request.read_json()
    image_count = len(body["input"]) if "input" in body else 1

    return {"object": "list", "model": MODEL_NAME,
            "data": [{"object": "embedding", "index": index, "embedding": [float(index), 0.5, 0.25]}
                     for index in range(image_count)]}


def _build_classifications_reply(request: RecordedRequest) -> dict:
    image_count = len(request.read_json()["images"])

    return {"results": [{"decision": "keep", "score": 0.75} for _ in range(image_count)]}


def _build_models_reply(request: RecordedRequest) -> dict:
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}


ROUTE_DICT = {
    ("POST", "/v1/chat/completions"): _build_chat_reply,
    ("POST", "/v1/audio/transcriptions"): _build_transcription_reply,
    ("POST", "/v1/embeddings"): _build_embeddings_reply,
    ("POST", "/v1/classifications"): _build_classifications_reply,
    ("GET", "/v1/models"): _build_models_reply,
}


class FakeModelServer(ThreadingHTTPServer):
    """A server that answers every route of `ROUTE_DICT` and keeps what it was asked."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _FakeModelHandler)
        self.request_list: list[RecordedRequest] = []
        self.reply_dict: dict[str, tuple[int, bytes]] = {}
        self.dropped_count = 0

    def build_url(self) -> str:
        """Give me the address a settings file holds for this server, up to and including `/v1`."""

        return f"http://127.0.0.1:{self.server_address[1]}/v1"

    def set_reply(self,
                  path: str,
                  status: int,
                  body: bytes) -> None:
        """Make every later request to `path` (`/v1/embeddings`) get HTTP `status` and `body`, whatever it asks."""

        self.reply_dict[path] = (status, body)

    def drop_connections(self,
                         count: int) -> None:
        """Make the next `count` requests be closed with no reply at all, as a server that crashed does."""

        self.dropped_count = count


class _FakeModelHandler(BaseHTTPRequestHandler):

    def _write_reply(self,
                     status: int,
                     body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        request = RecordedRequest(method=self.command, path=self.path,
                                  header_dict={key: value for key, value in self.headers.items()},
                                  body=self.rfile.read(length) if length else b"")
        server = self.server
        server.request_list.append(request)
        if server.dropped_count > 0:
            server.dropped_count -= 1
            self.close_connection = True
            return
        if self.path in server.reply_dict:
            status, body = server.reply_dict[self.path]
            self._write_reply(status=status, body=body)
            return
        build_reply = ROUTE_DICT.get((self.command, self.path))
        if build_reply is None:
            self._write_reply(status=404, body=json.dumps({"error": {"message": f"no route {self.command} {self.path}"}}).encode("utf-8"))
            return
        self._write_reply(status=200, body=json.dumps(build_reply(request)).encode("utf-8"))

    do_GET = _handle
    do_POST = _handle

    def log_message(self,
                    format: str,
                    *args) -> None:
        # a test's server keeps its requests in `request_list`; nothing is printed
        return


@contextlib.contextmanager
def serving(server: ThreadingHTTPServer):
    """Run `server` on a thread for the length of the block, then stop it and free its port."""

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


TEST_HOST = "test-server"                                     # the entry of `hosts` a test's server is added under


@contextlib.contextmanager
def serving_host(server: FakeModelServer):
    """Run `server` for the block as the entry `TEST_HOST` of `hosts`, which is put back after."""

    previous_entry = config.settings.hosts.get(TEST_HOST)
    config.settings.hosts[TEST_HOST] = config.HostSettings(url=server.build_url())
    try:
        with serving(server):
            yield server
    finally:
        if previous_entry is None:
            del config.settings.hosts[TEST_HOST]
        else:
            config.settings.hosts[TEST_HOST] = previous_entry


@contextlib.contextmanager
def serving_kind(server: FakeModelServer,
                 kind: str):
    """Run `server` for the block as the host `providers.<kind>` names, `TEST_HOST`; the kind's host is put back after."""

    provider = getattr(config.settings.providers, kind)
    previous_host = provider.host
    provider.host = TEST_HOST
    try:
        with serving_host(server):
            yield server
    finally:
        provider.host = previous_host
