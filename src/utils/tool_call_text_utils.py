"""A tool call a local model wrote into its reply's text instead of sending it as a call. Pure functions.

Some hosts return the call serialised in the body, `{"name": ..., "arguments": {...}}`, when the model skips the tool
channel; a model may also write it in its own tag format, the tool's name and then `<arg_key>` / `<arg_value>` pairs. Phases 3, 4 and 5 read it as the call it is (REQUIREMENTS.md R10 and R12).
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

__all__ = ["read_text_call", "read_bare_arguments", "split_leading_calls"]

# `submit_chapter` then `<arg_key>markdown</arg_key>` `<arg_value>...</arg_value>`, optionally inside `<tool_call>`; a
# chapter answer came back so with its key's closing tag missing and was taken for no call (2026-10-12)
_TAG_CALL = re.compile(r"^\s*(?:<tool_call>\s*)?(?P<name>[A-Za-z_]\w*)\s*\n(?P<pairs>\s*<arg_key>.*?)\s*(?:</tool_call>\s*)?$",
                       re.DOTALL)
_TAG_PAIR = re.compile(r"<arg_key>\s*(?P<key>\w+)\s*(?:</arg_key>)?\s*<arg_value>(?P<value>.*?)</arg_value>", re.DOTALL)


def _read_tag_call(text: str) -> dict[str, Any] | None:
    match = _TAG_CALL.match(text)
    if match is None:
        return None
    pairs = list(_TAG_PAIR.finditer(match.group("pairs")))
    if not pairs:
        return None
    args: dict[str, Any] = {}
    for pair in pairs:
        value = pair.group("value").strip()
        # A number or a list is written as JSON, text as itself
        try:
            args[pair.group("key")] = json.loads(value) if value[:1] in "[{-0123456789" or value in ("true", "false") else value
        except json.JSONDecodeError:
            args[pair.group("key")] = value

    return {"name": match.group("name"), "args": args, "id": ""}


def read_text_call(text: str) -> dict[str, Any] | None:
    """The call written in `text` as `{"name", "args", "id": ""}`, or None when the text is not one."""

    try:
        payload = json.loads(text.strip())
    except json.JSONDecodeError:
        return _read_tag_call(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return None
    # The first key that holds a value: `"arguments": null` is a call with no arguments, as a call with no key is
    arguments = next((payload[key] for key in ("arguments", "args", "parameters") if payload.get(key) is not None), {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None

    return {"name": payload["name"], "args": arguments, "id": ""} if isinstance(arguments, dict) else None


# What can stand around a reply's bare arguments: the tool's name on its own, a thinking block, a code fence
_THINK_BLOCK = re.compile(r"<details>.*?</details>|<think>.*?</think>", re.DOTALL)
_FENCE_LINE = re.compile(r"^\s*```\w*\s*$", re.MULTILINE)


def read_bare_arguments(text: str,
                        tool_name: str) -> dict[str, Any] | None:
    """The arguments of `tool_name` when `text` is exactly one JSON object, the tool's name, a thinking block or a code
    fence around it allowed; None otherwise. A summary was written twice as bare JSON in the reply, once after
    "submit_final" and a thinking block, and each time taken for no answer (pdf-real-fw9)."""

    body = _FENCE_LINE.sub("", _THINK_BLOCK.sub("", text)).strip()
    if body.startswith(tool_name):
        body = body[len(tool_name):].strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None

    return payload if isinstance(payload, dict) and "name" not in payload else None


# A call written as Python in the reply, one a line: `leave_out(frame="frame-002", reason="...")`. A chapter's worker
# wrote three of them as text and then checked its draft, which found the three frames unaccounted for (froscon 3211
# chapter 1, DEFECTS 290)
_WRITTEN_CALL_LINE = re.compile(r"^\s*(?P<name>[A-Za-z_]\w*)\((?P<args>.*)\)\s*;?\s*$")


def _read_written_call(line: str,
                       names: set[str]) -> dict[str, Any] | None:
    match = _WRITTEN_CALL_LINE.match(line)
    if match is None or match.group("name") not in names:
        return None
    try:
        call = ast.parse(f"f({match.group('args')})", mode="eval").body
        args = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
    except (SyntaxError, ValueError):
        return None
    if call.args or None in args:
        return None

    return {"name": match.group("name"), "args": args, "id": ""}


def split_leading_calls(text: str,
                        names: set[str]) -> tuple[list[dict[str, Any]], str]:
    """The calls of the offered tools `names` a reply wrote as Python on its first or its last lines, one a line, with
    keyword arguments of literal values only, in order, and the text between them; no calls and the whole text when
    neither its first nor its last line is such a call, so a chapter quoting code inside it is never read as calls."""

    lines = text.split("\n")
    calls: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip():
            call = _read_written_call(lines[index], names)
            if call is None:
                break
            calls.append(call)
        index += 1
    # ... and on its last lines: a chapter's worker wrote a leave_out below its chapter too (froscon 3211 chapter 2)
    end = len(lines)
    trailing: list[dict[str, Any]] = []
    while end > index:
        if lines[end - 1].strip():
            call = _read_written_call(lines[end - 1], names)
            if call is None:
                break
            trailing.insert(0, call)
        end -= 1
    calls += trailing

    return (calls, "\n".join(lines[index:end]).strip()) if calls else ([], text)
