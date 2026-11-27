"""A scanned page is read through the standard chat request when `providers.chat.host` names a host: the page goes as an image
with the instruction the reading model was trained on, a page that loops to the token limit is asked again with a
repetition penalty, and a failing server is named in the error. The request names the model `pdf.unlimitedOcrModel`."""

from __future__ import annotations

import base64
import io
import tempfile
from pathlib import Path

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from support import fake_model_server
from support import harness

import config
from services import unlimited_ocr_reader_service

PAGE_SIZE = (1000, 400)
PAGE_LINES = ("Knowledge from recordings", "Frames words and boxes", "Scanned pages read by a model")
CHAT_PATH = "/v1/chat/completions"


def _write_page(folder: Path) -> Path:
    """Give me a PNG of a white page with three lines of black text, `PAGE_SIZE` big."""

    page = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=44)
    for number, line in enumerate(PAGE_LINES):
        draw.text((30, 30 + number * 110), line, fill="black", font=font)
    path = folder / "page.png"
    page.save(path)

    return path


def _set_chat_url(url: str) -> dict:
    kept = config.settings
    config.settings = config.Settings(**{"hosts": {fake_model_server.TEST_HOST: {"url": url}},
                                         "providers": {"chat": {"host": fake_model_server.TEST_HOST}}})

    return kept


def _build_page_reply(request: fake_model_server.RecordedRequest) -> dict:
    """Give me the chat reply of a reading server: the full-size page loops to the token limit until it is asked again with a
    repetition penalty, the small page reads at once."""

    body = request.read_json()
    data_url = body["messages"][0]["content"][0]["image_url"]["url"]
    size = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).size
    if size != PAGE_SIZE:
        text, finish_reason = "page two text", "stop"
    elif "repetition_penalty" in body:
        text, finish_reason = "page one text", "stop"
    else:
        text, finish_reason = "looping ....", "length"

    return {"choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": text}}]}


def test_a_scanned_page_goes_to_the_chat_server_with_the_models_instruction():
    folder = Path(tempfile.mkdtemp())
    first, second = _write_page(folder), folder / "page-2.png"
    Image.new("RGB", (PAGE_SIZE[0] // 2, PAGE_SIZE[1] // 2), "white").save(second)
    route = ("POST", CHAT_PATH)
    kept_route = fake_model_server.ROUTE_DICT[route]
    fake_model_server.ROUTE_DICT[route] = _build_page_reply
    server = fake_model_server.FakeModelServer()
    told = []
    try:
        with fake_model_server.serving(server):
            kept = _set_chat_url(server.build_url())
            try:
                installed = unlimited_ocr_reader_service.is_installed()
                readings = unlimited_ocr_reader_service.read_pages([first, second], lambda read, total: told.append((read, total)))
            finally:
                config.settings = kept
    finally:
        fake_model_server.ROUTE_DICT[route] = kept_route
    body_list = [request.read_json() for request in server.request_list if request.path == CHAT_PATH]
    content = body_list[0]["messages"][0]["content"]
    harness.check("a chat server set makes the reader count as installed", installed, str(installed))
    harness.check("each page's reading comes back in order, the looped page read again", readings == ["page one text", "page two text"],
                  str(readings))
    harness.check("three requests: the looped page twice, the other once", len(body_list) == 3, str(len(body_list)))
    harness.check("a request names the reading model of the settings", body_list[0]["model"] == config.settings.pdf.unlimited_ocr_model,
                  body_list[0]["model"])
    harness.check("a request holds the page as an image and the model's instruction",
                  [part["type"] for part in content] == ["image_url", "text"] and content[1]["text"] == "Multi page parsing."
                  and content[0]["image_url"]["url"].startswith("data:image/png;base64,"), str(content[1]))
    harness.check("only the second request of the looped page carries a repetition penalty",
                  "repetition_penalty" not in body_list[0] and body_list[1]["repetition_penalty"] == 1.1
                  and "repetition_penalty" not in body_list[2], str([body.get("repetition_penalty") for body in body_list]))
    harness.check("the progress is told after each page", told == [(1, 2), (2, 2)], str(told))


def test_a_page_the_chat_server_fails_on_is_named_in_the_error():
    folder = Path(tempfile.mkdtemp())
    path = _write_page(folder)
    server = fake_model_server.FakeModelServer()
    server.set_reply(CHAT_PATH, 503, b'{"error": {"message": "busy"}}')
    refused = ""
    with fake_model_server.serving(server):
        kept = _set_chat_url(server.build_url())
        try:
            unlimited_ocr_reader_service.read_pages([path])
        except RuntimeError as failed:
            refused = str(failed)
        finally:
            config.settings = kept
    harness.check("the error names the page and the server's status", "page.png" in refused and "503" in refused, refused)
