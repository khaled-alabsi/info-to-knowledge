# This source is a PDF document

- Each frame is one page of a PDF document, not a moment of a recording, and nobody speaks. The words given as said
  while a frame is on screen are the page's own text, read from the file and cleaned by the program of what a reader
  does not need (page trees, breadcrumbs, buttons, view counts, running headers and footers). That text is exact: where
  it and your reading of the image differ, its spelling wins.
- Write the notes from the page: every fact, step, table, list, code block and figure it holds. Quote the page's text
  where it states the knowledge. A fact says what the page says and no more: never a reason, a purpose or a
  consequence the page does not state ("a test webhook is sent during onboarding", never "... to verify
  connectivity").
- Write every table the page shows under `## As markdown`, each as its own markdown table in the page's order, the rows
  of a table that continues from the page before as well as the tables that start on it. When the page's text says a
  table continues the one on the page before, write only the rows this page adds, under the same header.
- A table's rows and a list's items are written once, in `## As markdown`. `## Facts` says what a table or a list
  tells (what it lists, its range, an item the page's text calls out), never one fact per row or per item, and `## Screen` outlines the table as one part with its
  header row quoted (`- table: the tenants' latency, header "Tenant", "Region", "Median latency"; rows in As
  markdown`), never its rows again: the outline check counts what `## As markdown` writes as quoted.
- `## Screenshots`: a page is `needed` only for what its text cannot carry, a picture, a chart, a diagram or a
  screenshot it shows. A page of text, lists, code and tables is `none`: its text is written in the document as it
  stands.
- `## What happens` still holds one or two sentences: what the page holds and where it stands in the document ("The
  guide's last page lists its attachments and two comments"). It is never left out because nobody speaks.
- The rules about speech do not apply: there is no speaker, no irony, no misheard word and no question asked unless the
  page shows one written. Under `## Take aways`, pair the page's text with the part of the page it stands in.
- A heading is structure, never a fact: no `## Facts` entry says that a section begins or a table continues ("An
  'Incidents' section begins"); the heading stands in `## Screen` and in `## As markdown`, and the page's own text
  says where it is.
- Keep the document's own structure: its headings, its numbered steps, its lists. Never add a structure the page does
  not have.
- An export's chrome (a page tree, a breadcrumb, buttons, a view count, a print footer) is no knowledge: leave it out
  of `## Facts` and `## Take aways`. `## Screen` still outlines the whole page: write the chrome there as one line
  naming its parts, such as `- the wiki's chrome: breadcrumb, page tree, Edit and Like buttons, print footer`, on
  every page that shows it, never pointing at another page.
- `# Batch` is about the document, never a meeting: `## Story of the batch` says what its pages set out and in
  which order, and `## Atmosphere` how the document presents it (a manual's numbered steps, a report's tables). Nobody
  presents a PDF: never a presenter, a speaker, a slide read aloud, a room or an audience.
