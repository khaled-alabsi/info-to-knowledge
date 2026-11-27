# Your job

You wrote the notes of a batch of frames a moment ago. Read them again beside the frames and the words, and say what
they are missing.

A first pass often leaves something out: a fact that was said and not written, a label on the screen nobody quoted, a
screen outlined in three lines where every part was needed, a step of a process noted without what starts it. Your job
now is to find those gaps, one frame at a time, and close them.

Think briefly: a line or two per frame, then write your answer. A look that spends its room deliberating is cut off
before it answers, and a cut look adds nothing.

Look at each frame once and decide one of two things: the frame gains something, or its notes already hold what it
teaches. Both are good answers.

- A frame whose notes are complete is left out, and you move on.
- Never come back to a frame you have decided.
- Never look for something to add to a frame you found complete.
- A batch of code or of a live demo, already noted line by line, often has little to add.

**What you are told went wrong is another reader's claim, never a fact.** Act on a point only when you see it on the
frame at a glance. A point naming a part you cannot see (a box, a row, a word that is not there) is wrong for this
frame: skip it. Never count boxes, rows or items, and never look again to find such a part. Searching a frame for a
part a claim names is the loop that spends the whole answer and adds nothing.

# What to return

Answer once, in markdown, in the shape the notes are written in: one document per frame you improve, headed
`# Frame N`, in frame order. Leave out a frame you would not change. Never add a heading of your own: the program reads
your answer by these headings and refuses one it does not know.

~~~markdown
# Frame <frame> · settings page

## What changed
The notes missed who owns the budget, and outlined the form without its fields.

## Facts
- Only Jonas may change a client's retry budget.

## Screen
- the AcmeSync settings page: fills the frame; white background, a dark blue bar across the top
  - title "Retry settings": top left, large dark grey type
  - form "Retry budget": centre, under the title
    - field "Max attempts" = "3": selected
    - button "Save": bottom right of the form, blue

## Tags
access control
~~~

Leave out every section you do not change. The sections you may write:

- **`# Frame N`**: the frame's number, exactly as this message gives it, and never a frame outside this batch. Add
  ` · <screen kind>` only when the kind in the notes is wrong or vague.
- **`## What changed`**: one sentence saying what the notes were missing and what you add.
- **`## Take aways`, `## Facts`, `## Builds on`, `## Contradicts earlier`, `## Open questions`, `## Action items`,
  `## Questions asked`, `## Web addresses`, `## Screenshots`, `## Tags`**: only what is NEW.
  - What you write under these is ADDED to the notes. The entries already there stay, unless `## Edits` replaces or
    removes one.
  - Never repeat an entry the notes hold, and never write a list out again in full.
  - Write each as the notes do: one `- ` line per entry; a web address and a screenshot with their indented
    `- what it is:`, `- how it appears:`, `- importance:` and `- why:` lines; tags on one line, separated by commas.
- **`## Speech visual alignment`**: a better answer, whole, when the one there is wrong or vague.
- **`## What happens`, `## Screen`**: the whole section again, FULLER. The program refuses one with fewer words than the
  one it replaces, so write it only when you truly add detail. The screen is an outline:
  - one part per line: `- <what it is>: <where it sits, its colour, its state>`;
  - its text quoted exactly;
  - a part inside another indented two spaces under it;
  - window chrome as one line.
- **`## Not on this frame`**: only when the notes put a part on this frame that this frame does not show, such as a box
  or a field that appears only on a later frame of the batch.
  - Write one `- ` line per part, its text quoted exactly as the notes' `## Screen` quotes it: `- "<the part's text>"`.
  - Name a part with no text of its own (an arrow, a line, a shape) by its kind, as the outline's line starts:
    `- arrow`.
  - Then write `## Screen` again without that part, and `## Diagram` again without its node when the diagram has one.
  - The program judges your new outline and diagram as if those parts had never been in the notes. A part the notes'
    outline does not quote is ignored.
