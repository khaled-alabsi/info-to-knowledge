"""Phase 4 on a chosen path, for the cases that script one path. REQUIREMENTS.md R12.

The default is the conversation; a case that scripts one call per piece pins the calls path it drives, and a case
that scripts the follow-ups of the conversation pins the conversation, never inheriting the default either way.
"""

from __future__ import annotations

import config as app_config
from graph.nodes import write_document_node
from services import host_window_service


def _answer_nothing(url: str):
    raise OSError(f"no host in the test suite: {url}")


def run_phase_4_in_calls_mode(state: dict,
                              config: dict) -> dict:
    """Phase 4 on its calls path, the setting put back afterwards."""

    previous = app_config.settings.document.write_mode
    app_config.settings.document.write_mode = "calls"
    try:
        return write_document_node.write_document_node(state, config)
    finally:
        app_config.settings.document.write_mode = previous


def run_phase_4_in_conversation_mode(state: dict,
                                     config: dict) -> dict:
    """Phase 4 as one conversation, the setting and the host's window listing put back afterwards. The suite reaches
    no model host: the local settings may name LM Studio, so its window listing answers as a host that is not there."""

    previous_mode = app_config.settings.document.write_mode
    app_config.settings.document.write_mode = "conversation"
    previous_fetch = host_window_service.set_fetch_json(_answer_nothing)
    try:
        return write_document_node.write_document_node(state, config)
    finally:
        app_config.settings.document.write_mode = previous_mode
        host_window_service.set_fetch_json(previous_fetch)
