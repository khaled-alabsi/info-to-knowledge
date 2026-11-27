"""Worker tags for runs whose phase 4 ended before the final answer held them: one
call each, on phase 4's model, handed the published document's title and executive summary and the tag inventory, as
the final follow-up is. The tags go through `run_tags_service`, so they reach the document's metadata block too."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage

import config
from chains import chain_runner
from models.operation_result import OperationResult
from prompts import write_messages
from schemas.run_tags_answer import RunTagsAnswer
from services import chat_model_service
from services import run_tags_service
from utils import display_path_utils

__all__ = ["add_finished_run_tags"]

_PHASE = "finalize"
# How much of the published document the call reads: its title and executive summary come first
_DOCUMENT_CHARS = 6_000
_SYSTEM = ("You tag finished documents written from recordings of meetings and talks, so runs can be found and grouped. "
           "Answer through the function you were given, once.")


def _read_model_tags(document: Path) -> list[str]:
    """The tags the model gives the document; none when its reply could not be read."""

    text = document.read_text(encoding="utf-8")
    body = text.split("\n---\n", 1)[1] if text.startswith("---\n") and "\n---\n" in text[4:] else text
    messages = [SystemMessage(content=_SYSTEM),
                HumanMessage(content=f"The document's start:\n\n{body[:_DOCUMENT_CHARS]}{write_messages.render_tag_ask()}")]
    # Asked to think nothing: thinking, a call over 6,000 characters took about 5 minutes, and 49 runs most of an evening
    model = chat_model_service.bind_no_thinking(chat_model_service.get_model(_PHASE))
    reply = chain_runner.invoke_structured_chain(model, RunTagsAnswer, chat_model_service.resolve_structured_method(_PHASE),
                                                 messages)

    return list(reply.parsed.tags) if reply.parsed is not None else []


def add_finished_run_tags(roots: list[Path],
                          report: Callable[[str], None] = lambda line: None) -> OperationResult:
    """Every run folder under `roots` with a published document and no worker tags yet (a tags file naming only its
    naming rules' and its format tag, or none) given up to `tags.workerTags` tags from one call. Each run's tags go to
    `report` the moment it has them: on 74 run folders, at minutes a call, a command that printed only at its end
    showed nothing for an hour."""

    tagged = 0
    for root in roots:
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            document = run_tags_service.find_published_document(folder) if folder.is_dir() else None
            if document is None:
                continue
            held = run_tags_service.read_tags(folder)
            rule_tags = {tag.casefold() for tag in run_tags_service.list_rule_tags(folder.name)}
            # the program's own tags are no worker's: a format tag ("from-video") is on every run
            if any(tag.casefold() not in rule_tags and not run_tags_service.is_format_tag(tag) for tag in held):
                continue
            inventory = [tag for tag, _ in run_tags_service.list_tag_inventory(roots)]
            picked = run_tags_service.match_inventory_tags(_read_model_tags(document), inventory, config.settings.tags.worker_tags)
            now = run_tags_service.apply_tag_changes(folder, picked, [])
            tagged += 1
            report(f"{display_path_utils.render_path(folder)}: {', '.join(now) or '(none)'}")

    return OperationResult(0, [f"{tagged} finished run(s) given worker tags"])
