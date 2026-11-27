# <!-- sync: seed/prompts.system_prompt_builder v1 -->
"""Assembles a worker's system prompt from `agent-context/`. It renders; it never instructs.

Instruction text is DATA, in `agent-context/instructions/*.md`. It is not in this file and not in any
other `.py`, for a reason that only shows up later: a playbook's instruction text always turns out to
be split across a Python prompt and a prose "contract" file, and a `.py` cannot be merged with a
`.md`. Keeping the prompt in code makes that duplication permanent.

The instruction file carries four kinds of marker: `{{PLACEHOLDER}}` (substituted from
`output_contract.PROMPT_PLACEHOLDERS`, so a rule a validator checks has ONE definition and the prompt, the contract
and the template all render it), `{{SHARED:<name>}}` (a fragment from `instructions/_shared/`),
`{{PHASE_CATALOGUE}}` (the other phases and what each is for) and `{{INJECT:base|contracts|templates}}` (the
documents `context_map.json` declares for this phase and this item, inlined AT THE ANCHOR - the diagram rules next
to the diagram instruction, because a rule stated far from where it applies is not followed). The STEP comments in
`build_system_prompt` give the order they are expanded in.

Every failure here RAISES. A builder that skips an unresolvable entry and returns the rest renders a
prompt that looks complete and is missing the one document the phase cannot work without - silently,
on every item, forever. That is the defect this whole folder exists to prevent, one level down.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import config
from models import output_contract
from models.run_errors import PromptBuildError
from services import instruction_store_service

INJECTION_HEADERS = {
    "base": ("CONTEXT YOU HAVE ALREADY BEEN GIVEN\n"
             "  Reproduced IN FULL below. Not optional background - this is what the work MEANS.\n"
             "  Do not go looking for these; they are not in the facts catalogue."),
    "contracts": ("THE RULES YOUR OUTPUT IS CHECKED AGAINST\n"
                  "  Reproduced IN FULL below, at the point where they apply. A validator enforces\n"
                  "  every one of them against the bytes you write."),
    "templates": ("THE EXACT SHAPE TO PRODUCE\n"
                  "  Reproduced IN FULL below. Follow it section for section."),
}

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
SHARED = re.compile(r"\{\{SHARED:([a-z0-9-]+)\}\}")
SHARED_DEPTH = 4  # how deep guidelines may include one another: a workflow's file, its step's guideline, the terms
# The item field naming the workflow a call belongs to (`video`, `audio`, `pdf`), and the run's own, set once the run
# knows its recording: a phase with an instruction per workflow reads the item's, else the run's
WORKFLOW_FIELD = "workflow"
RUN_WORKFLOW = ""
# A phase's map of workflow to its own instruction file; `instruction` stays the step's shared text, which a workflow
# with no file of its own, and a process started on code that knows no map, reads
WORKFLOWS_KEY = "workflows"
INJECT = re.compile(r"\{\{INJECT:([a-z]+)\}\}")
# The roster of the OTHER phases, for a role that may be asked to do any of their jobs. It is
# rendered rather than written down because a second list of phases is a second thing to keep in
# step with context_map.json, which is already the single answer to what each phase is for.
PHASE_CATALOGUE = re.compile(r"\{\{PHASE_CATALOGUE\}\}")

CATALOGUE_PREAMBLE = (
    "You may be asked for any of the jobs below. `read_instruction(phase, kind)` renders that "
    "phase's own\ninstruction in full - the name in backticks is the `phase` argument. Read one "
    "only when you are\nactually doing that phase's job.")


def _require(declaration: dict,
             key: str,
             where: str) -> Any:
    """`declaration[key]`, or say precisely which declaration lacks it."""

    if key not in declaration:
        raise PromptBuildError(f"{where} has no {key!r}: {declaration}")

    return declaration[key]


def _read(path: Path,
          what: str) -> str:
    """Read a file that something declared, or say precisely which declaration is wrong. A shipped instruction is read
    as it is in force: a person's edit from the page when one exists, else the default."""

    if not path.is_file():
        raise PromptBuildError(f"{what} names {path}, which is not on disk")
    relative = path.relative_to(config.AGENT_CONTEXT_DIR).as_posix() if path.is_relative_to(config.AGENT_CONTEXT_DIR) else ""
    if relative and instruction_store_service.is_editable(relative):
        return instruction_store_service.read_active(relative).strip()

    return path.read_text(encoding="utf-8").strip()


# The one shared fragment that may be extended by a file git never sees: the team's own terms name the
# client and its systems, so the tracked fragment holds the format and invented examples, and the real
# entries live beside it in `known-terms.local.md`, which is git-ignored.
KNOWN_TERMS = "known-terms"
LOCAL_SUFFIX = ".local.md"


