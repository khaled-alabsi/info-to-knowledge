"""A phase 7 part on a model host that lists its model READY and answers every call with LM Studio's "Compute error"
(made data: a small HTTP server on this machine speaking the standard model list and LM Studio's error body, the real
calls of the wait and the real part call file and parts table). The part waited 23 minutes while its row read "writing
for 22m 59s, 0 words of thinking"; now the part says it waits for the model host, with the host's error."""

import json
import logging
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from langchain_core.messages import AIMessage

from support import harness

import config
from models import output_contract
from models.run_paths import RunPaths
from services import chat_model_service
from services import document_pass_service
from services import document_reshaping_service
from services import host_return_service

ERROR_BODY = {"error": "Engine protocol predict stream returned an error: "
                       "{\"code\":500,\"message\":\"Compute error.\",\"type\":\"server_error\"}"}


def _start_host(model: str,
                broken_calls: int) -> tuple[ThreadingHTTPServer, list[str]]:
    """A host listing `model` loaded whose first `broken_calls` chat calls answer LM Studio's engine error."""

    seen: list[str] = []

    class Host(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            seen.append(f"list {self.path}")
            self._answer(200, {"object": "list", "data": [{"id": model, "object": "model"}]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append("call")
            if seen.count("call") <= broken_calls:
                self._answer(500, ERROR_BODY)
            else:
                self._answer(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Host)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    return server, seen


def test_a_part_on_a_model_answering_compute_error_says_it_waits_for_the_host_defect():
    # Defect: LM Studio listed its model READY and answered "Compute error" to every call; the part's row said it was
    # writing for 23 minutes. Fix: the part's row and live panel name the wait and the host's error
    role = config.settings.resolve_model(config.settings.resolve_phase_model_name(
        output_contract.DOCUMENT_RESHAPING_PHASE))
    provider, model = chat_model_service.split_spec(role.spec)
    if provider != "lmstudio":
        print("phase 7's model is not on LM Studio in these settings: skipped")
        return
    server, seen = _start_host(model, broken_calls=2)
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    cleanup = folder / output_contract.DOCUMENT_RESHAPING_DIR_NAME
    cleanup.mkdir(parents=True)
    (cleanup / "state.json").write_text(json.dumps({"pass": "knowledge", "batches": [[1]], "closed": {}}),
                                        encoding="utf-8")
    (cleanup / "batch-001.jsonl").write_text(json.dumps({"at": 100.0, "call": 1, "text": "", "tools": []}) + "\n",
                                             encoding="utf-8")
    shown, replies = {}, []

    def pause(seconds):
        # the wait's pause between two checks: the moment the page reads the part
        shown.setdefault("row", document_pass_service.describe_parts(folder, is_live=True, now=10_000_000_000.0)[0])
        shown.setdefault("doing", document_pass_service.describe_part_activity(folder, 1, now=10_000_000_000.0)["doing"])

    def read_streamed(bound, messages, live, on_first_piece=None):
        replies.append(1)
        if len(replies) == 1:
            raise ConnectionError("the stream broke")
        return AIMessage(content="done")

    saved_url = chat_model_service.resolve_host_url
    saved_read = document_reshaping_service.chain_runner.read_streamed_reply
    saved_clock = host_return_service.set_clock(lambda: 0.0, pause)
    chat_model_service.resolve_host_url = lambda cfg: f"http://127.0.0.1:{server.server_port}/v1"
    document_reshaping_service.chain_runner.read_streamed_reply = read_streamed
    try:
        reply = document_reshaping_service.stream_reply(paths, None, [], 1, logging.getLogger("test"))
    finally:
        chat_model_service.resolve_host_url = saved_url
        document_reshaping_service.chain_runner.read_streamed_reply = saved_read
        host_return_service.set_clock(*saved_clock)
        server.shutdown()
    harness.check("the host was asked over HTTP until the model answered", seen.count("call") == 3, str(seen))
    harness.check("the program asked only the standard model list and sent calls, never a load or an unload",
                  set(seen) == {"list /v1/models", "call"}, str(seen))
    harness.check("the part's call is asked again and answers", reply.content == "done", str(reply))
    harness.check("while it waits, the row names the host's error and no thinking",
                  "Compute error." in shown["row"]["host_wait"] and shown["row"]["thinking"] == "", str(shown["row"]))
    harness.check("and the live panel says it waits for the model host",
                  shown["doing"]["what"].startswith("waiting for the model host"), str(shown["doing"]))
