"""The endpoint client calls a model server by URL: each route of `docs/models.md` against a stand-in server on port 0,
a connection that is refused, a dropped connection tried once more, an HTTP 500, and a reply that misses a field. Each
failure names the URL and the HTTP status."""

from __future__ import annotations

import json
import os

from support import harness
from support import fake_model_server

import config
from models.endpoint_errors import EndpointError
from models.endpoint_spec import EndpointSpec
from models.endpoint_spec import MultipartFileSpec
from services import endpoint_client

AUDIO = MultipartFileSpec(file_name="part-1.mp3", content=b"stand-in audio bytes", media_type="audio/mpeg")


def _build_endpoint(server: fake_model_server.FakeModelServer,
                    api_key_env: str = "",
                    model: str = "") -> EndpointSpec:
    return EndpointSpec(base_url=server.build_url(), api_key_env=api_key_env, timeout_s=10, model=model)


def _find_error(call) -> EndpointError | None:
    """Give me the `EndpointError` a call raises; None when it raises nothing."""

    try:
        call()
    except EndpointError as failed:
        return failed

    return None


def test_chat_completion_sends_the_messages_and_reads_the_text():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        body = {"model": "qwen", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
        reply = endpoint_client.request_chat_completion(endpoint=_build_endpoint(server=server), body=body)
    sent = server.request_list[0]
    harness.check("one POST to the chat route", (sent.method, sent.path) == ("POST", "/v1/chat/completions"),
                  f"{sent.method} {sent.path}")
    harness.check("the body goes as JSON", sent.header_dict["Content-Type"] == "application/json" and sent.read_json() == body)
    message = reply["choices"][0]["message"]
    harness.check("the text and the reasoning come back", (message["content"], message["reasoning_content"]) ==
                  ("stand-in text", "stand-in reasoning"), str(message))


def test_transcription_sends_the_audio_as_multipart_and_reads_words_with_and_without_probability():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        reply = endpoint_client.request_transcription(endpoint=_build_endpoint(server=server, model="whisper-large"),
                                                      audio=AUDIO, language="en", granularity_list=["word", "segment"])
    sent = server.request_list[0]
    part_list = sent.read_multipart_fields()
    harness.check("one POST to the transcription route",
                  (sent.method, sent.path) == ("POST", "/v1/audio/transcriptions"), f"{sent.method} {sent.path}")
    harness.check("the audio is the part named file, with its name and bytes",
                  ("file", "part-1.mp3", AUDIO.content) in part_list, str([(name, file) for name, file, _ in part_list]))
    field_list = [(name, content.decode("utf-8")) for name, file, content in part_list if file is None]
    harness.check("the fields are the model, the language, verbose_json and both granularities, in order",
                  field_list == [("model", "whisper-large"), ("language", "en"), ("response_format", "verbose_json"),
                                 ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")],
                  str(field_list))
    harness.check("the segments come back", reply["segments"][0]["text"] == "hello world", str(reply["segments"]))
    harness.check("a word has its probability when the server gives it", reply["words"][0]["probability"] == 0.9)
    harness.check("a word the server gives no probability for stays without one, never zero",
                  "probability" not in reply["words"][1], str(reply["words"][1]))


def test_transcription_without_a_language_or_a_model_leaves_both_fields_out():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        endpoint_client.request_transcription(endpoint=_build_endpoint(server=server), audio=AUDIO, language="",
                                              granularity_list=[])
    names = [name for name, file, _ in server.request_list[0].read_multipart_fields() if file is None]
    harness.check("only response_format is sent", names == ["response_format"], str(names))


def test_speaker_turns_ask_for_diarized_json_and_read_speaker_start_and_end():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        reply = endpoint_client.request_speaker_turns(endpoint=_build_endpoint(server=server), audio=AUDIO)
    field_list = [(name, content.decode("utf-8")) for name, file, content in server.request_list[0].read_multipart_fields()
                  if file is None]
    harness.check("the request asks for diarized_json", ("response_format", "diarized_json") in field_list, str(field_list))
    harness.check("each turn has a speaker, a start and an end",
                  reply["segments"][1] == {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0}, str(reply["segments"]))


def test_embeddings_send_each_image_as_a_messages_request_and_give_one_vector_each_in_order():
    server = fake_model_server.FakeModelServer()
    image_list = [endpoint_client.build_data_url(content=b"crop-1", media_type="image/png"),
                  endpoint_client.build_data_url(content=b"crop-2", media_type="image/png")]
    with fake_model_server.serving(server):
        vector_list = endpoint_client.request_embeddings(endpoint=_build_endpoint(server=server, model="siglip2"),
                                                         data_url_list=image_list)
    first = server.request_list[0]
    harness.check("one POST to the embeddings route for each image",
                  [(sent.method, sent.path) for sent in server.request_list] == [("POST", "/v1/embeddings")] * 2,
                  str([(sent.method, sent.path) for sent in server.request_list]))
    expected = {"model": "siglip2", "encoding_format": "float",
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_list[0]}}]}]}
    harness.check("the body is vLLM's: the model, one user message with an image_url part, the float format",
                  first.read_json() == expected, str(first.read_json()))
    harness.check("the second request holds the second image",
                  server.request_list[1].read_json()["messages"][0]["content"][0]["image_url"]["url"] == image_list[1])
    harness.check("an image is a base64 data URL", image_list[0] == "data:image/png;base64,Y3JvcC0x", image_list[0])
    harness.check("one vector per image, in the order sent", vector_list == [[0.0, 0.5, 0.25], [0.0, 0.5, 0.25]],
                  str(vector_list))


