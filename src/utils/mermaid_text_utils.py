"""Mermaid source as plain text: whether a string is a diagram, and how to get it out of or past its fence. Pure.

Two layers ask these questions - the frame validator of a frame's `diagram`, the facts validator of a
section's diagrams and of the finished document - so they ask them here, once.

A local model often writes a flowchart's nodes as bare labels, `"Partner Portal" --> "Proxy"`, which Mermaid
refuses to draw: a node needs an id. `name_bare_nodes` gives each such label one, the same label always the same
id, and changes nothing else.
"""

from __future__ import annotations

import re

__all__ = ["is_mermaid", "strip_fence", "remove_blocks", "name_bare_nodes", "repair_group_used_as_box",
           "wrap_bracketed_labels_in_quotes", "repair_diagram", "take_wrapped_diagram", "repair_fenced_blocks",
           "list_fenced_problems",
           "count_arrows",
           "find_syntax_problems", "is_arrowless_flowchart", "join_plus_nodes", "MERMAID_KINDS", "CHART_KINDS", "MOST_TRUSTED_ARROWS"]

# The Mermaid types a frame's or a section's diagram may start with; a chart type of CHART_KINDS among them (gantt)
# is refused where a chart is.
MERMAID_KINDS = ("flowchart", "graph", "sequenceDiagram", "stateDiagram", "stateDiagram-v2", "classDiagram",
                 "erDiagram", "journey", "gantt", "mindmap", "timeline", "block-beta", "C4Context")
# The words Mermaid starts a CHART with: values plotted, never a flow, an architecture, a sequence or a state. A gantt is
# bars over time, and needs each bar's day: drawn from a slide that shows months, its days were invented, and it went
# into two published documents beside the slide (2026-10-06)
CHART_KINDS = ("pie", "xychart-beta", "quadrantChart", "sankey-beta", "gantt")
# The most arrows a diagram phase 3 drew from a slide is trusted with: past it, the slide's lines were drawn by where the
# boxes sit, and a 13-arrow diagram about half wrong went into a document beside its slide (2026-10-06)
MOST_TRUSTED_ARROWS = 10
# A flowchart's link: an arrow head, a circle or a cross head (`--o`, `--x`), or an open link `---`
_ARROW = re.compile(r"<?[-=.]{2,}>|[-=.]{2,}[ox](?![\w])|---")
# A class or ER diagram's relation, one per line: `A <|-- B`, `A *-- C`, `A ..> D`, `CUSTOMER ||--o{ ORDER`
_RELATION_LINE = re.compile(r"--|\.\.")
_RELATION_KINDS = ("classDiagram", "erDiagram")
# A sequence diagram's message: `->>`, `-->>`, `->`, `-->`, `-)`, `--)`, `-x`, `--x`
_SEQUENCE_ARROW = re.compile(r"-{1,2}(?:>>|[>x)])")
_FENCE = re.compile(r"^```(?:mermaid)?[ \t]*\n?|\n?[ \t]*```$")
_BLOCK = re.compile(r"```mermaid\n.*?\n```", re.DOTALL)
_FLOWCHART_KINDS = ("flowchart", "graph")
_NAMED_NODE = re.compile(r"\b([A-Za-z_][\w-]*)\s*[\[({]+\s*\"([^\"]*)\"")
_CLOSERS = {"[": "]", "(": ")", "{": "}"}
# What a link's own text follows, `A -- "text" --> B`: an open link, not a finished arrow.
_OPEN_LINKS = ("--", "==", "-.")


