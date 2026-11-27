"""What the person wrote to a worker, added to the worker's conversation before its next call. Apart from
`worker_message_service`, which the page and `i2k mcp` read, since this one needs LangChain's message types."""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from services import worker_message_service

__all__ = ["add_waiting_to_conversation"]


def add_waiting_to_conversation(path: Path,
                                messages: list[BaseMessage]) -> int:
    """Add what waits to the conversation `messages`; how many. An answer in words never ends a worker's work: only its
    closing tool does, and each loop sends a reply with no tool call back to work."""

    waiting = worker_message_service.take_waiting(path)
    for message in waiting:
        if worker_message_service.is_system_message(message):
            messages[0] = SystemMessage(content=worker_message_service.render_system_text(messages[0].content, message))
        else:
            messages.append(HumanMessage(content=worker_message_service.render_user_text(message)))

    return len(waiting)