def test_text_embeddings_send_the_texts_as_the_standard_input_list_and_give_one_vector_each():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        vector_list = endpoint_client.request_text_embeddings(endpoint=_build_endpoint(server=server, model="siglip2"),
                                                              text_list=["a board", "a face"])
    harness.check("one POST holds both texts as `input`",
                  len(server.request_list) == 1 and server.request_list[0].read_json()
                  == {"model": "siglip2", "input": ["a board", "a face"], "encoding_format": "float"},
                  str([sent.read_json() for sent in server.request_list]))
    harness.check("one vector per text, in the order sent", vector_list == [[0.0, 0.5, 0.25], [1.0, 0.5, 0.25]], str(vector_list))


def test_decisions_send_the_images_and_the_question_and_give_one_decision_each():
    server = fake_model_server.FakeModelServer()
    image_list = ["data:image/png;base64,AAAA", "data:image/png;base64,BBBB", "data:image/png;base64,CCCC"]
    with fake_model_server.serving(server):
        result_list = endpoint_client.request_decisions(endpoint=_build_endpoint(server=server), image_url_list=image_list,
                                                        question="does this frame show knowledge?")
    sent = server.request_list[0]
    harness.check("one POST to the classifications route", (sent.method, sent.path) == ("POST", "/v1/classifications"))
    harness.check("the images and the question go in the body",
                  sent.read_json() == {"images": image_list, "question": "does this frame show knowledge?"})
    harness.check("one decision and score per image", result_list == [{"decision": "keep", "score": 0.75}] * 3, str(result_list))


def test_listing_the_models_is_one_get_and_gives_the_names():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        name_list = endpoint_client.list_served_models(endpoint=_build_endpoint(server=server))
    sent = server.request_list[0]
    harness.check("one GET to the models route with no body", (sent.method, sent.path, sent.body) == ("GET", "/v1/models", b""))
    harness.check("the names come back", name_list == [fake_model_server.MODEL_NAME], str(name_list))


def test_the_key_goes_in_an_authorization_header_when_the_variable_names_one():
    server = fake_model_server.FakeModelServer()
    os.environ["I2K_TEST_ENDPOINT_KEY"] = "secret-123"
    try:
        with fake_model_server.serving(server):
            endpoint_client.list_served_models(endpoint=_build_endpoint(server=server, api_key_env="I2K_TEST_ENDPOINT_KEY"))
            endpoint_client.list_served_models(endpoint=_build_endpoint(server=server))
            del os.environ["I2K_TEST_ENDPOINT_KEY"]
            missing = _find_error(lambda: endpoint_client.list_served_models(
                endpoint=_build_endpoint(server=server, api_key_env="I2K_TEST_ENDPOINT_KEY")))
    finally:
        os.environ.pop("I2K_TEST_ENDPOINT_KEY", None)
    harness.check("the key goes as a Bearer token", server.request_list[0].header_dict.get("Authorization") == "Bearer secret-123")
    harness.check("a server with no key variable gets no Authorization header",
                  "Authorization" not in server.request_list[1].header_dict)
    harness.check("a variable that is not set is an error naming it, and nothing is sent",
                  missing is not None and "I2K_TEST_ENDPOINT_KEY" in missing.detail and len(server.request_list) == 2,
                  str(missing))


