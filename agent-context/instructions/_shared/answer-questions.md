# Your job

You listed the questions a batch of frames leaves open. Now look for each answer in what you were shown: the
notes, the words spoken, and the frames.

# What to return

Answer once, in markdown, in this shape and no other:

~~~markdown
# Batch

## Open questions
- Which of the two tenants is meant by "the yellow one"?

## Answered questions
- Who owns the retry budget after the migration? → Only Jonas may change it (said in frame 12, and shown in the "Retry budget" form).

## Not a question
- What does the status line "Synced 4s ago" mean? → a detail of the screen the talk never raises
~~~

- Every question you were given is in exactly one of the three sections, copied word for word.
- A question that is no question goes under `## Not a question` as `<the question> → <why>`: a detail of the screen
  (a status line, a timer, a counter) the words never raise; what the notes or the words already say; a name the
  speech misheard and a frame spells; an aside that asks nothing. It never reaches the reader as an open point.
- An answered question is `<the question> → <the answer>`, and the answer says where it comes from: a frame number, what
  was said, or what the screen shows.
- Write `(none)` under a section that has no entry.

# Rules

- Answer only from what you were shown. When the notes, the words and the frames do not settle it, the question stays
  open. A guess is worse than an open question.
- Decide each question once. A question is answered only when the notes, the words or the frames STATE the answer.
  When they only point at it ("in this previous talk", "the link on the slide") without saying which talk or where it
  leads, the question stays open. If you cannot tell which it is, leave it open. Never reconsider a decision you made.
- When the screen and the speech give two different values for one thing (the slide says 3 retries, the speaker says
  5) and nothing else you were shown says which holds, the question stays open, written with both: "the slide
  says <one>, the speaker says <other>". Two spellings of one name or word ("Jonas" on the slide, "Jonnas" in the
  speech) are no two values: the speech may have misheard it, and the next rule settles it by the frame. Decide this the moment you see the two disagree; weighing which one the
  speaker meant is a guess.
- A question about a word the speech may have misheard (what it is, how it is spelled, whether two spellings are one
  thing) is settled only by text on a frame that spells it. The same holds for a name, a web address, a file name or a
  number heard only in the speech: a question asking its exact form ("the speaker says 'docs acme sync dot io'") is
  never answered by the words spoken, since they are what it doubts. The notes took their words from the same
  transcript, so comparing the notes with the speech settles nothing: look at the frames once, and if none spells it,
  the question stays open.
- Never give the account of the batch as the source of an answer: it is your own summary, not something the meeting
  showed. The sources are the frames' notes, the words spoken and the frames. An answer that only the account holds
  leaves the question open.
- An answer that says something is new, first or changed is checked against the frames before it and their notes:
  when an earlier frame already shows it (a warning the note calls new stands in an earlier frame's output), the note
  is wrong, it is no source, and the question stays open unless something else settles it.
- An answer never says "likely", "probably", "presumably" or "intended to". A guess about what someone meant to say
  or would do is not an answer: the question stays open.
- Reason in one or two lines per question: where you looked, and what you found or did not find. Never repeat a
  conclusion, and never argue with the rules of this message. When every question has its line, write the answer.
- The transcript does not mark who speaks, and a name in the notes beside some words is a guess: words that seem to
  come from someone else may finish the sentence a question asks about. Read them as its end before you leave it open.
- Never add a question of your own here.
- Never copy the value of a secret, password, key or token: write `[secret]` in its place.
- Write in English. Quote on-screen text exactly, in its own language.
- No preamble, and no other heading than the three above.

A question the list of terms below answers is answered from it, with the term written as the list writes it: the
list is a source like the notes, the words and the frames.

{{SHARED:known-terms}}

# Reporting a problem

When an instruction can be read two ways, two instructions disagree, or what you were sent misreads the notes, add a `# Feedback` document at the very end of your answer, with `## Kind` (one of `wrong_rejection`, `unclear_rejection`, `unclear_instruction`, `wrong_instruction`, `missing_instruction`, `contradiction`, `wasted_effort`, `other`), `## Summary` (one line) and `## Detail` (the sentence you mean, quoted). It is read by the people building this program and never replaces your answer.

{{INJECT:base}}
