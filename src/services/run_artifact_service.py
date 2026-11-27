"""The files of a run folder a person looks at, grouped for the UI as `list_artifacts` lists them: the document, the
draft and the requests, the notes, the frame notes and critiques, an audio recording's or a text-only PDF's paragraphs
and prepared notes, the cuts phase 4 made for its figures, the frames, the frames phase 2's filters or a person
dropped, and the audio. Program state (every `.json`, the call logs) is never listed and never served. The recording and its transcript are
served here as well, for the Recording tab.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import config
from models import output_contract
from models.run_paths import RunPaths
from models.ui_errors import UnknownArtifactError
from services import document_file_service
from services import document_folder_service
from services import document_pass_service
from services import frame_extraction_service
from services import inbox_move_service
from services import critique_collection_service
from services import knowledge_filter_service
from services import profile_version_service
from services import recording_parts_service
from services import run_recording_service
from services import run_reset_service
from utils import clock_utils

__all__ = ["ARTIFACT_SUFFIXES", "list_artifacts", "list_dropped_frames", "resolve_artifact", "list_linked_files", "build_document_zip",
           "find_recording", "build_files_zip", "build_versions_zip", "build_versions_download", "name_document_version",
           "describe_recording_parts", "resolve_recording_part",
           "read_transcript_lines", "render_transcript_text"]

_LOG = logging.getLogger(__name__)
# What the UI shows or plays. A file with any other suffix is never served, whatever its path.
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
ARTIFACT_SUFFIXES = (".md", *_IMAGE_SUFFIXES, ".wav", ".mp3", ".m4a")
_FRAMES_DIR = output_contract.FRAME_IMAGES_DIR_NAME
_DROPPED_FRAMES_DIR = f"{_FRAMES_DIR}/{knowledge_filter_service.DROPPED_DIR_NAME}"
_DRAFT_FILE = f"{output_contract.WRITING_DIR_NAME}/draft.md"
# Every request phase 4 sent its writer, whole: a fast-processing run's one call, the quality conversation's follow-ups
_REQUESTS_DIR = f"{output_contract.WRITING_DIR_NAME}/{document_file_service.REQUESTS_DIR_NAME}"
# The regions phase 4's writer cut from the recording for its figures (the `cut` tool), one image each.
_CUTS_DIR = f"{output_contract.IMAGE_WORK_DIR_NAME}/cuts"
# The targets a markdown document points at: `![alt](target "title")`, `[text](target)` and `<img src="target">`.
_MARKDOWN_TARGET = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HTML_SOURCE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
# A web address, an absolute path or an anchor: no file of the run
_OUTSIDE_TARGET = re.compile(r"^([a-z]+:|/|#)", re.IGNORECASE)


def _describe_files(folder: Path,
                    paths: list[Path],
                    suffixes: tuple[str, ...] = ARTIFACT_SUFFIXES) -> list[dict]:
    return [{"path": path.relative_to(folder).as_posix(), "name": path.name, "size_bytes": path.stat().st_size}
            for path in paths if path.is_file() and path.suffix.lower() in suffixes]


def _read_json_file(path: Path) -> object:
    """The file's JSON, or None when it is missing or unreadable."""

    # The page still lists the run's files while a phase is writing this one, so an unreadable file reads as empty
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, json.JSONDecodeError) as error:
        _LOG.warning("%s could not be read, listed as empty: %s", path.name, error)
        return None


def _read_json_object(path: Path) -> dict:
    value = _read_json_file(path)

    return value if isinstance(value, dict) else {}


def _read_json_list(path: Path) -> list:
    value = _read_json_file(path)

    return value if isinstance(value, list) else []


def _read_frame_entries(folder: Path,
                        frames_file: dict) -> dict[str, dict]:
    """Each frame image's entry in phase 2's frame list `frames_file` (`02_frames.json` as read), kept or dropped by a
    filter, by its path in the run folder (`frames/frame-005.jpg`, `frames/dropped/frame-012.jpg`); while phase 2 runs,
    the dropped frames' entries its filters wrote beside their images, so the page names the layer before
    `02_frames.json` exists."""

    # `02_frames.json` comes last, so its entry wins over the list the filters wrote before it
    entries = [*_read_json_list(folder / _DROPPED_FRAMES_DIR / knowledge_filter_service.DROPPED_LIST_NAME),
               *frames_file.get("items", []), *frames_file.get("droppedFrames", [])]

    return {entry["path"]: entry for entry in entries if isinstance(entry, dict) and entry.get("path")}