def test_a_refused_connection_is_an_error_naming_the_url():
    server = fake_model_server.FakeModelServer()
    endpoint = _build_endpoint(server=server)
    server.server_close()
    failed = _find_error(lambda: endpoint_client.list_served_models(endpoint=endpoint))
    harness.check("nobody answered: an EndpointError with no status that names the URL",
                  failed is not None and failed.status is None and failed.url == endpoint.base_url + "/models", str(failed))


def test_a_connection_dropped_once_is_tried_again_and_dropped_twice_is_an_error():
    server = fake_model_server.FakeModelServer()
    with fake_model_server.serving(server):
        server.drop_connections(count=1)
        name_list = endpoint_client.list_served_models(endpoint=_build_endpoint(server=server))
        harness.check("the second try gets the answer", name_list == [fake_model_server.MODEL_NAME], str(name_list))
        harness.check("the server saw the request twice", len(server.request_list) == 2, str(len(server.request_list)))
        server.request_list.clear()
        server.drop_connections(count=2)
        failed = _find_error(lambda: endpoint_client.list_served_models(endpoint=_build_endpoint(server=server)))
    harness.check("a connection dropped twice is an error with no status", failed is not None and failed.status is None, str(failed))
    harness.check("it is tried twice and no more", len(server.request_list) == 2, str(len(server.request_list)))


def test_an_http_500_is_an_error_with_the_status_and_the_servers_message_and_is_not_tried_again():
    server = fake_model_server.FakeModelServer()
    server.set_reply(path="/v1/embeddings", status=500, body=json.dumps({"error": {"message": "the card is out of memory"}}).encode("utf-8"))
    with fake_model_server.serving(server):
        failed = _find_error(lambda: endpoint_client.request_embeddings(endpoint=_build_endpoint(server=server),
                                                                       data_url_list=["data:image/png;base64,AAAA"]))
    harness.check("the status is 500", failed is not None and failed.status == 500, str(failed))
    harness.check("the error carries the server's message", failed is not None and "the card is out of memory" in failed.detail,
                  str(failed))
    harness.check("the error names the URL", failed is not None and failed.url.endswith("/v1/embeddings"), str(failed))
    harness.check("a server that answered is asked once", len(server.request_list) == 1, str(len(server.request_list)))


def test_an_unknown_route_is_a_404_that_names_the_url():
    server = fake_model_server.FakeModelServer()
    endpoint = EndpointSpec(base_url=server.build_url() + "/nowhere", api_key_env="", timeout_s=10)
    with fake_model_server.serving(server):
        failed = _find_error(lambda: endpoint_client.list_served_models(endpoint=endpoint))
    harness.check("HTTP 404 with the stand-in's message", failed is not None and failed.status == 404 and "no route" in failed.detail,
                  str(failed))


def test_a_reply_that_misses_a_field_is_an_error_naming_the_field():
    server = fake_model_server.FakeModelServer()
    endpoint = _build_endpoint(server=server)
    server.set_reply(path="/v1/chat/completions", status=200, body=b'{"choices": []}')
    server.set_reply(path="/v1/audio/transcriptions", status=200, body=b'{"text": "no segments"}')
    server.set_reply(path="/v1/embeddings", status=200, body=b'{"data": [{"index": 0}]}')
    server.set_reply(path="/v1/classifications", status=200, body=b'{"results": []}')
    server.set_reply(path="/v1/models", status=200, body=b'{"data": [{"name": "no id"}]}')
    with fake_model_server.serving(server):
        failure_dict = {
            "chat": _find_error(lambda: endpoint_client.request_chat_completion(endpoint=endpoint, body={"messages": []})),
            "transcription": _find_error(lambda: endpoint_client.request_transcription(endpoint=endpoint, audio=AUDIO, language="",
                                                                                       granularity_list=[])),
            "embeddings": _find_error(lambda: endpoint_client.request_embeddings(endpoint=endpoint, data_url_list=["x"])),
            "decisions": _find_error(lambda: endpoint_client.request_decisions(endpoint=endpoint, image_url_list=["x"], question="q")),
            "models": _find_error(lambda: endpoint_client.list_served_models(endpoint=endpoint)),
        }
    for route, field in (("chat", "message"), ("transcription", "segments"), ("embeddings", "embedding"),
                         ("decisions", "decisions came back"), ("models", "id")):
        failed = failure_dict[route]
        harness.check(f"a {route} reply with no {field} is an error with status 200 that names it",
                      failed is not None and failed.status == 200 and field in failed.detail, str(failed))