def set_run_workflow(workflow: str) -> None:
    """The workflow of the run this process runs (`video`, `audio` or `pdf`); a test sets and puts it back."""

    global RUN_WORKFLOW
    RUN_WORKFLOW = workflow


def read_context_text(relative: str) -> str:
    """The text of one file of `agent-context/`, named by its path there (`compaction/cut-thinking/finish.md`), read now."""

    return _read(config.AGENT_CONTEXT_DIR / relative, relative)


def read_known_terms(root: Path | None = None) -> str:
    """The term mapping a worker is sent: the tracked fragment, plus the local one when it is on disk."""

    shared_dir = (root or config.AGENT_CONTEXT_DIR) / "instructions" / "_shared"
    text = _read(shared_dir / f"{KNOWN_TERMS}.md", f"{{{{SHARED:{KNOWN_TERMS}}}}}")
    local = shared_dir / f"{KNOWN_TERMS}{LOCAL_SUFFIX}"

    return f"{text}\n\n{local.read_text(encoding='utf-8').strip()}" if local.is_file() else text


# A known term's entry in the one term mapping, and the words it says the transcript writes for it ("The transcript writes
# it "Sweetmart", which is not the retailer"): notes written from the speech carry the word, and a chapter wrote the
# misheard word as a stakeholder though the mapping was in its instructions.
# An entry may name more than its term before the colon, "- **ABC** (numbered: ABC1, ...) and **XYZ**: ...": the term
# is its first bold name, and the entry runs to the next one
_TERM_ENTRY = re.compile(r"^- \*\*([^*]+)\*\*(.*?)(?=^- \*\*|\Z)", re.MULTILINE | re.DOTALL)
# The quoted spellings after "transcript writes it" (or "writes <TERM>"), joined by ", ", " or ", " and " or ", or "
_TRANSCRIPT_WORDS = re.compile(r"transcript writes (?:it|them|[A-Z][\w/-]*)\s+((?:\"[^\"]+\"(?:,?\s+(?:or|and)\s+|,\s*)?)+)")


def read_transcript_spellings() -> dict[str, str]:
    """Each word the term mapping says the transcript writes for a term, with that term: {"Sweetmart": "Legal", ...}."""

    spellings: dict[str, str] = {}
    for term, body in _TERM_ENTRY.findall(read_known_terms()):
        for words in _TRANSCRIPT_WORDS.findall(" ".join(body.split())):
            spellings.update({word: term for word in re.findall(r"\"([^\"]+)\"", words)})

    return spellings


