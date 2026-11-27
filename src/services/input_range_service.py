"""The part of the input a run processes, chosen by the person: kept in the run folder's
`run-input-range.json`, written before phase 1 by `run --start/--end` or `run --pages` (the run form's Range), and read
by phases 1 and 2, the only phases that read the input. No file means the whole input. Pure file and number work."""

from __future__ import annotations

import json
import re
from pathlib import Path

from models import output_contract
from models.input_range_spec import InputRangeSpec, PartWindowSpec
from utils import json_store_utils

__all__ = ["parse_time", "parse_pages", "build_range", "read_range", "write_range", "list_part_windows",
           "list_page_indexes", "describe_range", "format_time"]

_SECONDS_A_MINUTE = 60                                     # a time's fields after the first count in sixties
_PAGE_SPAN = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")  # "12-40" or "7"


def _build_range_path(folder: Path) -> Path:
    return folder / output_contract.RUN_INPUT_RANGE_FILE


def parse_time(text: str) -> float:
    """A time typed as seconds, minutes:seconds or hours:minutes:seconds, as seconds; refused by name otherwise."""

    fields = text.strip().split(":")
    if not text.strip() or len(fields) > 3:
        raise ValueError(f"{text!r} is no time: write seconds, m:ss or h:mm:ss")
    try:
        numbers = [float(field) for field in fields]
    except ValueError as exc:
        raise ValueError(f"{text!r} is no time: write seconds, m:ss or h:mm:ss") from exc
    if any(number < 0 for number in numbers) or any(number >= _SECONDS_A_MINUTE for number in numbers[1:]):
        raise ValueError(f"{text!r} is no time: minutes and seconds after the first field run from 0 to 59")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * _SECONDS_A_MINUTE + number

    return seconds


def parse_pages(text: str) -> tuple[int, int]:
    """Pages typed as "first-last" or one page, as (first, last) from 1; refused by name otherwise."""

    match = _PAGE_SPAN.match(text)
    if match is None:
        raise ValueError(f"{text!r} is no page range: write first-last, such as 12-40, or one page")
    first = int(match.group(1))
    last = int(match.group(2)) if match.group(2) else first
    if first < 1 or last < first:
        raise ValueError(f"{text!r} is no page range: pages start at 1 and the last comes after the first")

    return first, last


def build_range(start: str,
                end: str,
                pages: str) -> InputRangeSpec | None:
    """The range the run line asks for, checked; None when it asks for none."""

    if pages.strip() and (start.strip() or end.strip()):
        raise ValueError("a range is times for a recording or pages for a PDF, never both")
    if pages.strip():
        first, last = parse_pages(text=pages)

        return InputRangeSpec(first_page=first, last_page=last)
    if not start.strip() and not end.strip():
        return None
    start_s = parse_time(text=start) if start.strip() else None
    end_s = parse_time(text=end) if end.strip() else None
    if end_s is not None and end_s <= (start_s or 0.0):
        raise ValueError(f"the end {end} comes before the start {start or '0'}")

    return InputRangeSpec(start_s=start_s, end_s=end_s)


def read_range(folder: Path) -> InputRangeSpec | None:
    """The range the person chose for the run folder; None when they chose none or the file cannot be read."""

    try:
        data = json.loads(_build_range_path(folder).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("firstPage") is not None:
        return InputRangeSpec(first_page=int(data["firstPage"]), last_page=int(data["lastPage"]))
    if data.get("startS") is None and data.get("endS") is None:
        return None
    start_s = float(data["startS"]) if data.get("startS") is not None else None
    end_s = float(data["endS"]) if data.get("endS") is not None else None

    return InputRangeSpec(start_s=start_s, end_s=end_s)


def write_range(folder: Path,
                input_range: InputRangeSpec) -> None:
    """The run folder's range set to `input_range`, the person's choice."""

    if input_range.is_pages:
        data = {"firstPage": input_range.first_page, "lastPage": input_range.last_page}
    else:
        data = {"startS": input_range.start_s, "endS": input_range.end_s}
    json_store_utils.write_json(_build_range_path(folder), data)


def list_part_windows(durations: list[float],
                      input_range: InputRangeSpec) -> list[PartWindowSpec]:
    """The window of each part of a recording, `durations` its parts' lengths, that the time range crosses, in reading
    order; a part wholly outside it is left out. A range starting past the recording's end is refused by name."""

    total_s = sum(durations)
    start_s = input_range.start_s or 0.0
    end_s = min(input_range.end_s if input_range.end_s is not None else total_s, total_s)
    if start_s >= total_s:
        raise ValueError(f"the range starts at {format_time(seconds=start_s)}, past the recording's end at "
                         f"{format_time(seconds=total_s)}")
    windows, offset_s = [], 0.0
    for index, duration_s in enumerate(durations):
        part_start_s, part_end_s = max(start_s - offset_s, 0.0), min(end_s - offset_s, duration_s)
        if part_end_s > part_start_s:
            windows.append(PartWindowSpec(part_index=index, start_s=part_start_s, end_s=part_end_s,
                                          offset_s=offset_s + part_start_s))
        offset_s += duration_s

    return windows


def list_page_indexes(input_range: InputRangeSpec | None,
                      page_count: int) -> list[int]:
    """The indexes, from 0, of the PDF's pages the range holds: every page with no range, a last page past the PDF's
    end cut to its last page, and a first page past it refused by name."""

    if input_range is None or not input_range.is_pages:
        return list(range(page_count))
    if input_range.first_page > page_count:
        raise ValueError(f"the range starts on page {input_range.first_page}, and the PDF has {page_count} page(s)")

    return list(range(input_range.first_page - 1, min(input_range.last_page, page_count)))


def format_time(seconds: float) -> str:
    """A time as the page shows it: m:ss, or h:mm:ss from an hour."""

    whole = int(round(seconds))
    hours, rest = divmod(whole, _SECONDS_A_MINUTE * _SECONDS_A_MINUTE)
    minutes, secs = divmod(rest, _SECONDS_A_MINUTE)

    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def describe_range(input_range: InputRangeSpec) -> str:
    """The range in the words the page shows: "12:30 to 1:05:00", "1:05 to the end", "pages 12 to 40", "page 7"."""

    if input_range.is_pages:
        if input_range.first_page == input_range.last_page:
            return f"page {input_range.first_page}"

        return f"pages {input_range.first_page} to {input_range.last_page}"
    start = format_time(seconds=input_range.start_s or 0.0)
    end = format_time(seconds=input_range.end_s) if input_range.end_s is not None else "the end"

    return f"{start} to {end}"