def test_a_reply_that_is_not_json_is_an_error():
    server = fake_model_server.FakeModelServer()
    server.set_reply(path="/v1/models", status=200, body=b"<html>not a model server</html>")
    with fake_model_server.serving(server):
        failed = _find_error(lambda: endpoint_client.list_served_models(endpoint=_build_endpoint(server=server)))
    harness.check("the error says the reply is not JSON", failed is not None and "not JSON" in failed.detail, str(failed))


def test_find_endpoint_gives_none_for_no_host_and_the_hosts_values_for_a_named_one():
    providers = {"hosts": {"speech-box": {"url": "http://host:8791/v1", "apiKeyEnv": "I2K_KEY"}},
                 "providers": {"speech": {"host": "speech-box", "model": "whisper-large", "timeoutS": 42}}}
    kept = config.settings
    config.settings = config.Settings(**providers)
    try:
        speech = endpoint_client.find_endpoint(kind="speech")
        chat = endpoint_client.find_endpoint(kind="chat")
        unknown = _find_unknown_kind_error(kind="voice")
        in_process_error_list = [_find_unknown_kind_error(kind=kind) for kind in ("ocr", "detection")]
    finally:
        config.settings = kept
    harness.check("a kind naming a host gives that host's address and key with the kind's model and timeout",
                  speech == EndpointSpec(base_url="http://host:8791/v1", api_key_env="I2K_KEY", timeout_s=42, model="whisper-large"),
                  str(speech))
    harness.check("a kind naming no host gives none, so the program keeps its own way", chat is None, str(chat))
    harness.check("a kind that does not exist is refused by name", "voice" in unknown, unknown)
    harness.check("text reading and detection are no kind of server: each is refused by name, as they run inside the program",
                  ["ocr" in in_process_error_list[0], "detection" in in_process_error_list[1]] == [True, True]
                  and "decision" in in_process_error_list[0], str(in_process_error_list))


def test_find_endpoint_reads_a_library_model_for_its_server_its_model_name_and_its_wait_time():
    values = {"hosts": {"gpu-box": {"url": "http://gpu-box.local:8090/v1", "apiKeyEnv": "GPU_KEY"}},
              "models": {"whisper-gpu": {"spec": "lmstudio:whisper-large-v3", "host": "gpu-box", "requestTimeoutS": 7200},
                         "reader-gpu": {"spec": "lmstudio:ocr-model", "host": "gpu-box"},
                         "nowhere": {"spec": "openai-compatible:x"}},
              "providers": {"speech": {"libraryModel": "whisper-gpu", "host": "ignored", "timeoutS": 42},
                            "chat": {"libraryModel": "reader-gpu"}, "decision": {"libraryModel": "nowhere"},
                            "diarization": {"libraryModel": "missing"}}}
    kept = config.settings
    config.settings = config.Settings(**values)
    try:
        speech = endpoint_client.find_endpoint(kind="speech")
        chat = endpoint_client.find_endpoint(kind="chat")
        no_server = endpoint_client.find_endpoint(kind="decision")
        unknown = _find_unknown_library_model_error(kind="diarization")
    finally:
        config.settings = kept
    harness.check("a library model gives its host's address and key, its model's name and the wait time it sets",
                  speech == EndpointSpec(base_url="http://gpu-box.local:8090/v1", api_key_env="GPU_KEY", timeout_s=7200,
                                         model="whisper-large-v3"), str(speech))
    harness.check("a library model that sets no wait time takes the kind's own",
                  chat == EndpointSpec(base_url="http://gpu-box.local:8090/v1", api_key_env="GPU_KEY", timeout_s=600, model="ocr-model"),
                  str(chat))
    harness.check("a library model naming no server keeps the program's own way", no_server is None, str(no_server))
    harness.check("a name the library does not hold is refused, naming the models there are",
                  "missing" in unknown and "whisper-gpu" in unknown, unknown)


def _find_unknown_library_model_error(kind: str) -> str:
    try:
        endpoint_client.find_endpoint(kind=kind)
    except ValueError as refused:
        return str(refused)

    return ""


def _find_unknown_kind_error(kind: str) -> str:
    try:
        endpoint_client.find_endpoint(kind=kind)
    except ValueError as refused:
        return str(refused)

    return ""