_DIRECTIONS = frozenset(("TB", "TD", "BT", "LR", "RL"))
_DIRECTION_LINE = re.compile(r"^\s*direction\s+(\S+)\s*$", re.IGNORECASE)
_SUBGRAPH_LINE = re.compile(r"^\s*subgraph\s+([A-Za-z_][\w-]*)")
# A box's id followed by its shape's opener; `>` opens a shape only when it is no arrow's head (`A-->B`)
_SHAPED_NODE = re.compile(r"(?:^|[\s>&|-])([A-Za-z_][\w-]*)\s*(?:[\[({]|(?<![-=.])>)")
_END_ID = re.compile(r"(?<![\w-])end(?![\w-])")
# An arrow's label: one wrapped in quotes whole is read to its closing quote, since Mermaid draws a "|" inside it
# (`-->|"e- | PQ"|`, checked on Mermaid 12; read up to the first "|", a figure's frame was blocked for it, wiki run 3)
_EDGE_LABEL = re.compile(r'\|("[^"]*"|[^|]*)\|')
_QUOTED = re.compile(r'"[^"]*"')
_BARE_ID = re.compile(r"(?<![\w-])[A-Za-z_][\w-]*")
_ASYMMETRIC_BOX = re.compile(r"([A-Za-z_][\w-]*)\s*(?<![-=.])>[^\]]*\]")
# A box written `id[label]` whose label holds a bracket and no quote; `id[[..]]`, `id[(..)]` and `id[/../]` are shapes
# A `<br/>` that a new statement follows; one inside a label is followed by the label's words
_LINE_BREAK_TAG = re.compile(r"\s*<br\s*/?>\s*(?=[A-Za-z_][\w-]*\s*(?:\[|\(|\{|-->|---|-\.|==))")
_DIAGRAM_HEAD = re.compile(r"^((?:flowchart|graph)(?:\s+(?:TB|TD|BT|LR|RL))?)\s+")
# A space outside quotes where the next statement starts: what follows is an id, then its box or its arrow
_NEXT_STATEMENT = re.compile(r"(?<=[\w\]\)\}|])\s+(?=[A-Za-z_][\w-]*\s*(?:\[|\(|\{|-->|---|-\.|==))")
_ARROW_AT_LINE_END = re.compile(r"((?:-->|---|-\.->|==>)(?:\s*\|[^|\n]*\|)?)[ \t]*\n[ \t]*", re.MULTILINE)
# An empty line, then a line of 40 characters or more that ends a sentence and holds no arrow
_SENTENCE_AFTER_DIAGRAM = re.compile(r"\n[ \t]*\n(?=[^\n>]{40,}[.:!?][ \t]*(?:\n|$))")
_SPLIT_LABEL_LINES = 6
_OPEN_QUOTE_BOX = re.compile(r'\["([^"\]\n]*)\](?!")')
_QUOTED_BOX = re.compile(r'\["(.*?)"\]')
_WIDEST_QUOTED_BOX = re.compile(r'\["(.*)"\]')
_BRACKETED_BOX = re.compile(r'(?<![\w-])([A-Za-z_][\w-]*)\[(?![\[(/\\"])([^\]"\n]*[(){}][^\]"\n]*)\]')
# A label opening as a markdown list item: "2. Send" or "2) Send", and "- Send", "* Send" or "+ Send"
_NUMBERED_ITEM = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$", re.DOTALL)
_BULLET_ITEM = re.compile(r"^(\s*)[-*+]\s+(.*)$", re.DOTALL)
_UNQUOTED_EDGE_LABEL = re.compile(r'\|([^|"\n]+)\|')
_UNQUOTED_BOX_LABEL = re.compile(r'(?<=[\w-])\[(?![\[(/\\"])([^\]"\n]+)\]')
_EMPTY_BOX = re.compile(r"[A-Za-z_][\w-]*\s*(?:\(\(\s*(?:\"\s*\")?\s*\)\)|\{\{\s*(?:\"\s*\")?\s*\}\}|\[\s*(?:\"\s*\")?\s*\]|\(\s*\"\s*\"\s*\)|\{\s*(?:\"\s*\")?\s*\})")


def _match_whole_id(name: str) -> str:
    """The pattern of `name` standing as a whole id, never part of a longer one."""

    return rf"(?<![\w-]){re.escape(name)}(?![\w-])"


