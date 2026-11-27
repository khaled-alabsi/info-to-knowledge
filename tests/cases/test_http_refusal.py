"""The text of a refusal the `i2k serve` API answers with is read the same way by every client of it."""

import httpx

from support import harness

from utils import http_refusal_utils


def test_an_object_body_gives_its_detail_and_any_other_body_gives_its_text():
    refused = httpx.Response(409, json={"detail": "a run is writing into this folder"})
    from_a_proxy = httpx.Response(502, text="Bad gateway")
    a_list = httpx.Response(422, json=[{"msg": "field required"}])
    harness.check("an object body gives the status and its detail",
                  http_refusal_utils.read_refusal_text(refused) == "409: a run is writing into this folder")
    harness.check("a body that is no JSON gives its text as it came",
                  http_refusal_utils.read_refusal_text(from_a_proxy) == "502: Bad gateway")
    harness.check("a body that is a list gives its text as it came",
                  http_refusal_utils.read_refusal_text(a_list).startswith("422: [{"), http_refusal_utils.read_refusal_text(a_list))
