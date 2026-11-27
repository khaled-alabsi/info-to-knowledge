"""The tags the owner gives a run folder, kept in its `run-tags.json`. Pure file work. Phase 2 reads them to pick the
run's ingesting profile, and phases 4 and 5 read them as a hint of what the recording is; a
reset or a resume leaves them as they are. The detail view and `i2k tags` change them through
`run_operations_service.set_run_tags`, and `i2k run --tag` adds to them before phase 1."""

from __future__ import annotations

import json
import re
from pathlib import Path

import config
from models import output_contract
from models.ui_errors import UnreadableRunTagsError
from utils import json_store_utils
from services import artifact_edit_service
from services import inbox_service

# Naming rules that name a client: beside the settings files, never in git
LOCAL_RULES_FILE = config.DEFAULT_CONFIG_FILE.parent / "tag-rules.local.json"

__all__ = ["LOCAL_RULES_FILE", "clean_tags", "read_tags", "write_tags", "apply_tag_changes", "list_rule_tags",
           "describe_format_tag", "describe_medium_format_tag", "is_format_tag",
           "list_tag_inventory", "match_inventory_tags", "is_program_tag",
           "find_published_document"]


def clean_tags(tags: list[str]) -> list[str]:
    """Each tag trimmed and its spaces collapsed, a comma splitting one into two, empty ones dropped, and a tag written
    again in other letter case kept once, as first written."""

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        for part in str(raw).split(","):
            tag = " ".join(part.split())
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                cleaned.append(tag)

    return cleaned


def _read_stored_tags(folder: Path) -> list[str]:
    """The tags the file holds; none when there is no file yet. A file that is there and does not read, or holds no
    list of tags, raises `UnreadableRunTagsError`."""

    path = folder / output_contract.RUN_TAGS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise UnreadableRunTagsError(f"{path} does not read ({error}): repair or remove it by hand") from error
    if not isinstance(data, dict) or not isinstance(data.get("tags", []), list):
        raise UnreadableRunTagsError(f"{path} holds no list of tags: repair or remove it by hand")

    return clean_tags(data.get("tags") or [])


def read_tags(folder: Path) -> list[str]:
    """The run folder's tags in the order they were given; none when it has no tags file or one that cannot be read
    (the views show no tags; a change of them is refused, `apply_tag_changes`). A published document whose metadata
    block was changed after the tags file was written gives the tags, and the file is written from it: the two are kept
    in step both ways."""

    try:
        stored = _read_stored_tags(folder)
    except UnreadableRunTagsError:
        return []
    document = find_published_document(folder)
    tags_file = folder / output_contract.RUN_TAGS_FILE
    if document is None or (tags_file.is_file() and document.stat().st_mtime <= tags_file.stat().st_mtime):
        return stored
    edited = _read_document_tags(document)
    if edited is None or edited == stored:
        return stored

    return _write_tags_file(folder, edited)


def write_tags(folder: Path,
               tags: list[str]) -> list[str]:
    """The run folder's tags set to `tags`, cleaned; the file is removed when none is left."""

    cleaned = _write_tags_file(folder, tags)
    _write_document_tags(folder, cleaned)

    return cleaned


def _write_tags_file(folder: Path,
                     tags: list[str]) -> list[str]:
    """The tags file alone set to `tags`, cleaned, removed when none is left."""

    cleaned = clean_tags(tags)
    path = folder / output_contract.RUN_TAGS_FILE
    if not cleaned:
        path.unlink(missing_ok=True)
        return []
    # Written whole and replaced at once: two jobs writing one run's tags left a file of both, which no reader reads
    json_store_utils.write_json(path, {"tags": cleaned})

    return cleaned


def apply_tag_changes(folder: Path,
                      added: list[str],
                      removed: list[str]) -> list[str]:
    """The run folder's tags with `added` put after the ones it has and `removed` taken out, letter case ignored. A
    tags file that does not read raises `UnreadableRunTagsError`, and is left as it is."""

    gone = {tag.lower() for tag in clean_tags(removed)}

    return write_tags(folder, [tag for tag in _read_stored_tags(folder) + list(added) if tag.lower() not in gone])


def list_rule_tags(name: str) -> list[str]:
    """The tags the settings' naming rules (`tags.nameRules`) give a recording named `name`, its file or meeting
    folder's name: each rule whose prefix the name starts with, letter case ignored."""

    folded = name.casefold()
    rules = [(rule.prefix, rule.tag) for rule in config.settings.tags.name_rules] + _read_local_rules()

    return clean_tags([tag for prefix, tag in rules if folded.startswith(prefix.casefold())])


# Every run carries the format of the file it was made from (the owner), as its recording's suffix says it: a video
# processed as audio is still from a video
FORMAT_TAG = "from-{}"
# A run's medium as its frames file records it, for a run whose recording is gone
_MEDIUM_FORMATS = {output_contract.AUDIO_MEDIUM: "audio", output_contract.VIDEO_MEDIUM: "video",
                   output_contract.PDF_MEDIUM: "pdf", output_contract.PDF_TEXT_ONLY_MEDIUM: "pdf",
                   output_contract.PDF_MARKDOWN_CONVERSION_MEDIUM: "pdf"}


def describe_format_tag(recording: Path) -> str:
    """`from-video`, `from-audio`, `from-pdf` or `from-office`: the format of the recording a run reads, by its suffix
    (a folder of parts by its files)."""

    return FORMAT_TAG.format(inbox_service.describe_file_type(recording))


def is_format_tag(tag: str) -> bool:
    return tag.casefold() in {FORMAT_TAG.format(name) for name in ("video", "audio", "pdf", "office", "ppt")}


