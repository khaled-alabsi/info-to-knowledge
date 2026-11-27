"""`i2k mcp` under this machine's own settings (playbook.default.json and playbook.local.json), a real MCP handshake
through `main.py`: loading every command, the pipeline, LangChain and transformers (installed for a POC in the one
environment) with them, it answered its first message after 29 to 37 s on a loaded machine, past the case file's 30 s
wait and an MCP client's (DEFECTS 475). It now answers with none of them imported."""

import json
import subprocess
import sys

from support import harness

import config

INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}


def test_the_mcp_server_answers_with_no_pipeline_loaded_defect():
    # The first answer came after the whole pipeline was imported; now from the server's own entry
    done = subprocess.run([sys.executable, "-X", "importtime", str(config.PLAYBOOK_ROOT / "main.py"), "mcp"],
                          cwd=config.PLAYBOOK_ROOT, input=json.dumps(INITIALIZE) + "\n", capture_output=True, text=True,
                          timeout=120)
    first = json.loads(done.stdout.splitlines()[0]) if done.stdout.strip() else {}
    names = {line.split("|")[-1].strip() for line in done.stderr.splitlines() if line.startswith("import time:")}
    harness.check("the first line answers initialize", first.get("id") == 1 and "result" in first, done.stdout[:200])
    harness.check("with no CLI command, pipeline, LangChain or transformers imported",
                  not names & {"cli", "graph", "langchain_core", "transformers"},
                  str(sorted(names & {"cli", "graph", "langchain_core", "transformers"})))
