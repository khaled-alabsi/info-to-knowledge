"""Text is read by a model server when `providers.ocr.url` is set: the words and boxes a page gives are the ones Tesseract
gives it, a word under the confidence floor is dropped, the server is asked once for each image, a server that fails gives
no words, and a scanned page goes to the server's chat route with the instruction the reading model was trained on. With
the URL empty nothing is sent."""

from __future__ import annotations

import base64
import io
import json
import tempfile
from pathlib import Path

import numpy
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from support import fake_model_server
from support import harness

import config
from services import board_text_reader_service
from services import text_detection_service
from services import unlimited_ocr_reader_service

PAGE_SIZE = (1000, 400)
PAGE_LINES = ("Knowledge from recordings", "Frames words and boxes", "Scanned pages read by a model")
OCR_PATH = "/v1/ocr"
CHAT_PATH = "/v1/chat/completions"


def _render_page() -> numpy.ndarray:
    """Give me a white page with three lines of large black text, as the picture a reader is handed (BGR, as OpenCV reads)."""

    page = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=44)
    for number, line in enumerate(PAGE_LINES):
        draw.text((30, 30 + number * 110), line, fill="black", font=font)

    return numpy.asarray(page)[:, :, ::-1].copy()


def _write_page(folder: Path) -> Path:
    path = folder / "page.png"
    Image.fromarray(_render_page()[:, :, ::-1]).save(path)

    return path


def _build_reply_of(word_list: list[dict],
                    confidence: float) -> bytes:
    """Give me the `/v1/ocr` reply of a server that reads exactly `word_list`, every word at `confidence`."""

    return json.dumps({"words": [{"text": word["text"], "box": word["box"], "line": word["line"], "block": word["block"],
                                  "confidence": confidence} for word in word_list]}).encode("utf-8")


def _set_ocr_url(url: str) -> dict:
    kept = config.settings
    config.settings = config.Settings(**{"providers": {"ocr": {"url": url, "model": "reader"}}})

    return kept


def _columns(word_list: list[dict]) -> list[tuple]:
    return [(word["text"], word["box"], word["block"], word["line"]) for word in word_list]


def test_the_words_and_boxes_of_a_page_are_the_ones_tesseract_gives():
    page = _render_page()
    own = text_detection_service.find_words_in_image(page)
    server = fake_model_server.FakeModelServer()
    server.set_reply(OCR_PATH, 200, _build_reply_of(word_list=own, confidence=95.0))
    with fake_model_server.serving(server):
        kept = _set_ocr_url(server.build_url())
        try:
            remote = text_detection_service.find_words_in_image(page)
            blocks = text_detection_service.group_text_blocks(remote)
        finally:
            config.settings = kept
    harness.check("Tesseract reads at least five words of the page, so the comparison holds something", len(own) >= 5, str(len(own)))
    harness.check("the server's words have the same text, boxes, blocks and lines as Tesseract's",
                  _columns(remote) == _columns(own), str(_columns(remote)[:3]))
    harness.check("the text blocks formed from them are the ones Tesseract's words form",
                  blocks == text_detection_service.group_text_blocks(own), str(blocks))


def test_a_word_below_the_confidence_floor_or_with_no_text_is_dropped():
    server = fake_model_server.FakeModelServer()
    word_list = [{"text": "kept", "box": [10, 10, 80, 40], "line": 1, "confidence": 80.0},
                 {"text": "noise", "box": [90, 10, 150, 40], "line": 1, "confidence": 20.0},
                 {"text": "  ", "box": [160, 10, 170, 40], "line": 1, "confidence": 99.0}]
    server.set_reply(OCR_PATH, 200, json.dumps({"words": word_list}).encode("utf-8"))
    with fake_model_server.serving(server):
        kept = _set_ocr_url(server.build_url())
        try:
            found = text_detection_service.find_words_in_image(_render_page())
        finally:
            config.settings = kept
    harness.check("only the sure word with text is kept, its block the line it is on when the server sends none",
                  _columns(found) == [("kept", [10, 10, 80, 40], 1, 1)], str(found))


