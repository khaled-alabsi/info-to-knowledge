"""A frame's web addresses held to what the frame shows and what was said over it (DEFECTS 255).

A batch's model wrote the address in the footer of a talk's slides onto its title card, which has no footer, "at the
bottom right of the slide"; the merge then made it an entry no outline may set aside. An address whose name Apple's
text recognizer does not read on the frame, and that the speech over it never says, is dropped as the notes are
written. Tesseract read such a 720-pixel footer on one frame of six that show it, so with no Apple recognizer nothing is
dropped. An address the recording is too small to show readably goes too: a 360-line recording's address bar held
pythontutor.com, which no reader read and nobody said.
"""

from __future__ import annotations

import logging
import re

import cv2

import config as app_config
from models import output_contract
from models.run_paths import RunPaths
from services import apple_text_service
from services import phase_file_service

__all__ = ["drop_unseen_web_addresses"]

_LOG = logging.getLogger(app_config.PLAYBOOK_NAME)
_SCHEME = re.compile(r"^\s*(?:[a-z][a-z0-9+.-]*://)?(?:www\.)?", re.IGNORECASE)
_NOT_LETTER_OR_DIGIT = re.compile(r"[^a-z0-9]")
# "mariadb dot org", as a transcript writes a spoken address
_SPOKEN_DOT = re.compile(r"\s+dot\s+", re.IGNORECASE)
# A frame narrower than this is read this many times as large
_LEAST_READ_WIDTH = 1600
_SMALL_FRAME_SCALE = 2


def _keep_letters_and_digits(text: str) -> str:
    return _NOT_LETTER_OR_DIGIT.sub("", text.casefold())


def _read_host(address: str) -> str:
    """The address's host with no scheme and no "www.", squeezed to its letters and digits ("mariadborg")."""

    return _keep_letters_and_digits(re.split(r"[/?#]", _SCHEME.sub("", address), maxsplit=1)[0])


def drop_unseen_web_addresses(paths: RunPaths,
                              slug: str,
                              record: dict) -> dict:
    """The record without each web address whose host neither the frame, read by Apple's text recognizer, nor the
    speech over it holds; unchanged with no address, no recognizer or no frame to read."""

    entries = record.get("urls") or []
    if not entries or not apple_text_service.is_available():
        return record
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    items = phase_file_service.read_phase_file(paths, frames_number).get("items", []) \
        if phase_file_service.has_phase_file(paths, frames_number) else []
    item = next((entry for entry in items if entry.get("slug") == slug), None)
    frame_path = paths.output_root / str((item or {}).get("path") or "")
    image = cv2.imread(str(frame_path)) if item and item.get("path") and frame_path.is_file() else None
    if image is None:
        return record
    # A frame that could not be read keeps every address: a timed-out reading dropped one a frame's code showed
    # (Lecture 7 frame 8, DEFECTS 553)
    words = apple_text_service.try_find_words_in_image(image)
    if words is None:
        return record
    read = _keep_letters_and_digits(" ".join(word["text"] for word in words))
    spoken = _keep_letters_and_digits(_SPOKEN_DOT.sub(".", f'{item.get("transcriptBefore") or ""} {item.get("transcriptCurrent") or ""}'))
    hosts = [_read_host(str(entry.get("url") if isinstance(entry, dict) else entry)) for entry in entries]
    # A small frame not showing every address is read again twice as large, and both readings count: at 1,280 pixels an
    # address bar's "regex101.com" came back "regexrut.com", while enlarged a 720-pixel footer's address was lost
    if image.shape[1] < _LEAST_READ_WIDTH and any(host and host not in read and host not in spoken for host in hosts):
        larger = cv2.resize(image, None, fx=_SMALL_FRAME_SCALE, fy=_SMALL_FRAME_SCALE, interpolation=cv2.INTER_CUBIC)
        larger_words = apple_text_service.try_find_words_in_image(larger)
        if larger_words is None:
            return record
        read += " " + _keep_letters_and_digits(" ".join(word["text"] for word in larger_words))
    kept = []
    for entry, host in zip(entries, hosts):
        address = str(entry.get("url") if isinstance(entry, dict) else entry)
        # The site's name alone, read or spoken, does not count: the talk's title card read "MariaDB" and the speech
        # said it all along, and the address was still not on the card
        if not host or host in read or host in spoken:
            kept.append(entry)
            continue
        _LOG.info(f"  {slug}: web address {address} dropped: neither the frame nor the speech over it shows it")
    if len(kept) == len(entries):
        return record

    return {**record, "urls": kept}