def _is_flowchart(source: str) -> bool:
    """Does the text, fenced or not, start with a flowchart?"""

    words = strip_fence(source).split()

    return bool(words) and words[0] in _FLOWCHART_KINDS


def _choose_node_id(label: str,
                    ids: dict[str, str],
                    taken: set[str]) -> str:
    """The id `label` already has, or the next free `n<number>`, recorded in `ids` and `taken`."""

    if label not in ids:
        number = 1
        while f"n{number}" in taken:
            number += 1
        ids[label] = f"n{number}"
        taken.add(ids[label])

    return ids[label]


def _is_link_text(before: str) -> bool:
    """Does a quote after `before` open the text of a link rather than a node?"""

    tail = before.rstrip()

    return tail.endswith(_OPEN_LINKS) and not tail.endswith(("---", "===", ".-"))


def _name_bare_nodes_in_line(line: str,
                             ids: dict[str, str],
                             taken: set[str]) -> str:
    """The line with every quoted label outside brackets, edge labels and link text written as `id["label"]`."""

    out: list[str] = []
    closers: list[str] = []
    is_edge_label = False
    index = 0
    while index < len(line):
        char = line[index]
        if closers:
            if char == closers[-1]:
                closers.pop()
            elif char in _CLOSERS:
                closers.append(_CLOSERS[char])
        elif char == "|":
            is_edge_label = not is_edge_label
        elif char in _CLOSERS and not is_edge_label:
            closers.append(_CLOSERS[char])
        elif char == '"' and not is_edge_label:
            end = line.find('"', index + 1)
            if end > index and _is_link_text("".join(out)):
                out.append(line[index:end + 1])
                index = end + 1
                continue
            if end > index:
                label = line[index + 1:end]
                out.append(f'{_choose_node_id(label, ids, taken)}["{label}"]')
                index = end + 1
                continue
        out.append(char)
        index += 1

    return "".join(out)


def _replace_quoted_line_breaks(source: str) -> str:
    """The source with every line break inside a double-quoted label written as Mermaid's `<br/>`: a model writes a
    two-line label with a real line break, which Mermaid cannot draw and which a line-by-line pass would cut in two."""

    # A quote left open would make every later line part of one label, joined by `<br/>` into one line Mermaid cannot
    # parse (2026-10-01): with an odd number of quotes nothing is joined, and the syntax check names the line
    if source.count('"') % 2:
        return source
    out: list[str] = []
    is_quoted = False
    for char in source:
        if char == '"':
            is_quoted = not is_quoted
        out.append("<br/>" if char == "\n" and is_quoted else char)

    return "".join(out)


def _map_first_box_in_group(lines: list[str]) -> dict[str, str]:
    """Each group that is also used as a box outside its own line, with the first box declared inside it."""

    groups: dict[str, str] = {}
    open_groups: list[str] = []
    for line in lines:
        group = _SUBGRAPH_LINE.match(line)
        if group:
            open_groups.append(group.group(1))
            groups.setdefault(group.group(1), "")
            continue
        if line.strip() == "end":
            if open_groups:
                open_groups.pop()
            continue
        box = _SHAPED_NODE.search(line)
        if box and open_groups and not groups.get(open_groups[-1]):
            groups[open_groups[-1]] = box.group(1)
    used = {group for group in groups
            if any(not _SUBGRAPH_LINE.match(line) and re.search(_match_whole_id(group), line)
                   for line in lines)}

    return {group: box for group, box in groups.items() if group in used and box and box != group}


def _unwrap_label(label: str) -> str:
    """An arrow's label without the pair of quotes Mermaid allows around it whole."""

    text = label.strip()

    return text[1:-1] if len(text) > 1 and text.startswith('"') and text.endswith('"') else text


def _clear_quotes(line: str) -> str:
    """The line with every quoted label's text taken out, its quotes kept."""

    return _QUOTED.sub('""', line)


def _describe_unknown_direction(line: str) -> str | None:
    direction = _DIRECTION_LINE.match(line)
    if direction and direction.group(1).upper() not in _DIRECTIONS:
        return f"Mermaid knows the directions TB, TD, BT, LR and RL only: `{line.strip()}`"

    return None