def is_program_tag(tag: str) -> bool:
    """Whether the program writes `tag` itself: a format tag, or a naming rule's (the settings' or the local file's).
    A worker that picks one names nothing about the subject: offered them, the worker gave a closing ceremony and a
    congress talk "lecture" and most runs "from-video" again."""

    key = _build_tag_key(tag)
    rules = [rule.tag for rule in config.settings.tags.name_rules] + [tag for _, tag in _read_local_rules()]

    return is_format_tag(tag) or any(_build_tag_key(rule) == key for rule in rules)


def describe_medium_format_tag(medium: str) -> str:
    """The format tag of a run's medium as `02_frames.json` records it, or "" for a medium it does not name."""

    medium = output_contract.RETIRED_MEDIUMS.get(medium, medium)
    name = _MEDIUM_FORMATS.get(medium, "pdf" if medium.startswith("pdf") else "")

    return FORMAT_TAG.format(name) if name else ""


def _read_local_rules() -> list[tuple[str, str]]:
    """The naming rules that name a client, kept in the git-ignored `tag-rules.local.json` beside the settings files
    (`{"nameRules": [{"prefix", "tag"}]}`); none when there is no such file or it does not read."""

    try:
        data = json.loads(LOCAL_RULES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    return [(str(rule.get("prefix") or ""), str(rule.get("tag") or "")) for rule in data.get("nameRules") or []
            if isinstance(rule, dict) and rule.get("prefix") and rule.get("tag")]


def list_tag_inventory(roots: list[Path]) -> list[tuple[str, int]]:
    """Every tag the run folders under `roots` hold that the program does not write itself (`is_program_tag`), as first
    written, with how many hold it, the most used first: the tags a worker picks from before it makes a new one."""

    counts: dict[str, list] = {}
    for root in roots:
        for folder in root.iterdir() if root.is_dir() else []:
            if folder.is_dir():
                for tag in read_tags(folder):
                    if not is_program_tag(tag):
                        counts.setdefault(_build_tag_key(tag), [tag, 0])[1] += 1

    return sorted(((tag, count) for tag, count in counts.values()), key=lambda pair: (-pair[1], pair[0].casefold()))


def _build_tag_key(tag: str) -> str:
    """A tag's form for matching: letter case, spaces, underscores and hyphens alike, a plural's last s dropped."""

    key = "-".join(re.split(r"[\s_\-]+", tag.strip().casefold()))

    return key[:-1] if len(key) > 3 and key.endswith("s") and not key.endswith("ss") else key


def match_inventory_tags(tags: list[str],
                         inventory: list[str],
                         limit: int) -> list[str]:
    """At most `limit` of the worker's `tags`, each written as the inventory's tag it only differs from in letter case,
    a plural, a space, an underscore or a hyphen; a tag no inventory tag matches kept as a new one."""

    known = {_build_tag_key(tag): tag for tag in inventory}

    return clean_tags([known.get(_build_tag_key(tag), tag) for tag in tags if not is_program_tag(tag)])[:limit]


# The tags line of a published document's metadata block: `tags: ["a", "b"]`, or a YAML list written by hand
_TAGS_LINE = re.compile(r"^tags:[ \t]*(.*)$")


def find_published_document(folder: Path) -> Path | None:
    """The run folder's published document, `<name>/<name>.md` beside its `provenance.json`; None before phase 4
    published one."""

    for document_dir in sorted(folder.iterdir()) if folder.is_dir() else []:
        document = document_dir / f"{document_dir.name}.md"
        if (document_dir / "provenance.json").is_file() and document.is_file():
            return document

    return None


def _split_front_matter(text: str) -> tuple[list[str], str] | None:
    """The metadata block's lines and the text after it, or None when the document opens with no block."""

    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)

    return (text[4:end].splitlines(), text[end + 5:]) if end != -1 else None


def _read_document_tags(document: Path) -> list[str] | None:
    """The tags the document's metadata block names, read as JSON or as a YAML list (inline or one `- tag` a line);
    None when it names none."""

    split = _split_front_matter(document.read_text(encoding="utf-8"))
    if split is None:
        return None
    lines = split[0]
    for index, line in enumerate(lines):
        match = _TAGS_LINE.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if not value:
            return clean_tags([item.strip()[2:].strip().strip("\"'") for item in lines[index + 1:]
                               if item.strip().startswith("- ")])
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = [item.strip().strip("\"'") for item in value.strip("[]").split(",")]

        return clean_tags([str(item) for item in parsed]) if isinstance(parsed, list) else None

    return None


def _write_document_tags(folder: Path,
                         tags: list[str]) -> None:
    """The published document's metadata block given `tags` (its tags line replaced, or added at the block's end),
    through the page's own save, which keeps the version it replaces; nothing when there is no published document or
    its block already names them."""

    document = find_published_document(folder)
    split = _split_front_matter(document.read_text(encoding="utf-8")) if document else None
    if split is None or _read_document_tags(document) == tags:
        return
    lines, rest = split
    kept = [line for line in lines if not _TAGS_LINE.match(line) and not (line.startswith("  - ") and _is_after_tags(lines, line))]
    block = [*kept, f"tags: {json.dumps(tags, ensure_ascii=False)}"]
    artifact_edit_service.save_markdown(folder, str(document.relative_to(folder)), "---\n" + "\n".join(block) + "\n---\n" + rest)


def _is_after_tags(lines: list[str],
                   line: str) -> bool:
    """Whether `line`, a `  - item`, belongs to a block list under the `tags:` line."""

    index = lines.index(line)
    previous = next((other for other in reversed(lines[:index]) if not other.startswith("  - ")), "")

    return bool(_TAGS_LINE.match(previous))
