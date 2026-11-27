"""A frame's open question claiming a typo on the screen held to what the frame shows (DEFECTS 348).

The model misread a slide's "TUIs" as "TULs" and asked whether the slide's typo was meant; the repair corrected the
outline, and the question stayed. Over the public runs 9 open questions claimed a typo on the screen: Apple's text
recognizer read the claimed misspelling on the 5 true ones and on none of the 4 misreads ("x/q", "ina", "S minutes",
"TULs"). A question whose misspelling the recognizer reads nowhere on the frame, at its size or twice it, is dropped
as the notes are written. A question about the speech (a word misheard) is no screen claim and stays, and with no
recognizer nothing is dropped.

A fact or a take-away claiming a typo is held the same way (DEFECTS 417): release test 2's frame 4 stated as a fact
that its footer reads "vorbebaten", where the footer reads "vorbehalten". There the claim must be one word, since a
take-away opens with a quote of the speech: over the public runs' facts and take-aways the recognizer read the 3 true
misspellings ("beem", "Schatplan" twice) and not the misread, and only a take-away quoting the speech ("checks for
what the user typed in") was a longer claim.
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

__all__ = ["drop_unread_typo_claims", "read_claimed_misspelling"]

_LOG = logging.getLogger(app_config.PLAYBOOK_NAME)
# A question claiming the screen holds a wrong spelling
_TYPO_CLAIM = re.compile(r"\b(?:typos?|misspell\w*|misspelt)\b", re.IGNORECASE)
# A question about what was said, not shown
_SPEECH_CLAIM = re.compile(r"\b(?:spoken|transcri\w*|said|heard|speaker|speech|pronounc\w*)\b", re.IGNORECASE)
_QUOTE = re.compile(r"[\"“]([^\"”]+)[\"”]")
# "misspells "beam" as "beem"": the misspelling stands after "as"
_AS_QUOTE = re.compile(r"\bas\s+[\"“]([^\"”]+)[\"”]", re.IGNORECASE)
_TOKEN_EDGE = re.compile(r"^\W+|\W+$")
_LEAST_READ_WIDTH = 1600
_SMALL_FRAME_SCALE = 2
# The fields a claim is held in, each with whether its claim must be one word
_CLAIM_FIELDS = {"open_questions": False, "facts": True, "take_aways": True}


def read_claimed_misspelling(question: str) -> str:
    """The text a question says the screen misspells: the one quoted after "as" ("misspells "beam" as "beem""), else
    the first quoted ("a typo "TULs" instead of "TUIs""); empty when the question claims no typo on the screen."""

    if not _TYPO_CLAIM.search(question) or _SPEECH_CLAIM.search(question):
        return ""
    after_as = _AS_QUOTE.search(question)
    quoted = _QUOTE.findall(question)

    return (after_as.group(1) if after_as else quoted[0] if quoted else "").strip()


def _list_tokens(text: str) -> list[str]:
    return [token for token in (_TOKEN_EDGE.sub("", word).casefold() for word in text.split()) if token]


def _is_read(text: str,
             read_tokens: list[str]) -> bool:
    """Whether the tokens of `text` stand in a row among `read_tokens`."""

    tokens = _list_tokens(text)

    return bool(tokens) and any(read_tokens[start:start + len(tokens)] == tokens
                                for start in range(len(read_tokens) - len(tokens) + 1))


def _read_field_claim(line: str,
                      is_one_word: bool) -> str:
    claim = read_claimed_misspelling(line)

    return "" if is_one_word and len(_list_tokens(claim)) != 1 else claim


def drop_unread_typo_claims(paths: RunPaths,
                            slug: str,
                            record: dict) -> dict:
    """The record without each open question, fact or take-away claiming a typo on the screen whose misspelling
    Apple's text recognizer reads nowhere on the frame; the facts and the take-aways are never emptied. Unchanged with no such claim, no
    recognizer or no frame to read."""

    claims = {field: [_read_field_claim(str(line), is_one_word) for line in record.get(field) or []]
              for field, is_one_word in _CLAIM_FIELDS.items()}
    every_claim = [claim for field_claims in claims.values() for claim in field_claims if claim]
    if not every_claim or not apple_text_service.is_available():
        return record
    frames_number = output_contract.PHASE_NUMBERS["frames_preparing"]
    items = phase_file_service.read_phase_file(paths, frames_number).get("items", []) \
        if phase_file_service.has_phase_file(paths, frames_number) else []
    item = next((entry for entry in items if entry.get("slug") == slug), None)
    frame_path = paths.output_root / str((item or {}).get("path") or "")
    image = cv2.imread(str(frame_path)) if item and item.get("path") and frame_path.is_file() else None
    if image is None:
        return record
    # A frame that could not be read keeps every claim: a timed-out reading is no reading of the word (DEFECTS 553)
    words = apple_text_service.try_find_words_in_image(image)
    if words is None:
        return record
    read = _list_tokens(" ".join(word["text"] for word in words))
    if image.shape[1] < _LEAST_READ_WIDTH and any(not _is_read(claim, read) for claim in every_claim):
        larger = cv2.resize(image, None, fx=_SMALL_FRAME_SCALE, fy=_SMALL_FRAME_SCALE, interpolation=cv2.INTER_CUBIC)
        larger_words = apple_text_service.try_find_words_in_image(larger)
        if larger_words is None:
            return record
        read += [""] + _list_tokens(" ".join(word["text"] for word in larger_words))
    changed = {}
    for field, field_claims in claims.items():
        lines = record.get(field) or []
        kept = [line for line, claim in zip(lines, field_claims) if not claim or _is_read(claim, read)]
        if len(kept) == len(lines) or (not kept and field != "open_questions"):
            continue
        for claim in field_claims:
            if claim and not _is_read(claim, read):
                _LOG.info(f"  {slug}: {field.replace('_', ' ')} line dropped: the frame does not show the misspelling \"{claim}\" it claims")
        changed[field] = kept

    return {**record, **changed} if changed else record