def _describe_group_as_box(line: str,
                           boxes: set[str]) -> str | None:
    group = _SUBGRAPH_LINE.match(line)
    if group and group.group(1) in boxes:
        return (f"`{group.group(1)}` is both a group and a box of this diagram, which "
                f"Mermaid refuses to draw. A group holds boxes and is never a box itself: give the group "
                f"an id no box uses, and point an arrow that ends on the group at its id alone, with no label: "
                f"`{line.strip()}`")

    return None


def _describe_quote_in_label(line: str) -> str | None:
    # A double quote inside an arrow's label (`-->|returns "OK"|`) ends the label early (2026-10-05: a published
    # diagram did not draw). A label wrapped in quotes whole (`-->|"Create Offer"|`) is Mermaid's own way of
    # writing a label with special characters in it and draws, so it is no problem (2026-10-06: it blocked five
    # frames of one batch).
    if any('"' in _unwrap_label(label) for label in _EDGE_LABEL.findall(line)):
        return f"an arrow's label `|…|` holds a double quote; write the label without quotes: `{line.strip()}`"

    return None


def _describe_bracket_in_label(line: str) -> str | None:
    # A bracket inside an arrow's label not wrapped in quotes (`-->|Prints letter (High costs)|`) ends the label at
    # the bracket, and the diagram does not draw (2026-10-08: a published diagram showed Mermaid's parse error)
    bare = [label for label in _EDGE_LABEL.findall(line)
            if not label.strip().startswith('"') and any(mark in label for mark in "()[]{}")]
    if bare:
        return (f"an arrow's label holds a bracket; wrap the whole label in double quotes, "
                f"`-->|\"{bare[0].strip()}\"|`: `{line.strip()}`")

    return None


def _describe_bracket_in_box(line: str) -> str | None:
    # A bracket inside a box's label not wrapped in quotes (`K[Info page (conditional)]`) ends the label at the bracket
    # and the diagram does not draw: a knowledge pass's flowchart reached the reader as a parse error
    found = _BRACKETED_BOX.search(line)
    if found:
        return (f"a box's label holds a bracket; wrap the whole label in double quotes, "
                f"`{found.group(1)}[\"{found.group(2).strip()}\"]`: `{line.strip()}`")

    return None


def _describe_open_quote(line: str) -> str | None:
    if line.count('"') % 2:
        return f"a label's quote is not closed: `{line.strip()}`"

    return None


def _describe_empty_box(line: str) -> str | None:
    # A box with nothing in its brackets (`S(())`, `S((""))`, `S{{}}`, `S[""]`) does not draw (2026-10-05: two
    # published diagrams opened with an empty start circle)
    if _EMPTY_BOX.search(line):
        return f"a box has an empty label; give every box a label, a start circle `S((\"Start\"))`: `{line.strip()}`"

    return None


def _describe_unclosed_bracket(line: str) -> str | None:
    # An asymmetric box `A>"Flag"]` opens with `>` and closes with `]`: that pair is taken out before counting
    outside = _ASYMMETRIC_BOX.sub(r"\1", _clear_quotes(line))
    if any(outside.count(opener) != outside.count(closer) for opener, closer in _CLOSERS.items()):
        return f"a bracket is not closed: `{line.strip()}`"

    return None


def _describe_line_break_outside(line: str) -> str | None:
    if re.search(r"<br\s*/?>", _clear_quotes(line)):
        return f"`<br/>` outside a quoted label joins two statements: `{line.strip()}`"

    return None


def _describe_box_named_end(line: str) -> str | None:
    # `end` closes a group, so a box named `end` breaks the diagram (2026-10-05: a flow's last box)
    if line.strip() != "end" and _END_ID.search(_EDGE_LABEL.sub("", _clear_quotes(line))):
        return f"`end` closes a group in Mermaid and cannot name a box; name it `End` or `finish`: `{line.strip()}`"

    return None


