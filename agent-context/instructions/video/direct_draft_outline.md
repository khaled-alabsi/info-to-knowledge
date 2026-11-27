# Finish the document of a meeting: its pictures, title, summary and agenda

A meeting's document was written in batches from its frames. You get its outline: each section's number and heading, the
first line of its text, and the pictures it places (`picture frame-042: caption`). You cut the pictures, then submit the
document's title, summary and agenda, and the changes you want to its headings. The program builds the table of contents
and puts the document together from your answer.

## What you do

1. Go through the pictures the outline lists, one at a time. For each one you want in the document, look at its frame,
   cut the part the reader needs, and look at the cut. The cut is what the document shows in place of the picture line.
   A picture you do not cut is left out of the document.
2. Cut only what the reader needs: the diagram, the table or the screen part that matters, whole and readable. Cut again
   when the cut loses a part that matters or shows more than it needs to.
3. Hide what must not be read: a secret, a token, an account or customer number, a name or other personal data. Mask it
   on the cut.
4. When every picture you want is cut, call `submit_outline` once. It ends your work.

## What you submit

- `title`: what the meeting was about, in a few words.
- `summary`: the conclusion first, as one sentence, then at most five more points, one sentence each. Write them from
  the headings and first lines the outline shows; a point the outline does not show is left out.
- `agenda`: one line for each chapter (a `##` heading), in the order the chapters come after your changes: the chapter's
  number and one sentence saying what it covers.
- `changes`: the changes you want to the headings, when the outline shows they are needed:
  - rename a heading whose title does not say its subject;
  - merge a heading into the section before it when both are one subject (for example two headings for the same topic,
    written in two batches);
  - move a heading, with its subsections, after another heading when the meeting's order reads better that way.
  Leave it empty when the headings are right.

## Rules

- You see the outline only. Do not write what the headings and first lines do not show.
- A heading is named by its number (`H03`), never by its title.
- Write in English. Never copy a password, a token or a key; write [secret].
- Do not write the document's text: the sections are kept as they are, except for the changes you ask for.