def list_dropped_frames(folder: Path) -> list[dict]:
    """Every frame whose image is in `frames/dropped/`, as its entry (`slug`, `path`, `droppedBy`, and the redundancy
    filter's `coveredBy` and `reason`), in frame order: read from `02_frames.json`, or while phase 2 runs from the list
    its filters write as they drop."""

    entries = _read_frame_entries(folder, _read_json_object(folder / output_contract.LOOP_ITEMS_FILE))

    return sorted((entry for path, entry in entries.items()
                   if path.startswith(f"{_DROPPED_FRAMES_DIR}/") and (folder / path).is_file()),
                  key=lambda entry: entry["path"])


def find_recording(folder: Path,
                   video: str) -> Path | None:
    """The recording a run record names, while it is still there, in `inbox/.archive/`, or kept in a done run's folder
    (`recording/`, this folder's or another run folder's of the same video): a video file, or a folder of a meeting's
    parts that still holds a video (a recording moved to the trash is not)."""

    path = Path(video) if video else None
    if path is not None and not path.exists():
        path = (inbox_move_service.find_moved_video(config.INBOX_DIR, inbox_move_service.ARCHIVE, video)
                or run_recording_service.find_stored_recording(folder)
                or run_recording_service.find_recording_by_origin(video, [folder.parent]))
    if path is None:
        return None

    return path if path.is_file() or recording_parts_service.list_recording_parts(path) else None


# A PDF's frames are its pages, or read as text only its figures, and its paragraphs are no speech (the owner: "what
# is frames in pdf?")
_PDF_TEXT_ONLY_TITLES = {"speech": "Paragraphs", "frames": "Figures", "processed": "Figure notes"}
_PDF_PAGE_TITLES = {"frames": "Pages", "processed": "Page notes"}


def _select_group_titles(medium: str) -> dict[str, str]:
    """The group titles a run of `medium` (phase 2's frame list's) shows in place of a video's, by group key."""

    if medium in output_contract.PDF_TEXT_ONLY_MEDIUMS:
        return _PDF_TEXT_ONLY_TITLES
    if medium == output_contract.PDF_MEDIUM:
        return _PDF_PAGE_TITLES

    return {}


def _add_frame_fields(item: dict,
                      entry: dict) -> None:
    """`item`, an image of a group, given in place what its frame's entry says: its second in the recording, or a
    PDF's page, the frame classifier's drop score and tag (people, text or other) when it ran, and for a dropped frame
    the layer that dropped it, and for the redundancy filter the frames said to hold it and the model's reason."""

    timestamp = entry.get("timestamp")
    item["time_s"] = float(timestamp) if isinstance(timestamp, (int, float)) else None
    # A PDF's page or figure is shown by its page, which a reader can find, never by a made-up time
    if isinstance(entry.get("page"), int):
        item["page"] = entry["page"]
    if isinstance(entry.get("dropScore"), (int, float)):
        item["drop_score"] = float(entry["dropScore"])
    if isinstance(entry.get("tag"), str):
        item["tag"] = entry["tag"]
    if isinstance(entry.get("droppedBy"), str):
        item["dropped_by"] = entry["droppedBy"]
    # The kind of empty frame the decision model named (`decision_filter_service`): people, desktop, blank or joining
    if entry.get("dropClass"):
        item["drop_class"] = str(entry["dropClass"])
    if entry.get("coveredBy"):
        item["covered_by"] = [str(slug) for slug in entry["coveredBy"]]
    if entry.get("reason"):
        item["drop_reason"] = str(entry["reason"])