# The checks of one line, in order, after a group named as a box; a line is named for the first that finds a problem
_LINE_CHECKS = (_describe_unknown_direction, _describe_quote_in_label,
                _describe_bracket_in_label, _describe_bracket_in_box, _describe_open_quote, _describe_empty_box, _describe_unclosed_bracket,
                _describe_line_break_outside, _describe_box_named_end)


def strip_fence(source: str) -> str:
    """The diagram without a surrounding ```mermaid fence, if it had one."""

    stripped = _FENCE.sub("", source.strip()).strip()

    return stripped


def is_mermaid(source: str) -> bool:
    """Whether the text, fenced or not, starts with a Mermaid diagram type."""

    words = strip_fence(source).split()
    is_diagram = bool(words) and words[0] in MERMAID_KINDS

    return is_diagram


def remove_blocks(text: str) -> str:
    """The text with every ```mermaid block taken out: a diagram's labels are not the document's prose."""

    prose = _BLOCK.sub("", text)

    return prose


def name_bare_nodes(source: str) -> str:
    """A flowchart with an id for every node written as a bare label, and no line break inside a label; any other
    text as it was."""

    if not _is_flowchart(source):
        return source
    source = _replace_quoted_line_breaks(source)
    ids = {label: node_id for node_id, label in _NAMED_NODE.findall(source)}
    # Every id written outside quotes is taken, so a bare label never gets an id a box already has
    taken = set(ids.values()) | set(_BARE_ID.findall(_clear_quotes(source)))
    lines = source.split("\n")
    named = [lines[0]] + [_name_bare_nodes_in_line(line, ids, taken) for line in lines[1:]]

    return "\n".join(named)


def _join_plus_in_line(line: str) -> str:
    """The line with each " + " outside a label, a quote and an edge's text written " & "."""

    depth, is_quoted, is_edge_text, out, index = 0, False, False, [], 0
    while index < len(line):
        char = line[index]
        if char == '"':
            is_quoted = not is_quoted
        elif not is_quoted and char in "[({":
            depth += 1
        elif not is_quoted and char in "])}":
            depth = max(depth - 1, 0)
        elif not is_quoted and depth == 0 and char == "|":
            is_edge_text = not is_edge_text
        elif (not is_quoted and depth == 0 and not is_edge_text and line.startswith(" + ", index)):
            out.append(" & ")
            index += 3
            continue
        out.append(char)
        index += 1

    return "".join(out)


def join_plus_nodes(source: str) -> str:
    """A flowchart whose arrows' nodes joined by "+" are joined by "&", the one way Mermaid joins them: the wiki's
    Calvin cycle wrote `CO2["Carbon Dioxide"] + RuBP[…] --> RuBisCO`, and the published block did not render. A "+"
    inside a label, a quote or an edge's text stays. Any other text as it was."""

    if not _is_flowchart(source):
        return source

    return "\n".join(_join_plus_in_line(line) if _ARROW.search(line) else line for line in source.split("\n"))


# An arrow's label after its arrow, not wrapped in quotes, holding a bracket and no double quote
_BRACKETED_LABEL = re.compile(r'((?:--|==|-\.)[-.=>ox]*\s*)\|([^|"]*[()\[\]{}][^|"]*)\|')


def wrap_bracketed_labels_in_quotes(source: str) -> str:
    """The source with each arrow's label, and a flowchart's box label, that holds a bracket wrapped in double quotes,
    Mermaid's way to draw it: left bare, the label ends at the bracket and the diagram does not draw; a worker wrote
    `-->|3. Return Embedding [0.5, 0.7, ... 0.3]|` and the batch was blocked (froscon talk 3143, frame 28)."""

    wrapped = _BRACKETED_LABEL.sub(lambda match: f'{match.group(1)}|"{match.group(2).strip()}"|', source)
    if not _is_flowchart(wrapped):
        return wrapped

    return _BRACKETED_BOX.sub(lambda match: f'{match.group(1)}["{match.group(2).strip()}"]', wrapped)


