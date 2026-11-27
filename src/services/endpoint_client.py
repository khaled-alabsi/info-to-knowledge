"""Call a model server by URL with the standard REST requests: JSON, multipart and base64 images.

Every kind of model the program reaches by URL (`config.settings.providers`) is called the same way, so the program never
knows which server answers. A request that fails raises `EndpointError`, which names the URL called, the HTTP status the
server answered, or that nobody answered. A connection that is dropped before the server answers is tried once more.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import urllib.error
import urllib.request
import uuid

import config
from models.endpoint_errors import EndpointError
from models.endpoint_spec import EndpointSpec
from models.endpoint_spec import MultipartFileSpec

ERROR_DETAIL_CHARS = 500                                      # how much of a server's refusal the error keeps
REPLY_SHOWN_CHARS = 120                                       # how much of a wrong reply the error shows
# The fields of a `/v1/models` entry in which a server reports a model's loaded window, by the server that writes each:
# LM Studio, vLLM, llama.cpp-style servers
WINDOW_FIELDS = ("loaded_context_length", "max_model_len", "context_length")


def _build_url(endpoint: EndpointSpec,
               path: str) -> str:
    return endpoint.base_url.rstrip("/") + "/" + path.lstrip("/")


def _read_api_key(endpoint: EndpointSpec,
                  url: str) -> str:
    """Give me the key the endpoint's environment variable holds; "" when the endpoint needs none."""

    if not endpoint.api_key_env:
        return ""
    key = os.environ.get(endpoint.api_key_env, "")
    if not key:
        raise EndpointError(url=url, status=None,
                            detail=f"the environment variable {endpoint.api_key_env}, which holds its key, is not set")

    return key


def _read_refusal(refused: urllib.error.HTTPError) -> str:
    """Give me what a server said when it refused: its error message when it sent one in the providers' shape."""

    text = refused.read(ERROR_DETAIL_CHARS * 4).decode(encoding="utf-8", errors="replace").strip()
    try:
        error = json.loads(text).get("error")
    except (json.JSONDecodeError, AttributeError):
        # not the providers' error shape (a plain text or an HTML page): its own text is the detail
        return text[:ERROR_DETAIL_CHARS] or str(refused.reason)

    return str(error.get("message", error) if isinstance(error, dict) else error)[:ERROR_DETAIL_CHARS]


def _open_request(request: urllib.request.Request,
                  url: str,
                  timeout_s: int) -> bytes:
    """Give me the bytes a server answers one request with; every failure is an `EndpointError`."""

    try:
        with urllib.request.urlopen(url=request, timeout=timeout_s) as response:
            return response.read()
    except urllib.error.HTTPError as refused:
        raise EndpointError(url=url, status=refused.code, detail=_read_refusal(refused=refused)) from refused
    except (urllib.error.URLError, http.client.HTTPException, OSError) as unreachable:
        # nobody answered (refused, reset, cut off, timed out): the error names the URL, and the caller decides what is next
        reason = getattr(unreachable, "reason", unreachable)
        raise EndpointError(url=url, status=None, detail=str(reason) or type(reason).__name__) from unreachable


def _is_dropped_connection(failure: BaseException | None) -> bool:
    """Whether a connection was closed on the program before the server finished answering; a time out is not."""

    reason = getattr(failure, "reason", failure)

    return isinstance(reason, (ConnectionError, http.client.HTTPException))


def _send(endpoint: EndpointSpec,
          path: str,
          body: bytes | None,
          content_type: str) -> bytes:
    """Give me the bytes a server answers one request with; `body` None is a GET. A dropped connection is tried once more."""

    url = _build_url(endpoint=endpoint, path=path)
    header_dict = {}
    if body is not None:
        header_dict["Content-Type"] = content_type
    key = _read_api_key(endpoint=endpoint, url=url)
    if key:
        header_dict["Authorization"] = f"Bearer {key}"
    method = "GET" if body is None else "POST"
    request = urllib.request.Request(url=url, data=body, headers=header_dict, method=method)
    try:
        return _open_request(request=request, url=url, timeout_s=endpoint.timeout_s)
    except EndpointError as failed:
        if not _is_dropped_connection(failure=failed.__cause__):
            raise

    # the one more try after a dropped connection: a server that was restarting or closed an idle line is back
    return _open_request(request=request, url=url, timeout_s=endpoint.timeout_s)