def list_artifacts(folder: Path) -> list[dict]:
    """The groups that hold at least one file: the markdown groups first, what the run produced last first, then the
    images. Each image
    carries what its frame's entry says (`_add_frame_fields`): its second or a PDF's page, the classifier's score and
    tag, and for a dropped frame who dropped it and why. The recording is no artifact: the UI shows it in a tab of its
    own."""

    published = [published_dir / f"{published_dir.name}.md"
                 for published_dir in run_reset_service.list_published_folders(folder)]
    # The published document is the final one; phase 4's own copy is the same text without its header, so it is shown
    # only until phase 4 publishes
    # Every version the Open document menu lists opens in the reader, which shows only files these groups hold: the
    # original and each pass's drafts live in phase 7's folder, and were listed in the menu yet opened nothing
    version_rows = profile_version_service.list_document_versions(folder, published[0] if published else None)
    versions = [folder / version["path"] for version in version_rows]
    # every part's draft, the last one of a finished pass too, which the menu leaves out as the result but the part's
    # row in the table of parts opens
    part_drafts = sorted(document_pass_service.build_part_draft_path(folder, 1).parent.glob("part-*.md"))
    documents = list(dict.fromkeys([*(published or [folder / output_contract.SUMMARY_FILE]), *versions, *part_drafts,
                                    folder / output_contract.PHASE_FILES[output_contract.PHASE_NUMBERS["review"]]["file"]]))
    # A batch's critique and phase 6's rule set are shown as what they are - what the run says about its own work -
    # and so are kept out of the frame notes, where they would read as a frame's notes (the owner).
    critiques = sorted((folder / output_contract.ITEMS_DIR_NAME).glob(f"*{critique_collection_service.CRITIQUE_SUFFIX}"))
    # The rule set under its name now, or the retired one of a run made before phase 6 was renamed
    rule_set = critique_collection_service.find_written_rule_set(
        RunPaths(repository=folder, module=folder.name, output_root_override=folder))
    rule_sets = [rule_set] if rule_set else []
    processed = [path for path in sorted((folder / output_contract.ITEMS_DIR_NAME).glob("*.md")) if path not in critiques]
    groups = [
        ("document", "Document", "markdown", documents),
        ("draft", "Draft", "markdown", [folder / _DRAFT_FILE]),
        # a running part's thinking so far, which the part's row opens (the owner could not tell it from a stuck one); the
        # page shows this group as Worker turns, every worker's session with its turns and tool calls
        ("thinking", "Worker turns", "markdown",
         sorted(document_pass_service.build_part_thinking_path(folder, 1).parent.glob("part-*.md"))),
        ("requests", "Requests", "markdown", sorted((folder / _REQUESTS_DIR).glob("*.md"))),
        ("notes", "Notes", "markdown", [folder / output_contract.NOTES_FILE, folder / output_contract.WORKER_FEEDBACK_FILE]),
        # A fast-processing run's phase 3: each frame's description with what was said while it was on screen
        ("descriptions", "Frame descriptions", "markdown", [folder / output_contract.FRAME_DESCRIPTIONS_FILE]),
        ("critique", "Critique", "markdown",
         [*rule_sets, *critiques]),
        ("processed", "Frame notes", "markdown", processed),
        # An audio recording's or a text-only PDF's phase 2 and 3 outputs: its paragraphs, and the notes its profile
        # prepared
        ("speech", "Speech paragraphs", "markdown", [folder / output_contract.SPEECH_FILE]),
        ("prepared", "Prepared notes", "markdown", sorted((folder / output_contract.PREPARED_DIR).rglob("*.md"))),
        ("cuts", "Cuts", "images", sorted([*(folder / _CUTS_DIR).glob("*.png"), *(folder / _CUTS_DIR).glob("*.jpg")])),
        ("frames", "Frames", "images", sorted((folder / _FRAMES_DIR).glob("*.jpg"))),
        ("dropped", "Dropped frames", "images", sorted((folder / _DROPPED_FRAMES_DIR).glob("*.jpg"))),
        ("audio", "Audio and transcript", "audio", [folder / output_contract.AUDIO_FILE]),
        # A PDF's phase 1 text, which has no audio to sit under: the PDF's tab shows the PDF alone (the owner: "the text
        # tab is artifact")
        ("text", "Text", "text",
         [] if (folder / output_contract.AUDIO_FILE).is_file() else [folder / output_contract.TRANSCRIPT_FILE]),
    ]
    frames_file = _read_json_object(folder / output_contract.LOOP_ITEMS_FILE)
    titles = _select_group_titles(str(frames_file.get("medium") or ""))
    # the text group's file is the page's sign that a text exists, which it reads through the transcript route and
    # never as a served file
    listed = [{"key": key, "title": titles.get(key, title), "kind": kind,
               "items": _describe_files(folder, paths, (".json",) if kind == "text" else ARTIFACT_SUFFIXES)}
              for key, title, kind, paths in groups]
    # Each version of the document carries the Open document menu's name for it ("original", "knowledge draft after
    # part 1 of 5"), so the reader's list says what each file is, not only its path
    version_labels = {str(folder / version["path"]): version["label"] for version in version_rows}
    for item in (item for group in listed if group["key"] == "document" for item in group["items"]):
        if str(folder / item["path"]) in version_labels:
            item["label"] = version_labels[str(folder / item["path"])]
    frame_entries = _read_frame_entries(folder, frames_file)
    for item in (item for group in listed if group["kind"] == "images" for item in group["items"]):
        _add_frame_fields(item, frame_entries.get(item["path"], {}))

    return [group for group in listed if group["items"]]


