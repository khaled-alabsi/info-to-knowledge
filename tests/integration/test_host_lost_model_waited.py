"""A model host that loses its model while a call runs (DEFECTS 317): release test 1's Mac restarted under the second
pass, the call ended "terminated", and two retries failed in seconds on a host with no model, so the step spent its
three attempts and phase 3 stopped. Here a local HTTP host made for the test answers as LM Studio does: its listing at
`/api/v0/models` and its `/v1/chat/completions`, whose first call fails and drops the model. Through the real client,
the real dispatch and the real listing read; only the pause between two checks is the module's seam."""

import json
import logging
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from langchain_core.messages import HumanMessage

from support import harness

import config
from models import call_outcome_vocabulary
from models import output_contract
from models.chain_call_spec import ChainCallSpec
from models.run_paths import RunPaths
from services import chain_dispatch_service
from services import chat_model_service
from services import document_reshaping_service
from services import host_return_service

PHASE = "frames_processing"
MODEL_NAME = "host-test"
BROKEN_MODEL_NAME = "host-test-broken"
MODEL = "made-up-model"
FIRST_FAILURE = "terminated"
SECOND_FAILURE = "second call reached the host"
ENGINE_ERROR = "Compute error: the engine failed"
REPLY = "part call answered"


class _Host:
    def __init__(self):
        self.is_loaded = True
        self.is_broken = False
        self.is_stream_answered = False
        self.completions = 0


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

        def _send_stream(self):
            piece = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}]}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(piece)}\n\ndata: [DONE]\n\n".encode())

        def do_GET(self):
            state = "loaded" if host.is_loaded else "not-loaded"
            self._send(200, {"data": [{"id": MODEL, "state": state, "loaded_context_length": 8_192}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if host.is_broken:
                self._send(500, {"error": {"message": ENGINE_ERROR}})
                return
            if body.get("max_tokens") == 1:
                self._send(200, {"choices": [{"message": {"content": "ok"}}]})
                return
            if host.is_stream_answered:
                self._send_stream()
                return
            host.completions += 1
            if host.completions == 1:
                host.is_loaded = False
            message = FIRST_FAILURE if host.completions == 1 else SECOND_FAILURE
            self._send(400, {"error": {"message": message}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    return server


def test_a_call_whose_host_lost_its_model_is_asked_again_once_it_is_back_defect():
    # the step spent three attempts in seconds on a host with no model loaded, and phase 3 stopped;
    # now the run waits for the model to be listed loaded again and asks the call again
    host = _Host()
    server = _serve(host)
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    settings = config.settings
    previous_models, previous_phases = dict(settings.models), dict(settings.phase_models)
    settings.models[MODEL_NAME] = config.ModelSettings(spec=f"lmstudio:{MODEL}", base_url=url, request_timeout_s=10)
    settings.phase_models[PHASE] = MODEL_NAME
    pauses = []

    def pause(seconds):
        pauses.append(seconds)
        host.is_loaded = len(pauses) >= 2

    previous_clock = host_return_service.set_clock(lambda: 0.0, pause)
    try:
        result = chain_dispatch_service.invoke_chain(None, ChainCallSpec(PHASE, "critique"), [HumanMessage("x")])
    finally:
        host_return_service.set_clock(*previous_clock)
        settings.models.clear()
        settings.models.update(previous_models)
        settings.phase_models.clear()
        settings.phase_models.update(previous_phases)
        server.shutdown()
    harness.check("the call is asked again once the model is back", host.completions == 2, str(host.completions))
    harness.check("the host was waited for until it listed the model", len(pauses) == 2, str(pauses))
    harness.check("the attempt's result is the second call's", result.outcome == call_outcome_vocabulary.CALL_ERROR
                  and SECOND_FAILURE in (result.detail or ""), str(result.detail))


def test_a_part_call_on_a_broken_load_waits_for_a_working_model_defect():
    # After LM Studio restarted, its loaded model answered every call with an engine error: phase 7's job ended in seconds
    # and the queue ran 12 more into it. Now the part's call waits, through the real client, until a test call answers
    host = _Host()
    host.is_broken = True
    host.is_stream_answered = True
    server = _serve(host)
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    settings = config.settings
    previous_models, previous_phases = dict(settings.models), dict(settings.phase_models)
    settings.models[BROKEN_MODEL_NAME] = config.ModelSettings(spec=f"lmstudio:{MODEL}", base_url=url, request_timeout_s=10)
    settings.phase_models[output_contract.DOCUMENT_RESHAPING_PHASE] = BROKEN_MODEL_NAME
    pauses = []

    def pause(seconds):
        pauses.append(seconds)
        host.is_broken = len(pauses) < 2

    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    previous_clock = host_return_service.set_clock(lambda: 0.0, pause)
    try:
        model = chat_model_service.get_named_model(BROKEN_MODEL_NAME)
        reply = document_reshaping_service.stream_reply(paths, model, [HumanMessage("x")], 1,
                                                        logging.getLogger("test"))
    finally:
        host_return_service.set_clock(*previous_clock)
        settings.models.clear()
        settings.models.update(previous_models)
        settings.phase_models.clear()
        settings.phase_models.update(previous_phases)
        server.shutdown()
    harness.check("the job waited until the test call answered", len(pauses) == 2, str(pauses))
    harness.check("the part's call is answered, the job not ended", reply.content == REPLY, str(reply.content))