def _parse_json_reply(url: str,
                      reply: bytes) -> dict:
    try:
        parsed = json.loads(reply)
    except json.JSONDecodeError as not_json:
        raise EndpointError(url=url, status=200, detail=f"the reply is not JSON: {reply[:80]!r}") from not_json
    if not isinstance(parsed, dict):
        raise EndpointError(url=url, status=200, detail=f"the reply is not a JSON object: {reply[:80]!r}")

    return parsed


def _read_list_field(url: str,
                     reply: dict,
                     field: str) -> list:
    """Give me the list the reply holds in `field`; the error names the URL and the field when it has none."""

    found = reply.get(field)
    if not isinstance(found, list):
        raise EndpointError(url=url, status=200, detail=f"the reply has no list {field!r}: {str(reply)[:REPLY_SHOWN_CHARS]}")

    return found


def _build_multipart_body(boundary: str,
                          field_list: list[tuple[str, str]],
                          file_dict: dict[str, MultipartFileSpec]) -> bytes:
    """Give me the `multipart/form-data` body that sends the fields and the files under their names."""

    part_list = []
    for name, value in field_list:
        part_list.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    for name, file in file_dict.items():
        file_name = file.file_name.replace('"', "%22")
        part_list.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{file_name}"\r\n'
                          f"Content-Type: {file.media_type}\r\n\r\n").encode("utf-8") + file.content + b"\r\n")
    part_list.append(f"--{boundary}--\r\n".encode("utf-8"))

    return b"".join(part_list)


def _send_json(endpoint: EndpointSpec,
               path: str,
               body: dict) -> dict:
    """Give me the JSON object the server replies to a JSON request to `path` with."""

    url = _build_url(endpoint=endpoint, path=path)
    reply = _send(endpoint=endpoint, path=path, body=json.dumps(body).encode("utf-8"), content_type="application/json")

    return _parse_json_reply(url=url, reply=reply)


def _send_multipart(endpoint: EndpointSpec,
                    path: str,
                    field_list: list[tuple[str, str]],
                    file_dict: dict[str, MultipartFileSpec]) -> dict:
    """Give me the JSON object the server replies to a `multipart/form-data` request to `path` with."""

    url = _build_url(endpoint=endpoint, path=path)
    boundary = uuid.uuid4().hex
    body = _build_multipart_body(boundary=boundary, field_list=field_list, file_dict=file_dict)
    reply = _send(endpoint=endpoint, path=path, body=body, content_type=f"multipart/form-data; boundary={boundary}")

    return _parse_json_reply(url=url, reply=reply)


def _build_model_field(endpoint: EndpointSpec) -> dict[str, str]:
    """Give me the `model` entry of a request body: the server's own name for the model, when the settings give one."""

    return {"model": endpoint.model} if endpoint.model else {}


def find_endpoint(kind: str) -> EndpointSpec | None:
    """Give me the server set for `kind` of model (`chat`, `speech`, `diarization`, `embeddings`, `ocr`, `detection`,
    `decision`); None when its URL is empty, which keeps the program's own way of running that model."""

    providers = config.settings.providers
    if kind not in type(providers).model_fields:
        raise ValueError(f"no kind of model {kind!r}: the kinds are {sorted(type(providers).model_fields)}")
    provider = getattr(providers, kind)
    if not provider.url:
        return None

    return EndpointSpec(base_url=provider.url, api_key_env=provider.api_key_env, timeout_s=provider.timeout_s,
                        model=getattr(provider, "model", ""))


def build_data_url(content: bytes,
                   media_type: str) -> str:
    """Give me `content` as the `data:` URL a request carries an image in."""

    return f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"