def resolve_artifact(folder: Path,
                     relative: str) -> Path:
    """The file `relative` names inside the run folder; refused when it leaves the folder, is missing, or is no
    artifact (a `.json`, a log)."""

    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise UnknownArtifactError(f"no file {relative} in {folder.name}")
    if path.suffix.lower() not in ARTIFACT_SUFFIXES:
        raise UnknownArtifactError(f"{relative} is not shown: the UI serves " + ", ".join(ARTIFACT_SUFFIXES))

    return path


def list_linked_files(folder: Path,
                      document: Path,
                      link_base: Path | None = None) -> list[Path]:
    """The artifacts inside the run folder that a markdown document links to by a relative path (its figures), each
    once, in the order the document names them. A web address, an anchor or a missing file is not one. A link is read
    from `link_base`, else from the document's own folder: an earlier version kept in phase 7's folder links the
    figures as the published document does."""

    text = document.read_text(encoding="utf-8", errors="replace")
    targets = _MARKDOWN_TARGET.findall(text) + _HTML_SOURCE.findall(text)
    base = link_base or document.parent
    linked = []
    for target in targets:
        if _OUTSIDE_TARGET.match(target):
            continue
        target_path = (base / unquote(target.split("#")[0])).resolve()
        # A relative link leaving the run folder, a missing figure or a file the UI does not serve is left out of the
        # zip, which the console says
        try:
            path = resolve_artifact(folder, target_path.relative_to(folder.resolve()).as_posix())
        except (UnknownArtifactError, ValueError) as error:
            _LOG.warning("%s links %s, left out of the zip: %s", document.name, target, error)
            continue
        if path != document and path not in linked:
            linked.append(path)

    return linked


def _select_compression(path: Path) -> int:
    """How a file is stored in a zip: an image is compressed already, so it is stored as it is."""

    return zipfile.ZIP_STORED if path.suffix.lower() in _IMAGE_SUFFIXES else zipfile.ZIP_DEFLATED


def build_document_zip(document: Path,
                       linked: list[Path]) -> bytes:
    """A zip of the document and the files it links, at the paths the document uses, so it opens with its figures."""

    # Every path is kept relative to the folder that holds the document and all it links: a figure linked as
    # `../images/cut-001.png` was stored by its name alone, beside the document, where the link did not reach it
    root = Path(os.path.commonpath([document.parent, *(path.parent for path in linked)]))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(document, document.relative_to(root).as_posix())
        for path in linked:
            archive.write(path, path.relative_to(root).as_posix(), compress_type=_select_compression(path))

    return buffer.getvalue()


def name_document_version(document: Path,
                          number: int,
                          label: str) -> str:
    """A version's file name: the published document's name, its number and its label (`talk-2-after-clean-up.md`)."""

    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")

    return f"{document.stem}-{number}-{slug}.md"


def build_versions_zip(folder: Path,
                       document: Path,
                       versions: list[dict]) -> bytes:
    """A zip of the chosen versions of the published document, each under `name_document_version`, beside the figures
    any of them links, at the paths the published folder keeps them, so every version opens with its figures."""

    buffer = io.BytesIO()
    figures: list[Path] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for version in versions:
            path = resolve_artifact(folder, version["path"])
            archive.write(path, name_document_version(document=document, number=version["number"], label=version["label"]))
            linked = list_linked_files(folder=folder, document=path, link_base=document.parent)
            new_figures = [figure for figure in linked if figure not in figures]
            figures.extend(new_figures)
        for figure in figures:
            # a figure outside the published folder is stored by its name; the document's links reach only its folder
            arc_name = (figure.relative_to(document.parent.resolve()).as_posix()
                        if figure.is_relative_to(document.parent.resolve()) else figure.name)
            archive.write(figure, arc_name, compress_type=_select_compression(figure))

    return buffer.getvalue()


def build_versions_download(folder: Path,
                            number: int | None) -> tuple[bytes, str]:
    """Version `number` of the run's published document, or every version when None, as a zip with the figures, and
    the zip's file name; refused by name when the run has no such version."""

    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    if not paths.summary.is_file():
        raise UnknownArtifactError(f"{folder.name} has no published document")
    document = document_folder_service.build_document_path(paths)
    versions = profile_version_service.list_document_versions(folder, document)
    chosen = [version for version in versions if number is None or version["number"] == number]
    if not chosen:
        raise UnknownArtifactError(f"{folder.name} has no document version {number}" if number else
                                   f"{folder.name} has no earlier version of its document")
    version_name = name_document_version(document=document, number=chosen[0]["number"], label=chosen[0]["label"])
    zip_name = f"{document.stem}-versions.zip" if number is None else Path(version_name).with_suffix(".zip").name

    return build_versions_zip(folder=folder, document=document, versions=chosen), zip_name