def _drop_quotes_inside_labels(line: str) -> str:
    """An arrow's label with the quote marks inside its text taken out, the marks that wrap it whole kept."""

    def rewrite(match: re.Match) -> str:
        label = match.group(1)
        if '"' not in _unwrap_label(label):
            return match.group(0)

        return f"|{label.replace(chr(34), '').strip()}|"

    return _EDGE_LABEL.sub(rewrite, line)


def _join_split_labels(source: str) -> str:
    """A box's label written over several lines joined onto one: Mermaid ends the box at the line's end. A group of at
    most `_SPLIT_LABEL_LINES` lines, so a bracket never closed joins nothing."""

    lines = source.split("\n")
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        end = index
        while line.count("[") > line.count("]") and end + 1 < len(lines) and end - index < _SPLIT_LABEL_LINES:
            end += 1
            line = f"{line} {lines[end].strip()}"
        is_closed = line.count("[") <= line.count("]")
        joined.append(line if is_closed else lines[index])
        index = end + 1 if is_closed else index + 1

    return "\n".join(joined)


def _split_statements(source: str) -> str:
    """A flowchart's statements each on a line of its own, as Mermaid needs them: run together on one line, joined by a
    `<br/>` outside any quote, or with an arrow's target left to the next line."""

    text = _LINE_BREAK_TAG.sub("\n", source)
    if "\n" not in text.strip():
        pieces = re.split(r'("[^"]*")', _DIAGRAM_HEAD.sub(lambda match: f"{match.group(1)}\n", text.strip(), count=1))
        text = "".join(piece if index % 2 else _NEXT_STATEMENT.sub("\n", piece) for index, piece in enumerate(pieces))

    return _ARROW_AT_LINE_END.sub(lambda match: f"{match.group(1)} ", text)


def _close_open_quotes(line: str) -> str:
    """A box's label opened with a quote and ended at its bracket gets its closing quote."""

    return _OPEN_QUOTE_BOX.sub(lambda match: f'["{match.group(1).rstrip()}"]', line)


def _drop_quotes_inside_boxes(line: str) -> str:
    """A box's quoted label with the quote marks inside its text taken out, the marks that wrap it whole kept."""

    def rewrite(match: re.Match) -> str:
        label = match.group(1)
        if '"' not in label or "[" in label or "-->" in label:
            return match.group(0)

        return f'["{label.replace(chr(34), "")}"]'

    return _QUOTED_BOX.sub(rewrite, line)


def _drop_quotes_inside_bracketed_box(line: str) -> str:
    """A box's quoted label that holds brackets and a stray quote (`Q["Queue (Accounts [Topic"], Cards [Topic])"]`)
    read from its `["` to the line's last `"]`, its inner quote marks taken out: the non-greedy reading stopped at the
    first `"]` and left the quote open (DEFECTS 716, a blocked batch). Only on a line with one `["` and an odd number of
    quotes, so two boxes on one line are never joined."""

    if line.count('"') % 2 == 0 or line.count('["') != 1:
        return line

    return _WIDEST_QUOTED_BOX.sub(lambda match: f'["{match.group(1).replace(chr(34), "")}"]', line)


def _fill_empty_boxes(line: str) -> str:
    """A box with nothing in its brackets labelled with its own id: Mermaid draws no empty box."""

    def rewrite(match: re.Match) -> str:
        box = match.group(0)
        box_id = re.match(r"[A-Za-z_][\w-]*", box).group(0)
        shape = box[len(box_id):].strip()
        opener = shape[:2] if shape[:2] in ("((", "{{") else shape[0]
        closer = "".join(_CLOSERS[mark] for mark in reversed(opener))

        return f'{box_id}{opener}"{box_id}"{closer}'

    return _EMPTY_BOX.sub(rewrite, line)