def _get_context_map(root: Path) -> dict:
    """The phase -> material mapping. One answer to 'what does this worker receive?'."""

    index = root / config.CONTEXT_MAPPING_FILE
    if not index.is_file():
        raise PromptBuildError(f"no delivery manifest at {index} - {root.name}/ is missing it, "
                               f"or it is missing from the package")
    try:
        document = json.loads(index.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptBuildError(f"{index} is not valid JSON: {exc}") from exc

    return _require(document, "phases", str(index))


def _get_phase(phase: str,
               root: Path) -> dict:
    """The phase's declaration in the context map, or a named error: every phase that runs a worker declares what that
    worker receives."""

    spec = _get_context_map(root).get(phase)
    if spec is None:
        raise PromptBuildError(f"no phase {phase!r} in {config.CONTEXT_MAPPING_FILE} - every phase that "
                               f"runs a worker declares what that worker receives")

    return spec


def list_phase_specifications(root: Path) -> dict[str, dict]:
    """Every phase the map declares, keyed by name - what a catalogue of instructions is built from."""

    return _get_context_map(root)


def substitute(text: str,
               where: str) -> str:
    """Replace every `{{NAME}}` from the config whitelist. An unknown one raises."""

    # Never leaves a placeholder rendered as itself: a worker handed a literal `{{TERMINAL_TOKENS}}`
    # has been given a rule it cannot follow, and the prompt still looks well-formed.

    def resolve_one_placeholder(match: re.Match) -> str:
        name = match.group(1)
        if name not in output_contract.PROMPT_PLACEHOLDERS:
            raise PromptBuildError(f"{where}: unknown placeholder {{{{{name}}}}} - add it to "
                                   f"output_contract.PROMPT_PLACEHOLDERS or fix the spelling")

        return output_contract.PROMPT_PLACEHOLDERS[name]

    return PLACEHOLDER.sub(resolve_one_placeholder, text)


_WHEN_RULE = f"a `when` rule of {config.CONTEXT_MAPPING_FILE}"


def _list_matching_rules(spec: dict,
                         item: dict | None) -> list[dict]:
    """The `when` rules this item satisfies, in declaration order. A condition reads an item FIELD against a closed set
    of values the orchestrator already holds, never a judgement the model makes."""

    return [rule for rule in spec.get("when", [])
            if str((item or {}).get(_require(rule, "field", _WHEN_RULE), "")) in _require(rule, "in", _WHEN_RULE)]


def _select_instruction(spec: dict,
                        item: dict | None) -> str:
    """The instruction file this item's prompt is built on: its workflow's own file when the phase's `workflows` names
    one (the item's `workflow`, else the run's), else the phase's `instruction`, unless a matching `when` rule names
    one. A role whose whole job differs by kind of item gets its own file, not a fragment gated by prose the other kind
    is told to skip. Last matching rule wins, as with the injected lists."""

    chosen = _require(spec, "instruction", f"a phase of {config.CONTEXT_MAPPING_FILE}")
    workflow = str((item or {}).get(WORKFLOW_FIELD) or RUN_WORKFLOW)
    chosen = spec.get(WORKFLOWS_KEY, {}).get(workflow, chosen)
    for rule in _list_matching_rules(spec, item):
        chosen = rule.get("instruction", chosen)

    return chosen


def list_injected_documents(phase: str,
                            item: dict | None,
                            kind: str,
                            root: Path) -> list[str]:
    """The ordered, de-duplicated files of one kind this phase gives this item."""

    # `always` first, then each matching `when` block in declaration order; first occurrence wins.
    # A condition selects on an item FIELD against a closed set of values the orchestrator already
    # holds - never on anything the model decides - so material that matters to one kind of item is
    # charged only to that kind, and the phase's unconditional set stays down to what the phase itself
    # cannot work without.

    spec = _get_phase(phase, root)
    declared = spec.get(kind, [])
    ordered = [declared] if isinstance(declared, str) else list(declared)
    for rule in _list_matching_rules(spec, item):
        extra = rule.get(kind, [])
        ordered.extend([extra] if isinstance(extra, str) else extra)

    return list(dict.fromkeys(ordered))


def _render_phase_entry(phase: str,
                        context_map: dict) -> str:
    """One catalogue line: the name a worker passes as `phase`, and what that phase's worker is for."""

    identity = context_map.get("identity")
    if not identity:
        raise PromptBuildError(
            f"phase {phase!r} in {config.CONTEXT_MAPPING_FILE} declares no identity, so the "
            f"catalogue cannot say what its worker does - and a bare name is a legal argument with "
            f"nothing said about when to pass it, which is the silence the catalogue removes")

    # De-duplicated in declaration order: two `when` rules may select the same kind, and a kind
    # listed twice reads as two different arguments.
    kinds = list(dict.fromkeys(value for rule in context_map.get("when", [])
                               for value in _require(rule, "in", f"a `when` rule of phase {phase!r}")))
    doing = _require(identity, "doing", f"the identity of phase {phase!r} in {config.CONTEXT_MAPPING_FILE}")
    entry = f"- `{phase}` - {doing}."
    if kinds:
        entry += ("\n  Its instruction differs by the item's kind, so pass one of: "
                  + ", ".join(f"`{kind}`" for kind in kinds) + ".")

    return entry


def _render_phase_catalogue(root: Path,
                            current_phase: str) -> str:
    """Every OTHER phase this run declares, so a worker that may take any of their jobs knows which
    instruction to ask for BEFORE it asks. Without it the roster is reachable only through
    `list_instructions()` - a call the worker has no basis to make, because what would prompt the
    call is exactly what the call returns."""

    entries = [_render_phase_entry(name, context_map)
               for name, context_map in _get_context_map(root).items()
               if name != current_phase]

    return "\n".join([CATALOGUE_PREAMBLE, ""] + entries)


def _render_block(phase: str,
                  item: dict | None,
                  kind: str,
                  root: Path) -> str:
    """One injected block: a header saying it is already given, then the documents in full."""

    if kind not in INJECTION_HEADERS:
        raise PromptBuildError(f"{{{{INJECT:{kind}}}}} in the instruction of {phase!r} names no kind of document; "
                               f"the kinds are {', '.join(INJECTION_HEADERS)}")
    entries = list_injected_documents(phase, item, kind, root)
    if not entries:
        return ""

    parts = [INJECTION_HEADERS[kind], ""]
    for entry in entries:
        # Substituted here, not only in STEP 5, so an unknown placeholder names the document it is in
        text = substitute(_read(root / kind / entry, f"{config.CONTEXT_MAPPING_FILE} {phase}.{kind}"), entry)
        parts.append(f"--- BEGIN {kind.upper()}: {entry}")
        parts.append(text)
        parts.append(f"--- END {kind.upper()}: {entry}")
        parts.append("")

    return "\n".join(parts).rstrip()


def render_recording_section(tags: list[str],
                             has_frames: bool,
                             is_pdf: bool = False) -> str:
    """What phases 4 and 5 are told of the recording beside their instruction: the owner's tags, as a hint, and that a
    recording with no frames (an audio recording) has nothing to look at or cut. Empty for a video with no tags."""

    lines = []
    if tags:
        lines.append(f"The owner tagged this recording: {', '.join(tags)}. Read the tags as a hint of what the recording "
                     f"is and what its reader wants from the document.")
    if is_pdf:
        lines.append("This source is a PDF document, not a recording: each frame is one of its pages, the speech entries "
                     "are its pages' own text, cleaned of what a reader does not need, and nobody spoke. Where the rules "
                     "say the meeting, the recording, the presenter or what was said, read the document and what its "
                     "pages state. Keep "
                     "the document's own structure: its chapters follow its headings, and only a document with no "
                     "heading gets chapters you make from the subjects its pages change between. Inside a chapter, a "
                     "draft heading is one of the document's own sub-headings, and a section with none is one heading: "
                     "never a heading per fact. A table split over pages is written as one table. Its sentences are "
                     "exact: keep one as written rather than reword it, and never add to it a reason, a purpose or a "
                     "consequence the document does not state. A time it writes is written as it stands: a PDF has no "
                     "position in a recording, so the rules about clock times do not apply, and no AM or PM is added. An e-mail address it prints "
                     "was published by its authors and is written out as it stands, and so is a person it names: the "
                     "rule to name a person by their role is for a meeting's people, never for a document's authors, "
                     "scientists or sources. A `> **Decision:**`, `> **Action:**` or `> **Open:**` line marks what the "
                     "document itself records as settled, assigned to someone or left open (an incident's follow-up "
                     "with its owner); a step or instruction the document gives its reader is written as text, a list "
                     "or a code block, never as an action. A frame that is one of its figures "
                     "is placed as its cut, made whole by `cut` naming no box and no rectangle, with no look needed "
                     "first; it is never replaced by a diagram, since a redrawn figure lost what it showed, and a "
                     "Mermaid block may stand beside it under `#### Simplified view`.")
    # A PDF with no figure has no frame either, and was told it "is an audio recording" beside the PDF lines: its writer
    # weighed the two against each other (DEFECTS 521)
    if not has_frames and is_pdf:
        lines.append("This document has no figure: no frame is suggested, no frame tool is offered and no figure is "
                     "placed. Every rule above about frames, looks, cuts, marks, masks and figures does not apply; the "
                     "notes are its pages' text, one paragraph per block of a page.")
    elif not has_frames:
        lines.append("This recording has no frames: it is an audio recording, so no frame is suggested, no frame tool is "
                     "offered and no figure is placed. Every rule above about frames, looks, cuts, marks, masks and "
                     "figures does not apply; the notes are the speech, one paragraph per speaker's turn.")

    return "\n\n".join(["# This recording", *lines]) if lines else ""


def _expand_shared(name: str,
                   instruction: str,
                   root: Path) -> str:
    """The text of the fragment `{{SHARED:<name>}}` names in `instruction`; the known terms add the local file."""

    if name == KNOWN_TERMS:
        return read_known_terms(root)

    return _read(root / "instructions" / "_shared" / f"{name}.md", f"{instruction} {{{{SHARED:{name}}}}}")


def build_system_prompt(phase: str,
                        item: dict | None = None,
                        root: Path | None = None) -> str:
    """The system prompt for one phase and one item."""

    # STEP 1: find what this phase declares it gives its worker
    root = root or config.AGENT_CONTEXT_DIR
    context_map = _get_phase(phase, root)

    # STEP 2: load the instruction file this item is owed - the phase's, or the one its `when` rule
    # names - which is the prompt's spine
    instruction = _select_instruction(context_map, item)
    text = _read(root / "instructions" / instruction,
                 f"{config.CONTEXT_MAPPING_FILE} {phase}.instruction")

    # STEP 3: expand the guidelines shared with the other workflows and phases; a guideline may include another
    for _depth in range(SHARED_DEPTH):
        text = SHARED.sub(lambda match: _expand_shared(match.group(1), instruction, root), text)
    if SHARED.search(text):
        raise PromptBuildError(f"{instruction}: its guidelines include each other more than {SHARED_DEPTH} deep, "
                               f"through {', '.join(sorted(set(SHARED.findall(text))))}")

    # STEP 3b: list the other phases and what each one does, for a role that may take any of their
    # jobs. Before STEP 5 on purpose: the marker matches the {{NAME}} grammar, so one left unresolved
    # raises there as an unknown placeholder rather than reaching a worker as literal text.
    text = PHASE_CATALOGUE.sub(lambda _match: _render_phase_catalogue(root, phase), text)

    # STEP 4: inline the base documents, contracts and templates this item is owed, at their anchors
    text = INJECT.sub(lambda m: _render_block(phase, item, m.group(1), root), text)

    # STEP 5: resolve every {{PLACEHOLDER}} from config, so no rule reaches the worker unrendered
    return substitute(text, instruction)
