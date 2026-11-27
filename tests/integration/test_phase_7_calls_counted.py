"""A phase 7 part's call, made through phase 7's own streaming code and the real OpenAI-compatible client against a
host on this machine (made data: a small HTTP server streaming one reply and its token counts the way LM Studio
does), shows on the phase's closing line. A knowledge pass's closing table read "0 calls" after 17 hours of calls."""

import json
import logging
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from langchain_core.messages import HumanMessage

from support import harness

from models import output_contract
from models.run_context import RunContext
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from services import call_record_service
from services import document_reshaping_service
from services import performance_log_service
from services import run_paths_service
from services.ignore_list_service import IgnoreList
from services.openai_compatible_client import OpenAICompatibleChatModel
from services.run_store_service import RunStore
from utils import json_lines_utils

PROMPT_TOKENS, OUTPUT_TOKENS = 1_234, 56


def _start_host() -> ThreadingHTTPServer:
    """A host that streams "part closed" in two pieces, then its usage, then the end of the stream."""

    class Host(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            base = {"id": "c1", "object": "chat.completion.chunk", "model": "m"}
            pieces = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "part "}}]},
                      {**base, "choices": [{"index": 0, "delta": {"content": "closed"}, "finish_reason": "stop"}]},
                      {**base, "choices": [], "usage": {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": OUTPUT_TOKENS,
                                                        "total_tokens": PROMPT_TOKENS + OUTPUT_TOKENS}}]
            for piece in pieces:
                self.wfile.write(f"data: {json.dumps(piece)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Host)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    return server


def _load_run_context() -> RunContext:
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module="doc.pdf", output_root_override=folder / "out")
    run_paths_service.ensure_run_paths(paths)
    run_dir = folder / "run"
    run_dir.mkdir()
    store = RunStore.create(run_dir / "run.json",
                            RunIdentity(run_id="t", repository=str(folder), module="doc.pdf",
                                        output_root=str(paths.output_root)), budgets={"maxAttempts": 1}, models=[])

    return RunContext(repository=folder, module="doc.pdf", paths=paths, store=store, run_dir=run_dir,
                      log=logging.getLogger("phase-7-calls-test"), ignore=IgnoreList(), model=None)


def test_a_phase_7_part_s_call_is_counted_on_the_phase_s_closing_line_defect():
    # Defect: a knowledge pass's phase 7 closing table read "0 calls" and "no model call" after 17 hours of calls.
    # Fix: every call through chain_runner is one row of the phase started last, so phase 7's calls are counted
    server = _start_host()
    ctx = _load_run_context()
    phase = output_contract.DOCUMENT_RESHAPING_PHASE
    (ctx.paths.output_root / output_contract.DOCUMENT_RESHAPING_DIR_NAME).mkdir(parents=True, exist_ok=True)
    model = OpenAICompatibleChatModel(model="m", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                                      api_key="not-needed", stream_usage=True)
    try:
        handles = performance_log_service.start_unit(ctx, phase, None)
        reply = document_reshaping_service.stream_reply(ctx.paths, model, [HumanMessage(content="Read part 1.")], 1,
                                                        logging.getLogger("phase-7-calls-test"))
        row = performance_log_service.record_phase(ctx, phase, "complete", handles)
    finally:
        call_record_service.clear_place()
        server.shutdown()
    harness.check("the host's reply came back whole", "part closed" in str(reply.content), str(reply.content))
    speed_rows = [row for row in json_lines_utils.read_json_lines(document_reshaping_service.build_batch_log_path(ctx.paths, 1))
                  if "writing_seconds" in row]
    harness.check("the call's written tokens and writing time are in the part's turn log, for its average speed",
                  len(speed_rows) == 1 and speed_rows[0]["written_tokens"] == OUTPUT_TOKENS, str(speed_rows))
    harness.check("the phase's closing line counts the part's one call and the host's token counts",
                  row.get("calls") == 1 and row.get("promptTokens") == PROMPT_TOKENS
                  and row.get("outputTokens") == OUTPUT_TOKENS, str(row))