def _rename_boxes_named_end(line: str) -> str:
    """`end` as a box's id, outside quotes, written `End`; a line that is only `end` closes a group and stays."""

    if line.strip() == "end":
        return line
    pieces = re.split(r'("[^"]*")', line)

    return "".join(piece if piece.startswith('"') else _END_ID.sub("End", piece) for piece in pieces)


def _rewrite_list_label(label: str) -> str:
    """A label opening as a markdown list item written so Mermaid draws its words: "2. Send" as "(2) Send", "- Send"
    as "Send". Mermaid reads such a label as a list and draws "Unsupported markdown: list" in its place."""

    numbered = _NUMBERED_ITEM.match(label)
    if numbered:
        return f"{numbered.group(1)}({numbered.group(2)}) {numbered.group(3)}"
    bulleted = _BULLET_ITEM.match(label)

    return f"{bulleted.group(1)}{bulleted.group(2)}" if bulleted else label


def _rewrite_list_labels(line: str) -> str:
    """Every label of the line, quoted, between an arrow's bars or in a box's brackets, opening as a list item, written
    so Mermaid draws it (a flowchart's arrows labelled "2. generate embedding" reached the reader as errors)."""

    line = _QUOTED.sub(lambda match: f'"{_rewrite_list_label(match.group(0)[1:-1])}"', line)
    line = _UNQUOTED_EDGE_LABEL.sub(lambda match: f"|{_rewrite_list_label(match.group(1))}|", line)

    return _UNQUOTED_BOX_LABEL.sub(lambda match: f"[{_rewrite_list_label(match.group(1))}]", line)


def repair_diagram(source: str) -> str:
    """The diagram with every common mistake the program can mend, mended, so no worker spends a call and no reader gets
    a parse error for it: a bracket in a label, a quote inside an arrow's label, an empty box, a box named `end`, a
    direction Mermaid does not know, bare labels with no id, `+` joining boxes, a group named as a box. A new mistake
    goes here first, with a test; only what cannot be mended for certain is left to `find_syntax_problems` to refuse."""

    source = take_wrapped_diagram(source)
    if not is_mermaid(source):
        return source
    wrapped = wrap_bracketed_labels_in_quotes(_join_split_labels(_split_statements(source)) if _is_flowchart(source) else source)
    if not _is_flowchart(wrapped):
        return wrapped
    lines = []
    for line in wrapped.splitlines():
        direction = _DIRECTION_LINE.match(line)
        if direction and direction.group(1).upper() not in _DIRECTIONS:
            continue
        mended = _drop_quotes_inside_bracketed_box(
            _drop_quotes_inside_boxes(_close_open_quotes(_drop_quotes_inside_labels(line))))
        if direction:
            mended = f"{line[:len(line) - len(line.lstrip())]}direction {direction.group(1).upper()}"
        lines.append(_rewrite_list_labels(_rename_boxes_named_end(_fill_empty_boxes(mended))))
    joined = "\n".join(lines) + ("\n" if wrapped.endswith("\n") else "")

    return repair_group_used_as_box(name_bare_nodes(join_plus_nodes(joined)))


def take_wrapped_diagram(source: str) -> str:
    """The diagram a worker wrapped in other lines, alone: a fence opened, a label such as "(flowchart TD)", a second
    fence holding the real diagram, then a note and a closing fence. Three frames' notes were refused that way and
    blocked their batch (DEFECTS 701). Taken only when a fence comes before the line that starts with a Mermaid type, up
    to the next fence; any other text stands as it is."""

    if is_mermaid(source):
        return source
    lines = source.strip().splitlines()
    for start, line in enumerate(lines):
        words = line.split()
        if not words or words[0] not in MERMAID_KINDS:
            continue
        if not any(earlier.lstrip().startswith("```") for earlier in lines[:start]):
            return source
        end = next((index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith("```")),
                   len(lines))
        return "\n".join(lines[start:end]).rstrip()

    return source


def repair_fenced_blocks(text: str) -> str:
    """A document's text with each fenced Mermaid block mended (`repair_diagram`), the rest as it stands."""

    return _BLOCK.sub(lambda match: f"```mermaid\n{repair_diagram(strip_fence(match.group(0)))}\n```", text)


