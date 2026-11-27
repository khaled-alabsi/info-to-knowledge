# Your job

A moment ago you wrote the notes of a batch of frames. Read them again beside the frames and the words, and name what
went WRONG in them, as rules.

You fix nothing here. A later message hands you these rules and asks you to fix the notes. This message asks only what
the mistakes were, and nothing you write now changes a frame's notes.

Below are the nine mistakes a first pass makes most often. Your answer opens with one line for each of them under
`## Checked` (see "What to return"): its number, its name, and the frames of this batch where you saw it, or "none".
Look at the frames once and write each line once. A line you have written is settled: go on to the next one.

1. A fact said and never written.
2. A label on the screen nobody quoted.
3. A screen outlined in three lines where every part was needed.
4. A step of a process noted without what starts it.
5. An English meaning beside a term in another language that says something else than the term (a word's opposite,
   "careless" for a word meaning "uncompromising").
6. A diagram that runs otherwise than the frame's own arrows: an arrow kept from the frame before without checking its
   head on this frame, a box of another branch placed in the path, or a step drawn after one it comes before. Follow
   the frame's arrows from its first box and compare each step of the notes' diagram with them.
7. A fact saying the screen shows a value or a result that only the speaker says (an output not printed yet, a
   variable panel still empty).
8. This screen's parts given a meaning that only the next slide supports, because the speaker named the next slide's
   subject while this one was showing. Check this on every frame where the speaker talks ahead of the screen. It is
   the mistake a first pass makes most, and the fix can take a wrong fact out only after you have named it.
9. A joke or an ironic remark written as a fact about the subject, where the screen or the rest of the speech shows
   otherwise ("the server is famous for running at zero load" kept as a fact, while its chart shows it full).

A mistake that fits none of the nine gets a rule of its own. Do not force it into one of the nine.

These are no mistakes, so never name them:

- a value the frame shows;
- a value that follows from what the frame shows, such as an output beside the code that printed it;
- a value the speaker says, written as said;
- any fact the speech alone gives, written as a fact about the subject without claiming the screen shows it. The
  notes' facts come from the frame and the speech together.

Name only the kinds of mistake these notes actually show.

Think briefly: a line or two, then write your answer. A critique that spends its room deliberating is cut off before it
answers, and a cut critique teaches nothing.

**Look at each frame once, in order, then answer.** Once you have looked at a frame, it is done:

- Never go back to look at it again.
- Never check a table row by row, or a value letter by letter.
- Never count boxes, rows or items, and never look again to confirm a count or a letter. "Wait, let me look again" is
  the loop that spends the whole answer and returns no critique.
- A mistake you cannot see at a glance is not one to report here.

After you look at the last frame of the batch, write `# Critique` at once. When nothing in the notes is wrong, write
`# Nothing to criticise` at that moment. Going over right notes a second time finds nothing, and it spends the room
until the answer is cut off empty.

# What a rule is

**Write each mistake once, as a rule that holds for ANY meeting.** Every later run of this program is given these
rules as instructions. Nobody reading them later has this meeting in front of them, so a rule about this meeting is
worthless.

- **Words that begin in the middle of a sentence finish the sentence of the frame before.** Read a frame's words
  together with the end of the words before them. "...we're not going" followed by "to include the 0" is one sentence:
  the 0 is left out. It is never a speaker saying the 0 is included. Notes that read the joined sentence right made
  no mistake. What the joined sentence says belongs to both frames: after "So it goes" and then "to the Gateway and
  fetches the token", the first frame's notes may say the token is fetched.
- **A mistake is a gap against the frame, the words, or a rule your instructions state.** A form the instructions do
  not ask for is no mistake:
  - Only `## Take aways` closes with a line labelled with its name. A rule asking every section for such a line adds
    noise to every frame.
  - A take-away ending `→ nothing on screen` is the form for words about no part of the screen. It never claims the
    screen is empty.
  - The closing `- Take aways:` line sums the frame up. It has no quote and no arrow, and needs neither.
- **A rule names no frame and no number of this batch in its own lines.** The frames go on the `Seen on` line, which
  is there for them.
- **A rule quotes no word from these screens in its own lines.** Name the KIND of text that was missed, such as "the row
  labels of a table" or "the button a step is started from", never the words this meeting showed.
- **Write one rule per kind of mistake, not one per frame.** The same mistake on four frames is one rule with four
  frame numbers on its `Seen on` line.
- **Write only what these notes show.** A mistake you can imagine but did not find here is no rule: leave it out.
- **Write only a mistake that costs a reader knowledge**: a fact, a quoted text, a step, a box or an arrow, a number.
  A colour, a font, a position, an overlay's exact place, a copyright sign or a footer is form, never a rule.
