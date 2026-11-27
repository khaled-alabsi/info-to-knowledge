# <!-- sync: seed/cases.test_injected_context v2 -->
"""What `agent-context/context_map.json` declares must ARRIVE in the rendered system prompt.

The defect these were written for: the layer model and the cross-repository lookup rules were both in
the facts catalogue, both scoped to the tracer role, and the tracer never called `read_document`
for either. Chains were traced to the service layer and stopped one hop short of their terminals -
with every section of the artifact filled in, so nothing looked wrong. The same shape was then found
in the read order: `trace-contract.md` and the template were named as paths and opened only if the
worker chose to. The whole suite was green throughout, because reachability was asserted and arrival
never was.

So every assertion here is on the RENDERED prompt, in the strongest available form: the document's
full text, byte for byte. A test that only checks `context_map.json` parses asserts that a mapping
exists, never that a worker received anything.
"""

from __future__ import annotations

import ast
import json
import re
import tempfile
from pathlib import Path

from support.harness import check
import config
from models import output_contract
from prompts import document_messages, system_prompt_builder
from prompts.system_prompt_builder import PromptBuildError
from models.run_paths import RunPaths
from services import run_paths_service

_ROOT = config.AGENT_CONTEXT_DIR
_PHASES = json.loads((_ROOT / config.CONTEXT_MAPPING_FILE).read_text(encoding="utf-8"))["phases"]

# Every phase, and both sides of every per-item condition. Asserting only the matching side would
# pass on a rule that injects unconditionally, which charges every item for what one kind needs.
# TODO: every phase key in context_map.json, and BOTH sides of every per-item condition. Asserting only
# the matching side passes on a rule that injects unconditionally.
CASES = tuple((phase, None) for phase in _PHASES)

KINDS = ("base", "contracts", "templates")


def _label(phase: str,
           item: dict | None) -> str:
    return f"{phase}[{(item or {}).get('kind', '-')}]"


def _declared(phase: str,
              spec: dict,
              kind: str) -> list[str]:
    """Everything a phase can inject of one kind, unconditional and conditional alike."""

    out = list(spec.get(kind, []))
    for rule in spec.get("when", []):
        out.extend(rule.get(kind, []))

    return out


def test_every_declared_file_exists():
    """A renamed file with a stale declaration is a phase that renders without it - except the
    builder raises instead, which is what this proves is still wired up."""

    for phase, spec in _PHASES.items():
        for name in [spec["instruction"], *spec.get("workflows", {}).values()]:
            check(f"{phase}: instruction {name} exists", (_ROOT / "instructions" / name).is_file(),
                  f"declared in {config.CONTEXT_MAPPING_FILE}, absent from {_ROOT}/instructions")
        for kind in KINDS:
            for entry in _declared(phase, spec, kind):
                check(f"{phase}: {kind}/{entry} exists", (_ROOT / kind / entry).is_file(),
                      f"declared in {config.CONTEXT_MAPPING_FILE}, absent from {_ROOT}/{kind}")


def test_every_file_on_disk_is_declared_by_some_phase():
    """The mirror. A document nobody declares is staged, shipped and read by nobody - the same dead
    weight as an unreferenced facts tree, minus even the tool that could have opened it."""

    for kind in KINDS:
        declared = {e for phase, spec in _PHASES.items() for e in _declared(phase, spec, kind)}
        for path in sorted((_ROOT / kind).glob("*.md")):
            if path.name == "README.md":
                continue
            check(f"{kind}/{path.name} is declared by at least one phase", path.name in declared,
                  "it reaches no worker; declare it or delete it")

    used = {name for spec in _PHASES.values() for name in [spec["instruction"], *spec.get("workflows", {}).values()]}
    instructions = _ROOT / "instructions"
    texts = [path.read_text(encoding="utf-8") for path in instructions.rglob("*.md")]
    included = {name for text in texts for name in system_prompt_builder.SHARED.findall(text)}
    for path in sorted(instructions.rglob("*.md")):
        relative = path.relative_to(instructions).as_posix()
        if relative.startswith("_shared/"):
            # a shared guideline is a phase's own text or included by another file; this machine's terms are appended
            is_reached = (relative in used or path.stem in included
                          or path.name.endswith(system_prompt_builder.LOCAL_SUFFIX))
            check(f"instructions/{relative} is a phase's text or included by a file", is_reached,
                  "no phase names it and no file includes it, so nothing renders it")
            continue
        check(f"instructions/{relative} is used by some phase", relative in used,
              "no phase names it, so nothing renders it")