def open_prose_fences(text: str) -> str:
    """A finished document's text with a Mermaid fence holding no diagram, a sentence marked as Mermaid, written as the
    text it holds, for its reader: a worker's edit holding one is refused, so it can write it again."""

    def open_fence(match: re.Match) -> str:
        source = strip_fence(match.group(0))
        if not is_mermaid(source):
            return source
        # A diagram whose fence was never closed runs on into the document's next sentence: the fence ends before it
        diagram, *sentence = _SENTENCE_AFTER_DIAGRAM.split(source, maxsplit=1)

        return f"```mermaid\n{diagram.rstrip()}\n```\n\n{sentence[0].lstrip()}" if sentence else match.group(0)

    return _BLOCK.sub(open_fence, text)


def list_fenced_problems(text: str) -> list[str]:
    """What Mermaid cannot draw in each fenced Mermaid block of a document's text."""

    return [problem for match in _BLOCK.finditer(text) for problem in find_syntax_problems(match.group(0))]


def repair_group_used_as_box(source: str) -> str:
    """A flowchart in which no group is also a box: an arrow pointing at a group keeps pointing at it, by its id alone,
    and the label the arrow gave the group is dropped, since the label makes the id a box too. Mermaid's flowchart
    draws an arrow to a group; moving it to the first box inside drew an input feeding one unit of a processor when the
    slide's arrow ended on the processor (2026-10-13). Any other text as it was."""

    if not _is_flowchart(source):
        return source
    lines = source.split("\n")
    inner = _map_first_box_in_group(lines)
    if not inner:
        return source
    repaired = []
    for line in lines:
        if _SUBGRAPH_LINE.match(line):
            repaired.append(line)
            continue
        for group, box in inner.items():
            # `A -->|x| G_target["Target"]` becomes `A -->|x| G_target`: the group's own line keeps its label
            line = re.sub(rf"(?<![\w-]){re.escape(group)}\s*\[[^\]]*\]", group, line)
        repaired.append(line)

    return "\n".join(repaired)


def find_syntax_problems(source: str) -> list[str]:
    """Each flowchart line that Mermaid cannot parse for a reason found without Mermaid: a quote left open, a bracket
    not closed, `<br/>` outside a quoted label, which joins two statements on one line, a direction it does not know,
    or a group named as a box of the same diagram. Empty for other kinds."""

    if not _is_flowchart(source):
        return []
    lines = strip_fence(source).splitlines()
    # A box drawn with a shape (`B["Billing"]`) and a group of the same name cannot both be drawn: Mermaid would make
    # the box its own parent; an arrow to a group by its bare name is fine
    boxes = {match.group(1) for line in lines[1:] if not _SUBGRAPH_LINE.match(line)
             for match in _SHAPED_NODE.finditer(_clear_quotes(line))}
    problems = []
    for number, line in enumerate(lines[1:], start=2):
        message = _describe_group_as_box(line, boxes) or next((found for check in _LINE_CHECKS if (found := check(line)) is not None), None)
        if message is not None:
            problems.append(f"line {number}: {message}")

    return problems


def is_arrowless_flowchart(source: str) -> bool:
    """Whether the text is a flowchart that links none of its boxes."""

    return _is_flowchart(source) and count_arrows(source) == 0


def count_arrows(diagram: str) -> int:
    """How many arrows a Mermaid diagram draws: a flowchart's links, a sequence diagram's messages, or a class or ER
    diagram's relations."""

    lines = strip_fence(diagram).splitlines()
    kind = lines[0].split()[0] if lines and lines[0].split() else ""
    if kind in _RELATION_KINDS:
        return sum(1 for line in lines[1:] if _RELATION_LINE.search(line))
    arrow = _SEQUENCE_ARROW if kind == "sequenceDiagram" else _ARROW

    return len(arrow.findall(diagram))
