# This frame is a figure of a PDF document

- Each frame is one figure cut from a page of a PDF document: a picture, a chart, a diagram or a screenshot. The
  document's text is written into the document from its own words, so these notes are only what the figure itself
  shows. The words given as said over the frame are the figure's caption, read from the file (empty when it has none),
  and the words before it are the text of its page before the figure: they say what the figure is for. Where the
  caption and your reading of the image differ, the caption's spelling wins.
- Nobody speaks: the rules about speech do not apply, and there is no question asked unless the figure shows one
  written. Under `## Take aways`, pair the caption, or the page's text before it, with the part of the figure it
  names.
- `## Facts` states what the figure shows that a reader needs: each value of a chart with its unit, each step and arrow
  of a flow, each label of a diagram tied to what it labels, what a screenshot's fields and buttons hold. A fact the
  page's text already states is still written when the figure shows it.
- `## Diagram` draws in Mermaid a figure that is a flow, a cycle, a sequence, a hierarchy or a structure of parts
  ("light → leaf → sugar", "client → gateway → provider"), every box and arrow the figure shows and no other; it is
  empty for a photo, a chart of values or a screenshot. Read each arrow on the figure before you draw it:
  - An arrow is drawn only where the figure draws one, from the box at its tail to the box at its head. Labels set in
    a ring or a row with no arrows between them are parts of one whole, never steps of a flow: draw them as the parts
    of the thing they surround (`Hub --- PartA`), never as `PartA --> PartB`.
  - A label in the middle of a ring, or a ring inside a ring, holds the others: it is the centre the outer labels
    belong to, never one more step among them.
  - Words written along an arrow are that arrow's label (`Team -->|weekly report| Lead`), never a box of their own.
  - Each arrow ends at the box it points at, with no box skipped: an arrow from the bottom role up to the middle one
    and a second from the middle one up to the top are two arrows, never one from the bottom to the top.
- `## As markdown` writes a figure that holds code or a table as the code block or the markdown table it holds,
  every line or row; it is `(none)` for anything else.
- `## Screenshots`: the figure is `needed` when its picture carries what words, a diagram or a table cannot (a photo,
  a chart's shape, a map, a screenshot, a drawing), and `helps` when the `## Diagram` or `## As markdown` holds it
  whole. A logo, an icon or a decoration with nothing to read is `none`, and its notes say only what it is.
- `## What happens` holds one sentence: what the figure is and what it shows ("A chart of the gateway's latency by
  month").
- `# Batch` is about the document, never a meeting: `## Story of the batch` says which figures the batch holds
  and what each shows, in their order ("The chart of latency by month follows the gateway's diagram"), and
  `## Atmosphere` says how the document presents them (a reference article's figures, plain and labelled). Nobody
  presents a PDF: never a presenter, a speaker, a slide read aloud, a room or an audience.