def test_each_phase_prompt_carries_every_document_it_declares():
    """Arrival, asserted on the rendered prompt as each document's own full text."""

    for phase, item in CASES:
        prompt = system_prompt_builder.build_system_prompt(phase, item)
        for kind in KINDS:
            for entry in system_prompt_builder.list_injected_documents(phase, item, kind, _ROOT):
                body = system_prompt_builder.substitute((_ROOT / kind / entry).read_text(encoding="utf-8").strip(), entry)
                check(f"{_label(phase, item)}: {kind}/{entry} is in the rendered prompt",
                      body in prompt, "declared for this phase and not delivered to the worker")


def test_every_shared_fragment_referenced_is_expanded():
    """`{{SHARED:x}}` left unexpanded would reach the worker as literal text, and the rule it names
    would reach it not at all."""

    for phase, item in CASES:
        prompt = system_prompt_builder.build_system_prompt(phase, item)
        check(f"{_label(phase, item)}: no unexpanded template markers",
              not re.search(r"\{\{[A-Za-z_:-]+\}\}", prompt),
              str(re.findall(r"\{\{[A-Za-z_:-]+\}\}", prompt)[:5]))


def test_the_conditional_context_is_injected_only_for_its_kind():
    """Both sides of the condition."""

    # TODO: name the phase and the two item kinds YOUR condition distinguishes.
    phase = next((p for p, s in _PHASES.items() if s.get("when")), None)
    if phase is None:
        check("no per-item condition declared yet - nothing to assert", True)

        return
    rule = _PHASES[phase]["when"][0]
    if not rule.get("in"):
        check(f"{phase}: the `when` rule names at least one value of `{rule.get('field')}`", False,
              "a rule with an empty `in` list selects nothing - fill it in or delete the rule")

        return
    selected = system_prompt_builder.list_injected_documents(phase, {rule["field"]: rule["in"][0]}, "base", _ROOT)
    other = system_prompt_builder.list_injected_documents(phase, {rule["field"]: "\x00not-a-real-value"}, "base", _ROOT)
    conditional = set(selected) - set(other)

    check("the conditional injects something", bool(conditional),
          "a `when` block that adds nothing is a rule with no effect")
    for entry in sorted(conditional):
        check(f"{entry} is injected for the selected kind", entry in selected)
        check(f"{entry} is NOT injected otherwise", entry not in other,
              "irrelevant context charged to every item in the phase")


def test_no_condition_selects_on_an_item_kind():
    """Frames have no kinds: a condition naming one never matches, and never says so, so the phase renders, the worker
    runs, and the document it needed was silently never injected."""

    for phase, spec in _PHASES.items():
        for rule in spec.get("when", []):
            check(f"{phase}: no condition selects on a frame kind, which no frame has", rule["field"] != "kind",
                  f"a condition on kind {rule.get('in')!r} never matches any item")


