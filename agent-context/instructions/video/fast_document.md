# Write the reference document of a meeting from its frame descriptions

You write the document a person who did not attend a meeting reads instead of watching its recording. You get every
frame of the recording's shared screen in time order: its number, its time, what was said while it was on screen, and
a description of what the screen showed. A small model wrote each description from the frame's image alone, so a
description can misread a word or miss a part; the speech can fix it, and a part described twice is one part.

## What the document holds

1. `# <title>`: what the meeting was about, in a few words.
2. `## Summary`: the conclusion first, then what was shown and decided, in at most 300 words.
3. One `##` chapter per part of the meeting, in the meeting's order, each titled by its subject. Inside a chapter, write
   what a reader needs: what was shown (the screens' words and numbers, quoted as the descriptions quote them), what
   was said about it, and what follows from it. Use lists and tables where the screen did.
4. `## Decisions`, `## Action items` and `## Open points`, each a list; leave a section out when the meeting had none.

## Rules

- Write knowledge, never a retelling of the recording: "the gateway signs every token", never "the presenter then
  showed a slide about tokens".
- Keep every number, name, key and value the descriptions and the speech give; a frame that adds nothing new is
  written once, where its subject is.
- Where a description and the speech disagree on a word, take the screen's spelling when the description quotes the
  screen, and say nothing about the disagreement.
- Never copy a password, a token or a key; write [secret].
- Write in English, the screen's own words quoted as they are.

Answer with the document in markdown and nothing else.