def test_the_server_is_asked_once_for_each_image_with_the_languages_and_the_model():
    folder = Path(tempfile.mkdtemp())
    path = _write_page(folder)
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        kept = _set_ocr_url(server.build_url())
        try:
            first = text_detection_service.find_words(path)
            second = text_detection_service.find_words(path)
        finally:
            config.settings = kept
    request_list = [request for request in server.request_list if request.path == OCR_PATH]
    body = request_list[0].read_json()
    sent = Image.open(io.BytesIO(base64.b64decode(body["image"].split(",", 1)[1])))
    harness.check("two reads of one file ask the server once, and give the same words",
                  len(request_list) == 1 and first == second and len(first) == 2, f"{len(request_list)} requests")
    harness.check("the request holds the image at its own size", sent.size == PAGE_SIZE, str(sent.size))
    harness.check("the request names the languages the settings give and the model the settings name",
                  body["languages"] == ["eng"] and body["model"] == "reader", str({key: body[key] for key in ("languages", "model")}))


def test_a_server_that_fails_gives_no_words_and_a_figure_it_could_not_read():
    folder = Path(tempfile.mkdtemp())
    path = _write_page(folder)
    server = fake_model_server.FakeModelServer()
    server.set_reply(OCR_PATH, 500, b'{"error": {"message": "model not loaded"}}')
    with fake_model_server.serving(server):
        kept = _set_ocr_url(server.build_url())
        try:
            words = text_detection_service.find_words_in_image(_render_page())
            figure_words = text_detection_service.read_figure_words(path)
        finally:
            config.settings = kept
    harness.check("no words, and no figure words (never an empty set that reads as 'no text')",
                  words == [] and figure_words is None, f"{words} {figure_words}")


def test_the_board_reader_comes_to_the_server_and_an_empty_url_sends_nothing():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        kept = _set_ocr_url(server.build_url())
        try:
            chosen = board_text_reader_service.set_reader(board_text_reader_service.AUTO)
            described = board_text_reader_service.describe_reader()
            board_text_reader_service.find_words_in_image(_render_page())
        finally:
            config.settings = kept
        sent_to_server = len([request for request in server.request_list if request.path == OCR_PATH])
        chosen_without = board_text_reader_service.set_reader(board_text_reader_service.TESSERACT)
        text_detection_service.find_words_in_image(_render_page())
    harness.check("'auto' with a server set comes to the server, which the console names",
                  chosen == board_text_reader_service.SERVER and described == "the model server's text reader", f"{chosen} {described}")
    harness.check("the board's words were asked of the server once", sent_to_server == 1, str(sent_to_server))
    harness.check("with the URL empty the reader is Tesseract and the server gets no further request",
                  chosen_without == board_text_reader_service.TESSERACT
                  and len([request for request in server.request_list if request.path == OCR_PATH]) == 1,
                  f"{chosen_without} {len(server.request_list)}")


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


def test_a_scanned_page_goes_to_the_servers_chat_route_with_the_models_instruction():
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
            kept = _set_ocr_url(server.build_url())
            try:
                installed = unlimited_ocr_reader_service.is_installed()
                readings = unlimited_ocr_reader_service.read_pages([first, second], lambda read, total: told.append((read, total)))
            finally:
                config.settings = kept
    finally:
        fake_model_server.ROUTE_DICT[route] = kept_route
    body_list = [request.read_json() for request in server.request_list if request.path == CHAT_PATH]
    content = body_list[0]["messages"][0]["content"]
    harness.check("a server set for text makes the reader count as installed", installed, str(installed))
    harness.check("each page's reading comes back in order, the looped page read again", readings == ["page one text", "page two text"],
                  str(readings))
    harness.check("three requests: the looped page twice, the other once", len(body_list) == 3, str(len(body_list)))
    harness.check("a request holds the page as an image and the model's instruction",
                  [part["type"] for part in content] == ["image_url", "text"] and content[1]["text"] == "Multi page parsing."
                  and content[0]["image_url"]["url"].startswith("data:image/png;base64,"), str(content[1]))
    harness.check("only the second request of the looped page carries a repetition penalty",
                  "repetition_penalty" not in body_list[0] and body_list[1]["repetition_penalty"] == 1.1
                  and "repetition_penalty" not in body_list[2], str([body.get("repetition_penalty") for body in body_list]))
    harness.check("the progress is told after each page", told == [(1, 2), (2, 2)], str(told))


def test_a_page_the_server_fails_on_is_named_in_the_error():
    folder = Path(tempfile.mkdtemp())
    path = _write_page(folder)
    server = fake_model_server.FakeModelServer()
    server.set_reply(CHAT_PATH, 503, b'{"error": {"message": "busy"}}')
    refused = ""
    with fake_model_server.serving(server):
        kept = _set_ocr_url(server.build_url())
        try:
            unlimited_ocr_reader_service.read_pages([path])
        except RuntimeError as failed:
            refused = str(failed)
        finally:
            config.settings = kept
    harness.check("the error names the page and the server's status", "page.png" in refused and "503" in refused, refused)
