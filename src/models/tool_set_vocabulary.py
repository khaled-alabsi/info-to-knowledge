"""Whose tools a conversation offers: phase 4 writes the document, phase 5 reviews it."""

from __future__ import annotations

from models import output_contract

__all__ = ["WRITE_TOOL_SET", "REVIEW_TOOL_SET", "CONVERSATION_LOG_RELPATHS", "PHASE_OF_TOOL_SET"]

WRITE_TOOL_SET = "write"
REVIEW_TOOL_SET = "review"
# Where each tool set's conversation logs every call
CONVERSATION_LOG_RELPATHS = {WRITE_TOOL_SET: output_contract.WRITE_CONVERSATION_LOG,
                             REVIEW_TOOL_SET: output_contract.REVIEW_CONVERSATION_LOG}
# The phase each tool set's conversation belongs to
PHASE_OF_TOOL_SET = {WRITE_TOOL_SET: "finalize", REVIEW_TOOL_SET: "review"}
