# Your job

You wrote the notes of a batch of frames, and then the account of the whole batch. Read them as someone who did not
attend the meeting and cannot see the screen, and list what that reader still cannot know.

# What counts as an open question

- **A term, a name or an abbreviation** used and never explained.
- **A number, a date or an owner** given with no source or no unit. A date is never a question because of when you
  think today is: you are not told when the meeting took place, and your own sense of the current year is not the
  meeting's. A date is a question only when the meeting's own dates disagree.
- **A decision, a step or a rule** mentioned without its reason, or without who decides.
- **Something the presenter pointed at** that the notes do not identify.
- **Garbled speech** where the notes do not say what the speaker was explaining there.
  - Words that make no sense in the sentence, or words of another language in the middle of it, are mistakes of the
    automatic transcript. When the notes hold what that part of the talk was about, they are no question, and a
    question never quotes them.
  - A sentence cut off at the end of one frame's words is no question: the next frame's words go on with it, and the
    message shows the start of what is said right after the batch.
  - A step of a demonstration whose result the next frames show (the speaker says "let's run it") is no question
    either: the meeting goes on after this batch.
  - The transcript does not mark who speaks, so words that seem to come from someone else may be the same sentence
    going on: read them as its end.
- **A screen and the speech that disagree about the subject:** they state different numbers, rules or names for the
  same thing. These are no disagreement:
  - a speaker announcing what comes next while the screen still shows the step before: that is the order of a demo;
  - two spellings of one name ("Jonas" on the slide, "Jonnas" in the speech): the speech misheard it, and the frame's
    spelling holds.

# What is never an open question

A reader needs what the meeting teaches. These teach nothing, so they are never questions:

- **How the recording was made:** what the presenter did or will do on screen, such as whether a window was switched,
  what was clicked, scrolled or pointed at, which file or tool they open next, how they go on after an error on
  screen, or why a word was said while another program was open. A question the notes list of this kind is left out,
  even though the notes asked it.
- **The exact characters of a text on the screen:** a quote's punctuation, an escape sequence, a letter the frame shows
  too small to read, or the line number an editor shows for a line of code. Where the frame cannot be read, the notes
  say so. A reader loses nothing the meeting teaches, and settling it means reading a frame letter by letter.
- **A detail of a thing on the screen that the talk never makes its subject:** a part's number, a case's or a cable's
  model, a firmware version, or a value nobody mentions ("which model is the enclosure in the photo?"). Nobody in the
  meeting said or was asked about these. Only what the talk raises and leaves unsettled is a question ("the speaker
  did not know whether the order limit applies to resellers").
- **A slip of your own account or notes**, such as a count the account gives that the frames' notes do not hold ("the
  account says 12 rows, the table lists 9"). A reader asks about the meeting, never about your summary of it, and the
  notes and the frames already settle it.

# What to return

Answer once, in markdown, in this shape and no other:

~~~markdown
# Batch

## Considered
- who owns the retry budget after the migration: question
- "Jonnas" in the speech: no, the slide spells the name "Jonas"
- "the yellow one": question

## Open questions
- Who owns the retry budget after the migration?
- Which of the two tenants is meant by "the yellow one"?
~~~

- **`## Considered`** is where you settle each candidate, once: one line per thing you weighed, then `question`, or
  `no` and why in a few words. A line written here is decided: never weigh it again in your thinking.
- **`## Open questions`** holds the questions of the lines marked `question`, each written out in full.
- When nothing is left open, write `(none)` under `## Open questions`.

# Rules

- **One question per line, complete on its own:** a reader with no frames understands it without the notes.
- **Never invent.** A question comes from the notes, the words or the frames of THIS batch. Never ask about the meeting
  in general, and never ask for an opinion.
- **Never ask what the notes already answer.**
- **Decide each candidate once, in a line:** it is a question, or it is not. Never reconsider it.
  - A term nobody explains is a question as it stands. Never work out what it might stand for, which word the
    transcript misheard, or which listed term it could be: answering is the next step's job.
  - A candidate you are unsure about is left out: an open question is a gap you are certain of.
  - Think briefly. A reasoning that weighs one term again and again is cut off before it answers, and a cut answer
    adds nothing.
- **Read the frames once.** When that one read finds nothing open, the whole answer is `(none)` under the heading:
  write it and stop. Never go back through the frames to look again for a question you did not find.
- **Reason in a few short lines per frame:** what is unclear, or that nothing is. Never restate the notes back to
  yourself, and never repeat a conclusion. When every frame has its lines, write the answer.
- **Look up the terms list below only for a word this batch's words or screens hold.** Never walk the list term by term
  to check that none of them appears.
- **Never copy the value of a secret, password, key or token:** write `[secret]` in its place.
- **Write in English.** Quote on-screen text exactly, in its own language.
- No preamble, and no other heading than the three above.

{{SHARED:known-terms}}

# Reporting a problem

When an instruction can be read two ways, two instructions disagree, or what you were sent misreads the notes, add a
`# Feedback` document at the very end of your answer, with `## Kind` (one of `wrong_rejection`, `unclear_rejection`,
`unclear_instruction`, `wrong_instruction`, `missing_instruction`, `contradiction`, `wasted_effort`, `other`),
`## Summary` (one line) and `## Detail` (the sentence you mean, quoted). The people building this program read it, and
it never replaces your answer.

{{INJECT:base}}