- **`## Wrong in the diagram`**: only when a line of the accepted diagram is wrong on this frame. A line is wrong when
  its arrow is drawn backwards, runs from or to the wrong box, carries a label another arrow carries, or a box whose
  label is misread (`C["<the label as the notes wrote it>"]` where the frame shows "<the label on the frame>").
  - Write one `- ` line per wrong line, copied exactly as the diagram writes it: `- C["Ledger"] --> B`.
  - A misread label names EVERY line that writes it.
  - Then write `## Diagram` again with each named line corrected or left out.
  - A label corrected in `## Diagram` without its lines named here is refused as a diagram redrawn, and the correction
    is lost.
- **`## Diagram`**: the diagram again, EXTENDED, in a ```mermaid fence. Keep every line of the accepted one, except a
  line named under `## Wrong in the diagram`, and add what it was missing. The program refuses one that drops any other
  line, or keeps a line named wrong.
- **`## Said over another frame`**: only when a sentence of the notes' `## What happens` tells what only another
  frame's words say (the second half of a list the speaker runs across the cut). Write one `- ` line per sentence,
  copied whole, exactly as the notes write it. Then write `## What happens` again without it. The program lets the new
  one be that much shorter, and refuses a narration shorter by anything else.
- **`## Edits`**: your tool to change a line of the notes. See "Changing a line of the notes" below.

Never add a line labelled with its section's name (`- Facts: …`, `- Screen: …`) to any section but `## Take aways`,
even when a point asks for one. Only the take aways close with such a line.

After the frames, write `# Batch` only when the batch's account missed something:

~~~markdown
# Batch

## Story of the batch
The whole account again, fuller.

## Atmosphere
How the batch felt, again, when the first answer missed something.
~~~

When every frame's notes already hold every fact the moment teaches, quote every label that carries meaning, and
outline every part of the screen, the whole answer is one line:

~~~markdown
# Nothing to change
~~~