def list_served_model_entries(endpoint: EndpointSpec) -> list[dict]:
    """Give me every entry the server lists at `GET /v1/models`, each with at least its 'id'. A server that reports more
    about a model (its loaded window) keeps those fields in the entry."""

    url = _build_url(endpoint=endpoint, path="/models")
    parsed = _parse_json_reply(url=url, reply=_send(endpoint=endpoint, path="/models", body=None, content_type=""))
    item_list = _read_list_field(url=url, reply=parsed, field="data")
    for item in item_list:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise EndpointError(url=url, status=200, detail=f"a listed model has no 'id': {str(item)[:REPLY_SHOWN_CHARS]}")

    return item_list


def list_served_models(endpoint: EndpointSpec) -> list[str]:
    """Give me the names of the models the server lists at `GET /v1/models`."""

    entry_list = list_served_model_entries(endpoint=endpoint)
    name_list = []
    for entry in entry_list:
        name_list.append(entry["id"])

    return name_list


def read_reported_window(endpoint: EndpointSpec,
                         model: str) -> int | None:
    """Give me the context window the server reports for `model` at `GET /v1/models`, or None when the server does not
    list the model or reports no window for it (the standard reply holds none)."""

    entry_list = list_served_model_entries(endpoint=endpoint)
    for entry in entry_list:
        if entry["id"] != model:
            continue
        for field in WINDOW_FIELDS:
            value = entry.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value

    return None


def request_chat_completion(endpoint: EndpointSpec,
                            body: dict) -> dict:
    """Give me the reply of `POST /v1/chat/completions` to `body` (the model, the messages with their text and `image_url`
    parts); the error names the URL when the reply holds no message."""

    url = _build_url(endpoint=endpoint, path="/chat/completions")
    reply = _send_json(endpoint=endpoint, path="/chat/completions", body=body)
    choice_list = _read_list_field(url=url, reply=reply, field="choices")
    if not choice_list or not isinstance(choice_list[0], dict) or not isinstance(choice_list[0].get("message"), dict):
        raise EndpointError(url=url, status=200, detail=f"the reply has no message in 'choices': {str(reply)[:REPLY_SHOWN_CHARS]}")

    return reply


def request_transcription(endpoint: EndpointSpec,
                          audio: MultipartFileSpec,
                          language: str,
                          granularity_list: list[str]) -> dict:
    """Give me the `verbose_json` reply of `POST /v1/audio/transcriptions` for `audio`: its `segments`, and its `words`
    when `granularity_list` asks for them. `language` "" lets the server detect it."""

    url = _build_url(endpoint=endpoint, path="/audio/transcriptions")
    field_list = list(_build_model_field(endpoint=endpoint).items())
    if language:
        field_list.append(("language", language))
    field_list.append(("response_format", "verbose_json"))
    for granularity in granularity_list:
        field_list.append(("timestamp_granularities[]", granularity))
    reply = _send_multipart(endpoint=endpoint, path="/audio/transcriptions", field_list=field_list, file_dict={"file": audio})
    segment_list = _read_list_field(url=url, reply=reply, field="segments")
    for segment in segment_list:
        if not isinstance(segment, dict) or not {"start", "end"} <= set(segment):
            raise EndpointError(url=url, status=200,
                                detail=f"a segment has no 'start' and 'end': {str(segment)[:REPLY_SHOWN_CHARS]}")

    return reply


def request_speaker_turns(endpoint: EndpointSpec,
                          audio: MultipartFileSpec,
                          min_speakers: int | None = None,
                          max_speakers: int | None = None) -> dict:
    """Give me the `diarized_json` reply of `POST /v1/audio/transcriptions` for `audio`: its `segments`, each with a
    `speaker`, `start` and `end`. The bounds on the number of speakers go along as the extra fields `min_speakers` and
    `max_speakers` when given, which a server that does not know them leaves out."""

    url = _build_url(endpoint=endpoint, path="/audio/transcriptions")
    field_list = list(_build_model_field(endpoint=endpoint).items())
    field_list.append(("response_format", "diarized_json"))
    if min_speakers is not None:
        field_list.append(("min_speakers", str(min_speakers)))
    if max_speakers is not None:
        field_list.append(("max_speakers", str(max_speakers)))
    reply = _send_multipart(endpoint=endpoint, path="/audio/transcriptions", field_list=field_list, file_dict={"file": audio})
    segment_list = _read_list_field(url=url, reply=reply, field="segments")
    for segment in segment_list:
        if not isinstance(segment, dict) or not {"speaker", "start", "end"} <= set(segment):
            raise EndpointError(url=url, status=200,
                                detail=f"a segment has no 'speaker', 'start' and 'end': {str(segment)[:REPLY_SHOWN_CHARS]}")

    return reply