def build_files_zip(folder: Path,
                    relatives: list[str]) -> bytes:
    """A zip of chosen artifacts of the run folder, each at its path in the folder; one that is no artifact is refused
    by `resolve_artifact`. Images are stored as they are: a JPEG or PNG does not shrink."""

    paths = [resolve_artifact(folder, relative) for relative in relatives]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in paths:
            archive.write(path, path.relative_to(folder.resolve()).as_posix(), compress_type=_select_compression(path))

    return buffer.getvalue()


@lru_cache(maxsize=256)
def _read_part_duration_s(path: str,
                          modified_ns: int,
                          size: int) -> float:
    """One part's length, read once per version of the file (its path, time and size are the key)."""

    duration_s = frame_extraction_service.read_duration_s(Path(path))
    if duration_s is None:
        # Cached with the others: said once per version of the file. Every later part's start second counts from 0.0
        _LOG.warning("%s: its length could not be read, so the parts after it start earlier than they do", path)
        return 0.0

    return duration_s


def describe_recording_parts(recording: Path) -> list[dict]:
    """The video files a recording is made of, in the order the pipeline reads them, each with where it starts in the
    meeting's time: a frame's second in the recording is in the last part whose `start_s` is not after it."""

    parts = []
    start_s = 0.0
    for path in recording_parts_service.list_recording_parts(recording):
        stat = path.stat()
        duration_s = _read_part_duration_s(str(path), stat.st_mtime_ns, stat.st_size)
        parts.append({"name": path.name, "size_bytes": stat.st_size, "start_s": round(start_s, 1),
                      "duration_s": duration_s})
        start_s += duration_s

    return parts


def resolve_recording_part(recording: Path,
                           index: int) -> Path:
    """Part `index` (from 0) of a recording; refused by name when there is no such part."""

    parts = recording_parts_service.list_recording_parts(recording)
    if not 0 <= index < len(parts):
        raise UnknownArtifactError(f"{recording.name} has no part {index + 1}; it has {len(parts)}")

    return parts[index]


def read_transcript_lines(folder: Path) -> list[dict]:
    """Phase 1's transcript as the Recording tab lists it: each line's first and last second of the meeting, its text
    and its speaker (None when diarization placed none); no lines before phase 1 wrote its file."""

    segments = _read_json_object(folder / output_contract.TRANSCRIPT_FILE).get("segments", [])

    return [{"start_s": float(segment["start"]), "end_s": float(segment["end"]), "text": str(segment["text"]).strip(),
             "speaker": segment.get("speaker")} for segment in segments]


def _render_full_clock(seconds: float) -> str:
    minutes, second = divmod(int(seconds), 60)
    hours, minute = divmod(minutes, 60)

    return f"{hours:02d}:{minute:02d}:{second:02d}"


def _render_speaker_turns(lines: list[dict]) -> str:
    """One block per speaker's turn: `[hh:mm:ss - hh:mm:ss] Speaker A:`, its words on the next line, a blank line
    after; lines one after another of one speaker are one turn, and a line with no speaker is `Speaker ?`."""

    turns: list[dict] = []
    for line in lines:
        speaker = line["speaker"] or "Speaker ?"
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1] = {**turns[-1], "end_s": line["end_s"], "text": f"{turns[-1]['text']} {line['text']}"}
        else:
            turns.append({**line, "speaker": speaker})

    return "\n".join(f"[{_render_full_clock(turn['start_s'])} - {_render_full_clock(turn['end_s'])}] {turn['speaker']}:\n"
                     f"{turn['text']}\n" for turn in turns)


def render_transcript_text(folder: Path) -> str:
    """The transcript as the page downloads it: one line per thing said, its time in front (`[mm:ss] text`, or
    `[h:mm:ss] text` past an hour); refused by name before phase 1 wrote it. With speakers, one block per speaker's
    turn: `[hh:mm:ss - hh:mm:ss] Speaker A:` and its words below."""

    lines = read_transcript_lines(folder)
    if not lines:
        raise UnknownArtifactError(f"{folder.name} has no transcript yet: phase 1 writes it")

    if any(line["speaker"] for line in lines):
        return _render_speaker_turns(lines)

    return "".join(f"[{clock_utils.render_clock(line['start_s'])}] {line['text']}\n" for line in lines)