- **A rule never asks the notes to hold less than they must.** The notes quote every text on the screen and state
  every fact the frame shows, whether anyone spoke about it or not. Writing down a table nobody read aloud is no
  mistake. A rule saying to leave such text out goes against the instructions the notes were written from.
- **A rule never changes the form the instructions set.** The headings, the line each section opens or closes with,
  and the shape of an entry are what the instructions require. Notes that follow them made no mistake. A rule against
  that form sends the repair after it, and the repair can only add a second copy. The cases where this matters most:
  - **A slide built up step by step:** a later step outlines only what that step adds, after the line its message
    gives ("- step <k> of the screen of frame <first>: ...", with no count of steps). That is the form for a step,
    not a screen left half outlined.
  - **A screen shown again:** the one line "- same as frame N" is its form when its message allowed it, not a screen
    left without an outline. Only a frame the speech list marks "repeats frame N" may use that line. Every other frame
    is outlined whole, however alike it looks; the program refuses the line there, so a rule asking such a frame to
    point to another is wrong.
  - **A frame showing only part of an earlier screen** (a close-up, a camera shot of the projection): its outline holds
    only the parts this frame shows. An outline naming parts this frame does not show is the mistake to report.
  - **A fact in its own words:** a fact may say a screen's text in its own words. The exact quotes belong in the
    screen's outline.
  - **Text written under `## As markdown`** counts as quoted. A table, a list or code written there whole needs no
    second copy under `## Screen`, so a rule asking the outline to repeat its cells, rows or lines is wrong: a second
    copy is typed again from the frame and carries misreads the block did not have.
  - **Text quoted both under `## As markdown` and under `## Screen`** is no mistake either: the second copy costs a
    reader nothing, so a rule asking the outline to drop its quotes is wrong.
  - **A name only the speech gives** (no screen shows it) follows the notes' own instructions: written with its real
    name when it is plainly a known product misheard, otherwise under `## Open questions` as heard. A rule asking for
    it in the facts as the transcript writes it is wrong, since the transcript garbles names.

# What to return

Answer once, in markdown: `# Critique`, then `## Checked` with one line per mistake of the numbered list, then one
`## <name>` section per rule. Never add a heading of your own: the program reads your answer by these headings and
refuses one it does not know. Every mistake for which `## Checked` names frames is a rule below it. When every line of
`## Checked` says "none" and you found no other mistake, the answer is `# Nothing to criticise`.

~~~markdown
# Critique

## Checked
- 1 a fact said and never written: none
- 2 a label on the screen nobody quoted: none
- 3 a screen outlined in three lines: frame <frame>
- 4 a step without what starts it: none
- 5 a wrong English meaning: none
- 6 a diagram running otherwise than the frame's arrows: frames <frame>, <frame>
- 7 a value only the speaker says: none
- 8 a meaning only the next slide supports: none
- 9 a joke written as a fact: none

## An arrow is kept from the frame before without checking its head
What it is: the newest diagram is handed over in full, and a frame that only adds a box keeps an arrow whose direction
this frame's own picture contradicts.
How to avoid it: read every arrow's head on the frame in front of you before you keep it, and drop the ones it does
not show.
Seen on: frames <frame>, <frame>
Where: frame <frame>: the diagram line `A["Gateway"] --> B["AcmeSync"]`, whose head the frame draws at the Gateway
Where: frame <frame>: the diagram line `B --> C["Jonas's queue"]`, drawn on a frame that shows no arrow there

## A table is outlined without its row labels
What it is: the outline names the table and its columns and stops there, so a reader cannot tell which row a value
belongs to.
How to avoid it: quote every row's label, in the order the rows stand, as part of the table's own outline.
Seen on: frame <frame>
Where: frame <frame>: the `## Screen` line "- table: columns Region, Limit" and its rows
~~~

Each part of a rule:

- **The name** is the mistake in one line, in the present tense, like the two names above.
- **What it is** says what goes wrong and why it is easy to do.
- **How to avoid it** is an instruction a worker can follow on its next frame.
- **Seen on** names the frames of THIS batch, by the numbers this message gave you.
- **Where** is one line per frame of `Seen on`: the note the mistake is in, quoted from your notes or named by its
  section, so the fix that follows finds it without searching. It is the one place where the meeting's own words may
  stand; the rule's other lines stay free of them.

Write `# Nothing to criticise` only when these notes truly hold no mistake of any kind. That is a real answer, and a
better one than a rule you had to reach for.

{{SHARED:known-terms}}

{{SHARED:worker-feedback}}

{{INJECT:base}}
