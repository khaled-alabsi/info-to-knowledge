"""The decision model's server (`i2k serve-models`) answers `POST /v1/classifications` and `GET /v1/models` and nothing else:
one decision and score for each image in the order sent, the question handed to the model as written, a body it cannot read
refused with 400 and its reason, a model that fails answered with 500, and the program's own client reads its reply. The
model is a stand-in that scores an image by its brightness: no model is loaded."""

from __future__ import annotations

import argparse
import base64
import io
import json
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from PIL import Image

from support import harness

from cli import serve_models_command
from models.endpoint_spec import EndpointSpec
from services import decision_model_server_service
from services import endpoint_client


def _build_data_url(brightness: int,
                    size: tuple[int, int] = (64, 48)) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", size, (brightness, brightness, brightness)).save(buffer, format="PNG")

    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class _BrightnessAsker:
    """A stand-in model: the probability of yes is the image's brightness, and it keeps the questions it was asked."""

    def __init__(self) -> None:
        self.question_list: list[str] = []

    def __call__(self,
                 image: Image.Image,
                 question: str) -> float:
        self.question_list.append(question)

        return image.getpixel((0, 0))[0] / 255


def _fail_to_answer(image: Image.Image,
                    question: str) -> float:
    raise MemoryError("the card is out of memory")


def _send(server: ThreadingHTTPServer,
          method: str,
          path: str,
          body: bytes | None = None) -> tuple[int, dict]:
    """Give me the status and the JSON reply of one request to `server`, whatever the status is."""

    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", data=body, method=method)
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read())


def _serve(ask):
    """The server on a free port of this machine, run on a thread by `fake_model_server.serving`."""

    return decision_model_server_service.build_server(host="127.0.0.1", port=0, ask=ask)


def test_each_image_gets_a_decision_and_a_score_in_the_order_sent():
    from support import fake_model_server

    asker = _BrightnessAsker()
    server = _serve(ask=asker)
    body = json.dumps({"images": [_build_data_url(brightness=255), _build_data_url(brightness=0),
                                  _build_data_url(brightness=204)], "question": "Is this screen blank?"}).encode("utf-8")
    with fake_model_server.serving(server):
        status, reply = _send(server=server, method="POST", path="/v1/classifications", body=body)
    harness.check("the request is answered", status == 200, str(status))
    harness.check("one result for each image, in the order sent: bright keeps, dark drops, the score is the probability",
                  [(result["decision"], result["score"]) for result in reply["results"]]
                  == [("keep", 1.0), ("drop", 0.0), ("keep", 0.8)], str(reply))
    harness.check("the model was asked the question as written, once for each image",
                  asker.question_list == ["Is this screen blank?"] * 3, str(asker.question_list))


def test_the_program_reads_the_reply_with_its_own_client():
    from support import fake_model_server

    server = _serve(ask=_BrightnessAsker())
    with fake_model_server.serving(server):
        endpoint = EndpointSpec(base_url=f"http://127.0.0.1:{server.server_address[1]}/v1", api_key_env="", timeout_s=30, model="")
        result_list = endpoint_client.request_decisions(endpoint=endpoint, image_url_list=[_build_data_url(brightness=255)],
                                                        question="Does this screen show information?")
    harness.check("the client gets one keep with its score", result_list == [{"decision": "keep", "score": 1.0}], str(result_list))


def test_the_models_route_names_the_one_model_it_serves():
    from support import fake_model_server

    server = _serve(ask=_BrightnessAsker())
    with fake_model_server.serving(server):
        status, reply = _send(server=server, method="GET", path="/v1/models")
    harness.check("one model, the decision model", status == 200 and [model["id"] for model in reply["data"]] == ["LiquidAI/d1-2B"],
                  str(reply))


def test_every_other_route_is_a_404_that_names_it():
    from support import fake_model_server

    server = _serve(ask=_BrightnessAsker())
    with fake_model_server.serving(server):
        chat_status, chat_reply = _send(server=server, method="POST", path="/v1/chat/completions", body=b"{}")
        ocr_status, ocr_reply = _send(server=server, method="GET", path="/v1/ocr")
    harness.check("a chat request is no route of this server", chat_status == 404 and "/v1/chat/completions" in chat_reply["error"]["message"],
                  str(chat_reply))
    harness.check("a text reading request is none either", ocr_status == 404 and "/v1/ocr" in ocr_reply["error"]["message"], str(ocr_reply))


def test_a_body_it_cannot_read_is_a_400_with_the_reason():
    from support import fake_model_server

    server = _serve(ask=_BrightnessAsker())
    case_dict = {"not JSON": b"{broken", "no question": json.dumps({"images": [_build_data_url(brightness=9)]}).encode("utf-8"),
                 "no images": json.dumps({"images": [], "question": "q"}).encode("utf-8"),
                 "an image that is not a data URL": json.dumps({"images": ["https://x/y.png"], "question": "q"}).encode("utf-8"),
                 "an image that is not a picture": json.dumps({"images": ["data:image/png;base64,AAAA"], "question": "q"}).encode("utf-8")}
    with fake_model_server.serving(server):
        reply_dict = {name: _send(server=server, method="POST", path="/v1/classifications", body=body) for name, body in case_dict.items()}
    for name, (status, reply) in reply_dict.items():
        harness.check(f"{name}: HTTP 400 with a message", status == 400 and reply["error"]["message"] != "", f"{status} {reply}")


class _ListLogger:
    """A stand-in for the command's logger that keeps every line it is given."""

    def __init__(self) -> None:
        self.line_list: list[str] = []

    def info(self,
             line: str) -> None:
        self.line_list.append(line)

    def error(self,
              line: str) -> None:
        self.line_list.append(line)


def _fail_to_load() -> None:
    raise RuntimeError("the decision model needs torch and transformers")


def test_the_command_listens_on_this_machine_only_by_default_and_names_a_model_that_cannot_load():
    parser = argparse.ArgumentParser()
    serve_models_command.add_serve_models_arguments(parser)
    defaults = parser.parse_args([])
    logger = _ListLogger()
    kept_loader = decision_model_server_service.load_model_asker
    decision_model_server_service.load_model_asker = _fail_to_load
    try:
        exit_code = serve_models_command.cmd_serve_models(argparse.Namespace(host="127.0.0.1", port=0, logger=logger))
    finally:
        decision_model_server_service.load_model_asker = kept_loader
    line_list = logger.line_list
    harness.check("the default address is this machine's own, so the frames stay on it", defaults.host == "127.0.0.1", defaults.host)
    harness.check("the default port is 8790", defaults.port == 8790, str(defaults.port))
    harness.check("a model that cannot load is exit code 2 and the error is said by name",
                  exit_code == 2 and any("torch and transformers" in line for line in line_list), f"{exit_code} {line_list}")


def test_a_model_that_fails_is_a_500_with_its_message_and_the_server_goes_on():
    from support import fake_model_server

    failing = _serve(ask=_fail_to_answer)
    body = json.dumps({"images": [_build_data_url(brightness=9)], "question": "q"}).encode("utf-8")
    with fake_model_server.serving(failing):
        status, reply = _send(server=failing, method="POST", path="/v1/classifications", body=body)
        later_status, _ = _send(server=failing, method="GET", path="/v1/models")
    harness.check("HTTP 500 that carries the model's message", status == 500 and "the card is out of memory" in reply["error"]["message"],
                  str(reply))
    harness.check("the next request is still answered", later_status == 200, str(later_status))
