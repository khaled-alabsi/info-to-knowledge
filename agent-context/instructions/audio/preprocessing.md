# Your job

You prepare the notes of one audio recording for a writer, who makes a document from them. The recording's speech
was already cleaned into paragraphs, each one speaker's turn on one point, under a heading naming its lines:
`## S0041-S0044 · Speaker A`. A paragraph is named by its first line's id: `S0041`.

The program prepares the notes in steps, and this request is one of them. Its step is described under "This step"
below: what one item is, and the sections every item holds. A first step finds the items in the whole speech; a later
step goes through one item at a time and writes it out in detail, reading only that item's paragraphs and what the
earlier step wrote about it.

Your notes are the writer's source. Write them as plain statements about the subject, never about the recording:
"The team estimates the export at 5 points", never "Speaker B says that the estimate is 5 points".

# What complete means

- **Every section of the step, under every item, in the step's order.** A section the paragraphs say nothing about
  holds only `{{PREPARED_NOT_SAID}}`, and nothing else.
- **Everything the paragraphs say about a section is in it**: every fact, number, name, date, reason, decision and
  open question. You make the speech easier to use; you never make it shorter by leaving content out.
- **Nothing is added.** No fact, estimate, owner, date or conclusion that the paragraphs do not hold. A number is
  written as the paragraph says it ("5 points", "two days"), and only a number a cited paragraph holds.
- **Every section ends with the paragraphs it rests on**, on its own last line, in square brackets: `[S0041, S0052]`,
  each paragraph by its first line's id, only paragraphs of this request. A run of paragraphs may be one range,
  `[S0041-S0060]`, which names every paragraph starting in it. A section that holds only `{{PREPARED_NOT_SAID}}`
  names none.

# The answer

Answer once, in markdown, in exactly this shape (invented example; every id, name and word is a placeholder):

```
# The items discussed

## Export to the new format

### What it is
Exporting the monthly report in the new CSV format, which the finance team asked for.
[S0012, S0019]

## Login page redesign

### What it is
A new login page with single sign-on, carried over from the last sprint.
[S0031-S0040]
```

- `# <the step's title>` first, then one `## <item>` heading per item, each item titled in a few words of the
  subject's own terms.
- Under each item, its sections, each `### <section>` with its text under it and its paragraph ids on the last line.
- No text between an item's heading and its first section, and no text before the first item.
- A request about one item answers with exactly that one item, under its own title.

When a section is decided, write it and go on to the next; do not check every earlier section again.

# This step

{{INJECT:base}}
