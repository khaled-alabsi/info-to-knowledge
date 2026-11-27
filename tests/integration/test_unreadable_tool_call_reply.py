"""GMT20210810's chapter 3 part 2 on this Mac's LM Studio (DEFECTS 722): the host answered a call with HTTP 500, "The
model produced output that does not match the expected peg-native format", its tool-call reader failing on the model's
reply, and the follow-up blocked after 0 attempts, stopping phase 4. Here a local HTTP host made for the test answers the
first chat call with that exact error and the second with a submit_chapter tool call, through the real OpenAI-compatible
client, the real streamed read and the real follow-up loop: the chapter is accepted."""

import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import harness

from cases import test_write_follow_up_loop as loop_cases
from models.validation_report import ValidationReport
from services import host_return_service
from services import openai_compatible_client
from services import write_conversation_service

HOST_ERROR = {"error": {"code": 500, "message": "The model produced output that does not match the expected "
                                                "peg-native format", "type": "server_error"}}


class _Host:
    def __init__(self):
        self.chats = 0


def _serve(host: _Host) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {"data": [{"id": "made-up-model", "state": "loaded", "loaded_context_length": 163_840}]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            host.chats += 1
            if host.chats == 1:
                self._send(500, HOST_ERROR)
                return
            arguments = json.dumps({"markdown": loop_cases.CHAPTER_MARKDOWN})
            call = {"index": 0, "id": "call_1", "type": "function",
                    "function": {"name": "submit_chapter", "arguments": arguments}}
            piece = {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [call]},
                                  "finish_reason": "tool_calls"}]}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(piece)}\n\ndata: [DONE]\n\n".encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    return server


def test_a_chapter_whose_reply_the_host_cannot_read_is_asked_again_and_accepted_defect():
    # The host's 500 on an unreadable reply blocked the chapter part at once; now it is asked again
    host = _Host()
    server = _serve(host)
    seen, restore = loop_cases._judge_with([ValidationReport(subject="chapter-01-text")])
    waited = host_return_service.wait_for_lost_model
    host_return_service.wait_for_lost_model = lambda phase: False
    try:
        session = loop_cases._build_session()
        model = openai_compatible_client.OpenAICompatibleChatModel(
            model="made-up-model", base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="none", max_retries=0)
        session = dataclasses.replace(session, model=model)
        outcome = write_conversation_service.run_follow_up(session, dataclasses.replace(loop_cases.CHAPTER, max_attempts=1))
    finally:
        restore()
        host_return_service.wait_for_lost_model = waited
        server.shutdown()
    harness.check("the host was asked twice", host.chats == 2, str(host.chats))
    harness.check("the chapter is accepted", outcome.is_accepted and len(seen) == 1, f"{outcome} {seen}")