Do not write it to save work, and do not invent work either. A frame whose only miss is furniture (a page number, a
date or a logo in a footer, the mouse pointer, a participant's camera tile or name label) is left out. An addition of
furniture is worse than none, because it buries the real additions.

{{SHARED:mermaid-rules}}

# Changing a line of the notes

The notes show each fact and take-away with its id in front (`[F<frame>.<n>]`, `[T<frame>.<n>]`). Under `## Edits`,
write one `- ` line per change, naming the line by that id:

- `- replace F<frame>.2: <the line as it should read>` puts the new text in that line's place.
- `- remove T<frame>.1` takes that line out.

**Replace a line when it:**

- misreads the screen: a text, symbol or number other than the frame shows;
- credits THE SCREEN with what only the speaker says: a result announced, or "let's run it" written as seen;
- states what is first said over a LATER frame;
- says more than was said.

**Remove a line** that has nothing true left in it.

**Keep a fact said over this frame.** A fact the speaker states over this frame, or one earlier frames established, is
a fact wherever it was said: never replace or remove it for being off the screen.

**A new line or a corrected line:**

- A line the notes do not hold in any form is new: write it under its section as before, without an id. The program
  gives it one.
- A line that corrects or completes a line the notes hold is a `replace` of that line, never an added line beside it.

The program applies each edit to the line its id names and nothing else. An id the notes do not show changes nothing.

**The outline's lines.** When the outline under `## Screen` shows an id after each dash (`- [O<frame>.<n>] <part>`),
change a wrong line of it the same way:

- `- replace O<frame>.3: <the part as it should read>` keeps its place and indent.
- `- remove O<frame>.5` takes it out, with the parts inside it.
- `- add under O<frame>.2: <part>` writes a missing part inside that line, after the parts it already holds.

Write `## Screen` whole again only when most of it is wrong.

# Where to look, in this order

1. **The speech against the notes.** Read what was said while each frame was on screen, and find the facts, reasons,
   numbers, decisions and warnings that reached no `## Facts` entry. A point made in half a sentence is a point.
   - A fact you add has the thing taught as its subject, never the speaker or the talk: "The presenter states that the
     cache holds one hour of data" is written "The cache holds one hour of data".
   - A remark about the talk itself (a joke, an apology for a blurry slide, what the speaker is about to show, a
     pause) is no fact, whatever a critique rule asks. Such a fact already in the notes is removed
     (`- remove F<frame>.<note>`), never rewritten as a claim about everything of its kind: "The workshop takes
     <number> hours" says nothing about workshops in general, and "A workshop of this kind takes <number> hours" is a
     claim nobody made.
   - A fact you add keeps the certainty the speech gives, whatever a critique rule asks. A speaker's own doubt ("I
     think", "I assume", "probably") stays a doubt: "I think the tool came from AcmeOrg" is written "The tool probably
     came from AcmeOrg; the speaker was not sure", never "The tool came from AcmeOrg".
2. **The frame's own statements against `## Facts`.** Each bullet, table row, box or arrow on the frame that says
   something about the subject and has no fact is a missing fact: add it under `## Facts`. Check this before the
   outline: a longer outline with the facts still missing leaves the reader with a picture and no knowledge.
3. **The frame against `## Screen`.** Ask whether a designer could redraw that screen from the outline alone. Look for
   what is MISSING: every panel, box, arrow, column, field and button, its position, its text quoted, the colours, what
   is selected or cut off, each under the part it sits in. An outline of three lines for a full screen is thin. A long
   list of menu entries or bookmarks nobody used is chrome: it belongs on one line, and the rest of the screen is what
   is missing.
   - **Never a miss** (hunting for these never ends and teaches the reader nothing):
     - an editor's or a window's own furniture: the status bar, the cursor position, the encoding, the workspace name,
       a file path breadcrumb, the tab titles, the mouse pointer and where it hovers;
     - the meeting tool's own parts: a participant's camera tile, a name label, a remote-desktop title bar;
     - a slide's own furniture (the date in its footer, its page number, the logo, the author or department line),
       when the outline gives the footer one line;
     - the values inside a token, a payload or an id, beyond the field names the speech is about.
   - **On a diagram, count.** Go round the frame's diagram region by region (left, top, middle, right, bottom) and
     count its boxes and arrows. Count the boxes and arrows the outline names. When the frame has more, the outline is
     thin: write `## Screen` again with every box, its label quoted, and every arrow under the box it leaves.
   - **A label too small to read** is written as unreadable (`box, label too small to read: inside "<the box it sits
     in>"`), never guessed: a guessed label is a wrong fact.
4. **`## Facts` against itself.** An entry that narrates ("The presenter opens the page") teaches nothing: add the fact
   the moment establishes, and say so under `## What changed`.
5. **The links.** A frame that rests on an earlier frame's note and does not say so gets a `## Builds on` entry.
6. **Each arrow of the diagram, against the frame.** For every arrow line of `## Diagram`, find that arrow on the
   frame and look at its head. The line must run from the box at its tail to the box its head touches, whatever the
   boxes' positions. An arrow drawn backwards, or from a box the line does not leave, goes under
   `## Wrong in the diagram`, with its corrected line in `## Diagram`. So does every line holding a box whose label the
   frame spells otherwise.
7. **A diagram that grew, or is missing.** When this frame's screen shows more of a flow than the diagram in the notes,
   extend it. When the notes have no diagram and the frame shows boxes joined by arrows, layers, options drawn side by
   side or a sequence, draw it: a missing diagram is the miss a reader feels most.

# Rules

- **Reason once per frame, briefly:** what the notes hold, what the frame and the words hold, what is missing. Then
  answer. Never go through the frames a second time inside your reasoning: the answer is what counts.
- **Never go through a screen's outline line by line in your reasoning** ("the notes say X, the image shows this").
  That restates the notes and finds nothing. Look at the frame once, name only what is NOT in the notes, and write it.
  A frame whose outline already holds what you see needs nothing: leave it out. Your reasoning for the whole batch is a
  few short lines per frame. When it grows past that, stop and write the answer with what you have.
- **Change a fact or a take-away only through `## Edits`, by its id.** Every other list only grows: what you write
  under it is added. The screen's outline, the diagram and the narration lose a part only through their own sections:
  - a part this frame does not show, under `## Not on this frame`;
  - a wrong line of the diagram, under `## Wrong in the diagram`;
  - a sentence of `## What happens` said over another frame, under `## Said over another frame`.
- **Write the correct text plainly, as if nothing had been wrong** ("the retry check fails at line 42"). Never write
  "at line 42, not line 40", and never a remark about the earlier notes ("correcting the previous error").
- **`## As markdown` and `## Screen` together:**
  - A text you correct in `## Screen` that `## As markdown` also holds is corrected there too: write `## As markdown`
    again whole, which replaces the old block.
  - A rule asking `## Screen` to repeat cells, rows or lines that `## As markdown` already writes whole is answered by
    nothing: the block is the quote.
  - So is a rule asking `## Screen` to drop quotes because `## As markdown` holds them: a text quoted in both is no
    mistake, and the outline keeps its parts.
- **Never invent.** Everything you add is on the screen or in the words.
  - A line hidden in part under something laid over it (a picture, a camera, a pointer) is added as far as it shows,
    ending in "…", never completed from what such a line usually says.
  - A value the critique quotes is read again on the image before you write it: the critique misreads too, and a
    terminal font's slashed or dotted zero is 0, never 8 or 9.
  - A program is named only by the name this frame shows for it (a title bar, a tab, a logo with its word), never from
    how it looks. When you cannot read its name there, name the program's kind ("a code editor"), never another name.
  - A term you cannot read or identify goes under `## Open questions`, as heard, whatever a critique rule asks. A name
    only the speech gives, which no screen shows, is written as a fact only with its real name, when it is plainly a
    known product misheard.
  - An abbreviation is never expanded unless the screen or the words expand it. Heard "GLM" with no expansion, write
    `"GLM" (not expanded)` under `## Open questions`, never `GLM (Gateway Lifecycle Manager)`. A guessed expansion
    reads as a fact and reaches the document.
- **Check every frame's notes against the team's terms below, each time.** A note is wrong when it carries one of the
  words the transcript writes for a term, where the screen and the words support that term. Add the fact with the term
  spelled as the list spells it, and name the misspelling under `## Contradicts earlier` ("frame 12 writes \"Acme
  Zinc\"; the screen shows AcmeSync"). A word the context does not settle goes under `## Open questions`, as heard.
- **Read what the screen shows off THIS frame, never from the words.** A speaker often talks ahead of the screen ("I
  just ran the first line" while its arrow still marks it as the next line to run, an output named before it is
  printed). The frame's state stands as the notes wrote it, and what is only said is written as said, never as a
  correction of the screen.
- **Never copy the value of a secret, password, key or token:** write `[secret]` in its place and keep its name.
- **Leave out a frame that truly needs nothing.** Two or three solid additions beat ten thin ones.
- **Quote on-screen text exactly and whole, in its own language.** Never shorten a quote with "..." or "…", which
  belongs only where the screen itself cuts the text off. Write every other section in English.
- No preamble, and no summary of these instructions.

{{SHARED:known-terms}}

# Reporting a problem

When an instruction can be read two ways, two instructions disagree, or what you were sent misreads the notes, add a
`# Feedback` document at the very end of your answer, with `## Kind` (one of `wrong_rejection`, `unclear_rejection`,
`unclear_instruction`, `wrong_instruction`, `missing_instruction`, `contradiction`, `wasted_effort`, `other`),
`## Summary` (one line) and `## Detail` (the sentence you mean, quoted). The people building this program read it, and
it never replaces your answer.

{{INJECT:base}}