def test_no_instruction_or_contract_restates_a_config_value():
    """The anti-drift assertion, and the one that would have caught the incident that cost a full
    retry budget: a prompt requiring `- **[external] — …` against a validator requiring
    `- **external — …`, both "correct", neither checked against the other."""

    # A rule a validator enforces is defined ONCE in `output_contract.PROMPT_PLACEHOLDERS`. Instructions and
    # contracts render it through `{{NAME}}`; writing the value out again creates a second statement
    # one edit away from disagreeing, invisibly.
    #
    # `templates/` and `base/` are exempt, deliberately. A template's markers and section headings ARE
    # the artifact's shape - that is the file's whole job - and `base/` is domain prose that describes
    # the system under analysis, not this playbook's output contract.

    # Every placeholder whose value is a literal a document might be tempted to write out. Names
    # and markers, not prose.
    watched = {name: value for name, value in output_contract.PROMPT_PLACEHOLDERS.items()
               if isinstance(value, str) and "\n" not in value and len(value) > 3}
    for folder in ("instructions", "contracts"):
        for path in sorted((_ROOT / folder).rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            for name, value in watched.items():
                check(f"{folder}/{path.name} uses {{{{{name}}}}} rather than the literal {value!r}",
                      value not in text,
                      "a second statement of a config value; render it through the placeholder")


def test_every_contract_is_named_by_a_validator():
    """Enforces the taxonomy mechanically instead of by intent: `contracts/` is for rules a program
    checks. A file in there that no validator mentions is an instruction or a fact wearing the word
    "contract", and it was that misfiling which kept 276 lines of instruction text sitting behind an
    optional read order for the life of the package."""

    claimed = set()
    validators = Path(config.PLAYBOOK_ROOT) / "src" / "validators"
    for source in sorted(validators.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", "") == "ENFORCES" for t in node.targets)
                    and isinstance(node.value, ast.Constant)):
                claimed.add(node.value.value)

    for path in sorted((_ROOT / "contracts").glob("*.md")):
        if path.name == "README.md":
            continue
        check(f"contracts/{path.name} is claimed by a validator's ENFORCES", path.name in claimed,
              "no validator enforces it, so it is not a contract - move it to instructions/ or base/")
    for name in sorted(claimed):
        check(f"the contract {name} a validator claims to enforce exists",
              (_ROOT / "contracts" / name).is_file(),
              "a validator names a contract that is not in contracts/")


def test_an_unknown_placeholder_raises():
    """A misspelling must fail loudly. Rendered as itself, `{{TERMNAL_TOKENS}}` reaches the worker as
    literal text inside a prompt that still looks well-formed."""

    try:
        system_prompt_builder.substitute("write only {{TERMNAL_TOKENS}} here", "fixture")
        check("an unknown placeholder raises", False, "it rendered as itself instead")
    except PromptBuildError as exc:
        check("an unknown placeholder raises PromptBuildError", True)
        check("the error names the placeholder", "TERMNAL_TOKENS" in str(exc), str(exc))


def test_a_missing_declared_file_raises_rather_than_rendering_without_it():
    """The failure this whole folder exists to prevent, one level down: a builder that skipped an
    unresolvable entry would render a prompt that looks complete and is missing the one document the
    phase cannot work without - silently, on every item, forever."""

    with tempfile.TemporaryDirectory() as handle:
        root = Path(handle)
        (root / "instructions").mkdir()
        (root / "base").mkdir()
        (root / "instructions" / "x.md").write_text("job\n{{INJECT:base}}", encoding="utf-8")
        (root / config.CONTEXT_MAPPING_FILE).write_text(
            '{"phases": {"trace": {"instruction": "x.md", "base": ["gone.md"]}}}', encoding="utf-8")
        try:
            system_prompt_builder.build_system_prompt("trace", None, root)
            check("a declared-but-missing file raises", False,
                  "it rendered a prompt without the document instead")
        except PromptBuildError as exc:
            check("a declared-but-missing file raises PromptBuildError", True)
            check("the error names the missing entry", "gone.md" in str(exc), str(exc))


def test_an_unknown_inject_kind_raises_rather_than_rendering_nothing():
    """`{{INJECT:template}}` for `templates` once rendered as nothing: the worker got a prompt that looked complete and
    lacked the template."""

    with tempfile.TemporaryDirectory() as handle:
        root = Path(handle)
        (root / "instructions").mkdir()
        (root / "instructions" / "x.md").write_text("job\n{{INJECT:template}}", encoding="utf-8")
        (root / config.CONTEXT_MAPPING_FILE).write_text('{"phases": {"trace": {"instruction": "x.md"}}}', encoding="utf-8")
        try:
            system_prompt_builder.build_system_prompt("trace", None, root)
            check("an unknown kind raises", False, "the marker rendered as nothing instead")
        except PromptBuildError as exc:
            check("an unknown kind raises PromptBuildError", True)
            check("the error names the marker's kind", "template" in str(exc), str(exc))


def test_an_undeclared_phase_raises():
    """Every phase that runs a worker declares what that worker receives. A phase missing from the
    manifest must not quietly produce an empty prompt."""

    try:
        system_prompt_builder.build_system_prompt("no-such-phase")
        check("an undeclared phase raises", False, "it rendered something instead")
    except PromptBuildError:
        check("an undeclared phase raises PromptBuildError", True)


# Measured 2026-08-16 against the served window. Ceilings are ~1.6x the current size: enough that
# ordinary edits do not trip them, tight enough that adding a whole document to `always` does.
# Injection is charged on EVERY attempt of EVERY item, before the worker reads a line of source, so
# this is the number that decides whether a phase can still do its job in its context window.
# TODO: measure each phase once, then set its ceiling ~1.6x that - loose enough that ordinary edits
# do not trip it, tight enough that adding a whole document to `always` does.
SIZE_CEILING_TOKENS = {phase: 40000 for phase in _PHASES}


def test_no_phase_prompt_exceeds_its_size_ceiling():
    """A budget nobody measures is a budget that grows until a run degrades for reasons nobody can
    name. Roughly 4 characters per token, which is close enough to catch a doubling."""

    for phase, item in CASES:
        tokens = len(system_prompt_builder.build_system_prompt(phase, item)) // 4
        ceiling = SIZE_CEILING_TOKENS[phase]
        check(f"{_label(phase, item)}: ~{tokens} tokens is within its {ceiling} ceiling",
              tokens <= ceiling,
              "raise the ceiling deliberately, or take something out of `always`")


# TODO: add the regression test for YOUR domain here, stated as the FACTS a worker must have
# rather than as a filename. The reference implementation asserts that its tracer prompt states what
# its intermediate layer is called and where shared code lives - the two facts whose absence made it
# stop every chain one hop short. Write the equivalent for the failure your playbook has seen; a
# named file can be moved without breaking a test, a fact cannot.


def test_every_phase_renders_from_the_packages_own_agent_context():
    """The builder reads the playbook's own `agent-context/`, never a copy: nothing stages one."""

    for phase, spec in _PHASES.items():
        rendered = system_prompt_builder.build_system_prompt(phase, {})
        instruction = (_ROOT / "instructions" / spec["instruction"]).read_text(encoding="utf-8")
        first_line = next((line for line in instruction.splitlines() if line.strip()), "")
        check(f"{phase}: the builder renders from the package's own tree",
              bool(first_line) and first_line.strip("# ").strip() in rendered, first_line)


def test_ensure_creates_the_run_directories_and_copies_no_instructions():
    """No `agent-context/` in a run folder (the owner, 2026-09-28). Every worker's instruction text
    reaches it INLINED in the system prompt and no tool a call is given takes a path, so a copy under
    the output root would be a folder nothing reads."""

    with tempfile.TemporaryDirectory() as handle:
        base = Path(handle)
        repository = base / "repo"
        repository.mkdir()
        paths = RunPaths(repository=repository, module="m", output_root_override=base / "out")
        run_paths_service.ensure_run_paths(paths)

        check("ensure() created the output root", paths.output_root.is_dir())
        check("ensure() created the runs root", paths.runs_root.is_dir())
        # REACHABLE: the run folder holds what the run PRODUCED, and reference material is not that.
        check("ensure() copied no agent-context/ into the run folder",
              not (paths.output_root / config.AGENT_CONTEXT_DIR.name).exists())
        check("ensure() copied no instruction file into the run folder",
              not any(paths.output_root.rglob("*.md")), sorted(str(p) for p in paths.output_root.rglob("*.md"))[:5])


def test_every_worker_that_reads_speech_is_told_the_teams_terms():
    """The owner (2026-10-02): speech recognition writes a team's terms as sound-alikes, and two review points left them
    to the reader as unknown acronyms. Each worker reading the transcript gets the terms with their meanings."""

    from prompts import system_prompt_builder
    for phase in ("frames_processing", "open_questions", "answer_questions", "write", "review"):
        prompt = system_prompt_builder.build_system_prompt(phase)
        check(f"{phase} is told the team's terms", "Terms this team uses" in prompt and "**BA**: business analyst" in prompt
                      and "{{SHARED" not in prompt, prompt[-300:])


def test_the_workers_that_write_open_questions_never_ask_about_a_date_from_their_own_year():
    """A frame's notes asked why a slide was dated years after "today": the model took its own sense of the current
    year for the meeting's, and the open questions step kept the question. Neither worker is told the meeting's date."""

    for phase in ("frames_processing", "open_questions"):
        prompt = " ".join(system_prompt_builder.build_system_prompt(phase).split())
        check(f"{phase} is told its sense of the current year is not the meeting's",
              "your own sense of the current year is not the meeting's" in prompt, phase)


def test_a_frames_open_questions_never_ask_what_the_presenter_does_on_screen():
    """Three batches of one run carried questions such as which file the presenter opens next or which tool they use:
    the frame's notes asked them first, and the batch's open questions kept them. `open_questions.md` forbade them; the
    frame's own instruction did not."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("frames_processing").split())
    check("a frame's notes are told what the presenter does on screen is no open question",
          "What the presenter does on screen is never a question either" in prompt, prompt[:200])


def test_the_open_questions_step_leaves_out_what_the_presenter_will_do_next():
    """The open questions step kept "which file will be opened next" and "how will the presenter proceed", reading a
    ban on what the presenter DID as allowing what they WILL do, and keeping questions because the notes asked them."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("open_questions").split())
    check("the open questions step is told what the presenter will do next is no question",
          "what the presenter did or will do on screen" in prompt
          and "A question the notes list of this kind is left out" in prompt, prompt[:200])


def test_the_critique_is_told_a_rule_never_asks_for_less_than_the_notes_must_hold():
    """A critique wrote the rule "only record the facts the presenter names; do not transcribe every visible cell",
    against the instruction that the notes quote every text on the screen. Its rules are carried into later runs'
    instructions, so a rule asking for less would undo the outline the checks require."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("critique").split())
    check("the critique is told a rule never asks for less than the notes must hold",
          "A rule never asks the notes to hold less than they must." in prompt, prompt[:200])


def test_the_critique_is_told_to_check_a_meaning_given_beside_a_foreign_term_defect():
    """A fact gave the opposite English meaning beside a quoted German term, and the critique never named it, so the
    repair kept it (DEFECTS 421). The critique's list of common mistakes now names a wrong meaning."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("critique").split())
    check("the critique is told to check a meaning given beside a term in another language",
          "An English meaning beside a term in another language that says something else than the term" in prompt,
          prompt[:200])


def test_the_critique_is_told_a_joke_written_as_a_fact_is_a_mistake_defect():
    """A speaker's joke about a building's height was written as a fact about the data, the slide showing otherwise, and
    the critique did not name it (DEFECTS 624). Its list of common mistakes names a joke or irony written as a fact."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("critique").split())
    check("the critique is told to check a joke written as a fact",
          "A joke or an ironic remark written as a fact about the subject" in prompt, prompt[:200])


def test_the_critique_is_told_a_meaning_only_the_next_slide_s_subject_supports_is_a_mistake_defect():
    """A frame still showing a slide had the speech already naming the next slide's subject, and its fact gave the old
    slide's options that subject's meaning; the critique said nothing (DEFECTS 430). Its list of common mistakes names
    it beside the talk-ahead case."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("critique").split())
    check("the critique is told to catch a meaning only the next slide's subject supports",
          "This screen's parts given a meaning that only the next slide supports, because the speaker named the next "
          "slide's subject while this one was showing" in prompt,
          prompt[:200])


def test_a_frame_nothing_is_said_over_has_no_none_quote_in_its_take_aways_defect():
    """A frame with no speech wrote the take-away "(none) → no speech during this frame.", the empty mark as a spoken
    quote (DEFECTS 433). The instruction says what such a frame's reading lines are."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("frames_processing").split())
    check("a frame with nothing said writes `not mentioned` lines, never a line opening `(none) →`",
          "A frame nothing is said over has no spoken quote: its reading lines are those `not mentioned` lines, "
          "never a line opening `(none) →`" in prompt, prompt[:200])


def test_a_comparison_with_no_named_baseline_is_checked_against_the_screen_s_numbers_defect():
    """A take-away wrote "Zstandard is faster than LZ4" for a speaker's "much faster" said of a table where it is slower
    than LZ4 and faster than the default (DEFECTS 431). The phase 3 instruction names the check, with invented numbers."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("frames_processing").split())
    check("an unnamed baseline is read from the numbers on screen",
          'is faster than A, never than B, which the table shows slower than B' in prompt, prompt[:200])


def test_a_screen_shown_again_with_nothing_said_is_told_to_write_no_take_away_defect():
    """A screen shown again with no word said over it got the take-away "The screen shows the slide again → no new
    information is added", since the instruction asked for what the talk is about and why the screen came back, whatever
    was said (DEFECTS 429). The instruction now says `(none)` when nothing is said."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("frames_processing").split())
    check("the repeat's take-aways are `(none)` when nothing is said over the showing",
          "why the screen came back. When nothing is said over this showing, `(none)`" in prompt, prompt[:200])


def test_the_second_pass_is_told_a_misread_box_label_is_named_wrong():
    """Two second passes corrected misread box labels in place: each diagram was refused as redrawn, and four frames
    kept the wrong labels. Only lines named under `## Wrong in the diagram` may change."""

    prompt = " ".join(system_prompt_builder.build_system_prompt("second_pass").split())
    check("the second pass is told a misread label goes under `## Wrong in the diagram`",
          "or a box whose label is misread" in prompt and "A misread label names EVERY line that writes it" in prompt,
          prompt[:200])


def test_the_local_term_file_extends_the_tracked_one():
    """The team's own terms name the client and its systems, so they live in a git-ignored `known-terms.local.md`
    beside the tracked fragment. Both reach the worker; a missing local file loses nothing else."""

    root = Path(tempfile.mkdtemp())
    shared = root / "instructions" / "_shared"
    shared.mkdir(parents=True)
    tracked = "# Terms this team uses\n\n- **Gateway**: the API gateway.\n"
    (shared / "known-terms.md").write_text(tracked, encoding="utf-8")

    without = system_prompt_builder.read_known_terms(root)
    check("without the local file, the tracked fragment is the whole mapping",
          without == tracked.strip(), without)

    local = "- **AcmeSync**: the sync service. The transcript writes it \"Acme Zinc\".\n"
    (shared / "known-terms.local.md").write_text(local, encoding="utf-8")
    both = system_prompt_builder.read_known_terms(root)
    check("the local entry is appended after the tracked ones",
          "**Gateway**" in both and both.endswith(local.strip()), both)

    original = config.AGENT_CONTEXT_DIR
    config.AGENT_CONTEXT_DIR = root
    try:
        spellings = system_prompt_builder.read_transcript_spellings()
    finally:
        config.AGENT_CONTEXT_DIR = original
    check("a local term's misheard word is found like a tracked one",
          spellings.get("Acme Zinc") == "AcmeSync", str(spellings))


def test_every_spelling_a_term_lists_is_read_whatever_joins_them():
    terms = ('- **Gateway**: the gateway service; the transcript writes it "gate way" and "gateaway".\n'
             '- **AcmeSync**: the sync tool; the transcript writes it "acme sink", "ac me", or "acne sync".\n')
    original = system_prompt_builder.read_known_terms
    system_prompt_builder.read_known_terms = lambda: terms
    try:
        spellings = system_prompt_builder.read_transcript_spellings()
    finally:
        system_prompt_builder.read_known_terms = original
    check("a second spelling after `and` is read", spellings.get("gateaway") == "Gateway", str(spellings))
    check("the last spelling after `, or` is read", spellings.get("acne sync") == "AcmeSync", str(spellings))


def _read_error(action) -> str:
    try:
        action()
    except PromptBuildError as exc:
        return str(exc)

    return ""


def test_an_unknown_phase_is_refused_by_name_in_every_entry_point():
    message = _read_error(lambda: system_prompt_builder.list_injected_documents("no-such-phase", {}, "base", _ROOT))
    check("listing the documents of an unknown phase raises, naming it", "no-such-phase" in message, message)


def test_a_context_map_without_phases_raises_naming_the_file():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / config.CONTEXT_MAPPING_FILE).write_text("{}", encoding="utf-8")
        message = _read_error(lambda: system_prompt_builder.list_phase_specifications(root))
    check("a map with no `phases` key raises, naming the file", config.CONTEXT_MAPPING_FILE in message, message)


def test_a_terminal_is_outlined_one_line_a_part_with_an_example_defect():
    # A lecture's batch outlined three terminal frames as one prose line each, its commands in backticks, and the
    # thin-outline check refused all three. The rule had no example for a terminal; it now shows one
    prompt = " ".join(system_prompt_builder.build_system_prompt("frames_processing").split())
    check("the frame instructions show a terminal outlined one quoted line a part",
          '- terminal: the whole frame, dark background' in prompt and '- line "~/demo > ls"' in prompt, prompt[:200])


def test_the_term_list_names_claude_code_for_the_transcript_s_cloud_code_defect():
    # A lecture's notes kept the transcript's "Cloud Code" for the agent its screens name Claude Code, two letters past
    # what the program respells; the term list now maps it, which phase 4's request reads to name such notes
    spellings = system_prompt_builder.read_transcript_spellings()
    check("the transcript's \"Cloud Code\" is mapped to Claude Code", spellings.get("Cloud Code") == "Claude Code",
          str(spellings))


def test_a_detail_the_talk_never_raises_is_no_open_question_defect():
    # A batch of a hardware talk asked for the enclosure's model, a firmware version and a part number past what the
    # schematic printed, none of which the talk raised. Both the frame's and the batch's open questions now rule it out.
    for step in ("frames_processing", "open_questions"):
        prompt = " ".join(system_prompt_builder.build_system_prompt(step).split())
        check(f"{step} is told a detail the talk never makes its subject is no question",
              "never makes its subject" in prompt and "which model is the enclosure in" in prompt, prompt[:200])


def test_the_writer_is_taught_to_edit_a_checked_draft_block_by_block_defect():
    # Defect: a chapter part was written again whole for each check's finding and lost what its draft had right
    # (DEFECTS 584). Fix: write.md teaches `edit_chapter_draft`, the tool the chapter follow-up offers
    from services import write_conversation_service

    prompt = " ".join(system_prompt_builder.build_system_prompt("write").split())
    check("the writer is taught the edit tool by name", "`edit_chapter_draft`" in prompt
          and write_conversation_service._EDIT_TOOL == "edit_chapter_draft", prompt[:200])


def test_the_repair_is_taught_the_add_under_edit_its_reader_takes_defect():
    # Defect: a repair wrote "add under [O126.2]: line ..." with no such edit taught or read, and its corrections were
    # lost (Lecture 7, DEFECTS 572). Fix: the reader takes the edit, and second_pass.md teaches it in the shape it reads
    import re

    from utils import frame_markdown_utils

    prompt = " ".join(system_prompt_builder.build_system_prompt("second_pass").split())
    taught = re.search(r"`- (add under O<frame>\.\d+: <part>)`", prompt)
    check("the repair is taught the add-under edit", taught is not None, prompt[:200])
    line = taught.group(1).replace("<frame>", "7").replace("<part>", 'line "page 3"') if taught else ""
    answer = frame_markdown_utils.parse_answer(f"# Frame 7\n\n## What changed\nA line.\n\n## Edits\n- {line}\n",
                                               is_addition=True)
    edits = (answer["frames"] or [{}])[0].get("edits") or []
    check("the taught line reads as an add under", [edit.get("action") for edit in edits] == ["add under"], str(edits))


def test_a_placeholder_hint_in_an_empty_input_line_is_never_typed_input_defect():
    # Defect: an agent's empty input bar showed the dim hint `Try "<example>"`, and the notes wrote it as typed input
    # and the repair as "a previous attempt" (Lecture 7 frame 7, DEFECTS 578). Fix: the notes' instruction says dim
    # text in an empty input line is a placeholder, quoted as one
    prompt = " ".join(system_prompt_builder.build_system_prompt("frames_processing").split())
    check("the notes are told a dim hint is a placeholder, never input",
          "is the program's placeholder hint" in prompt and "never write it as typed, sent or earlier input" in prompt,
          prompt[:200])
