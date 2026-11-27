"""Ask a model server for the faces or the people on one image (`providers.detection.url`, `POST /v1/images/detections`): the
image goes as a PNG no larger than `SENT_MAX_SIDE` pixels on its longer side, and the boxes come back in the image's own
pixels. `face_detection_service` and `person_detection_service` use it in place of their in-process detectors."""

from __future__ import annotations

import cv2
import numpy

from models.endpoint_spec import EndpointSpec
from services import endpoint_client

__all__ = ["SENT_MAX_SIDE", "request_boxes"]

SENT_MAX_SIDE = 1000                                          # the longer side of the picture sent; a larger one is shrunk first

# A box as x0, y0, x1, y1 in the pixels of the image the program holds
Box = tuple[int, int, int, int]


def _shrink_for_sending(image: numpy.ndarray) -> tuple[numpy.ndarray, float]:
    """Give me the image no larger than `SENT_MAX_SIDE` on its longer side, and the share of its size it was shrunk to."""

    scale = min(1.0, SENT_MAX_SIDE / max(image.shape[:2]))
    if scale == 1.0:
        return image, scale

    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), scale


def request_boxes(endpoint: EndpointSpec,
                  image: numpy.ndarray,
                  kind: str,
                  min_score: float) -> list[Box]:
    """Give me the boxes of every `kind` (`face` or `person`) the server finds on `image`, scoring `min_score` or more, as
    x0, y0, x1, y1 in the image's own pixels; a failed request raises the `EndpointError` that names the URL."""

    sent, scale = _shrink_for_sending(image=image)
    is_encoded, png = cv2.imencode(".png", sent)
    if not is_encoded:
        raise ValueError(f"a {sent.shape[1]}x{sent.shape[0]} image could not be encoded as PNG to be sent")
    detection_list = endpoint_client.request_detections(endpoint=endpoint,
                                                        image_url=endpoint_client.build_data_url(content=png.tobytes(),
                                                                                                 media_type="image/png"),
                                                        kind_list=[kind])
    box_list = []
    for detection in detection_list:
        if detection["kind"] != kind or float(detection["score"]) < min_score:
            continue
        left, top, right, bottom = detection["box"]
        box_list.append((round(left / scale), round(top / scale), round(right / scale), round(bottom / scale)))

    return box_list