def request_embeddings(endpoint: EndpointSpec,
                       data_url_list: list[str]) -> list[list[float]]:
    """Give me one vector per image of `data_url_list`, from `POST /v1/embeddings`, in the order the images were sent."""

    url = _build_url(endpoint=endpoint, path="/embeddings")
    body = {**_build_model_field(endpoint=endpoint), "input": data_url_list}
    reply = _send_json(endpoint=endpoint, path="/embeddings", body=body)
    item_list = _read_list_field(url=url, reply=reply, field="data")
    if len(item_list) != len(data_url_list):
        raise EndpointError(url=url, status=200, detail=f"{len(data_url_list)} images were sent and {len(item_list)} vectors came back")
    vector_list = []
    for item in item_list:
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise EndpointError(url=url, status=200, detail=f"an item has no 'embedding': {str(item)[:REPLY_SHOWN_CHARS]}")
        vector_list.append(item["embedding"])

    return vector_list


def request_text_boxes(endpoint: EndpointSpec,
                       image_url: str,
                       language_list: list[str]) -> list[dict]:
    """Give me the words of one image from `POST /v1/ocr` (a project extension, not a standard route): each with `text`,
    `box`, `line` and `confidence`."""

    url = _build_url(endpoint=endpoint, path="/ocr")
    body = {**_build_model_field(endpoint=endpoint), "image": image_url, "languages": language_list}
    reply = _send_json(endpoint=endpoint, path="/ocr", body=body)
    word_list = _read_list_field(url=url, reply=reply, field="words")
    for word in word_list:
        if not isinstance(word, dict) or not {"text", "box", "line", "confidence"} <= set(word):
            raise EndpointError(url=url, status=200,
                                detail=f"a word has no 'text', 'box', 'line' and 'confidence': {str(word)[:REPLY_SHOWN_CHARS]}")

    return word_list


def request_detections(endpoint: EndpointSpec,
                       image_url: str,
                       kind_list: list[str]) -> list[dict]:
    """Give me what `POST /v1/images/detections` finds in one image (a project extension, not a standard route): each
    detection has a `kind` (`face` or `person`), a `box` and a `score`."""

    url = _build_url(endpoint=endpoint, path="/images/detections")
    body = {**_build_model_field(endpoint=endpoint), "image": image_url, "kinds": kind_list}
    reply = _send_json(endpoint=endpoint, path="/images/detections", body=body)
    detection_list = _read_list_field(url=url, reply=reply, field="detections")
    for detection in detection_list:
        if not isinstance(detection, dict) or not {"kind", "box", "score"} <= set(detection):
            raise EndpointError(url=url, status=200,
                                detail=f"a detection has no 'kind', 'box' and 'score': {str(detection)[:REPLY_SHOWN_CHARS]}")

    return detection_list


def request_decisions(endpoint: EndpointSpec,
                      image_url_list: list[str],
                      question: str) -> list[dict]:
    """Give me one decision per image of `image_url_list` from `POST /v1/classifications` (a project extension, not a
    standard route), in the order the images were sent: each has a `decision` and a `score`."""

    url = _build_url(endpoint=endpoint, path="/classifications")
    body = {**_build_model_field(endpoint=endpoint), "images": image_url_list, "question": question}
    reply = _send_json(endpoint=endpoint, path="/classifications", body=body)
    result_list = _read_list_field(url=url, reply=reply, field="results")
    if len(result_list) != len(image_url_list):
        raise EndpointError(url=url, status=200,
                            detail=f"{len(image_url_list)} images were sent and {len(result_list)} decisions came back")
    for result in result_list:
        if not isinstance(result, dict) or not {"decision", "score"} <= set(result):
            raise EndpointError(url=url, status=200,
                                detail=f"a result has no 'decision' and 'score': {str(result)[:REPLY_SHOWN_CHARS]}")

    return result_list
