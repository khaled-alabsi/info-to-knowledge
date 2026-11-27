### Repair what the PDF conversion or the text reading broke

The document was read out of a PDF by a program, and a scanned page by a text reader. Both break text in known ways.
Repair each break you find, in every pass:

| What broke | As it reads | Repaired |
|---|---|---|
| A word split by spaces | "Th e da y i snot goo d asi expected" | "The day is not good as I expected" |
| Words run together | "theaccountopensafterthecheck" | "the account opens after the check" |
| A word broken at a line's end | "con- figuration" | "configuration" |
| A letter misread | "Inforrnation", "C0nfiguration", "Ident1ty" | "Information", "Configuration", "Identity" |
| A table broken over pages | its header row again in the middle, or one row split over two | one table, one header, each row whole |
| A table read as lines | "Field Type Required / tenant_id string yes" | a markdown table with those columns |
| A heading lost | a short bold line that opens a section ("**Refund rules**") | a heading at the level of its siblings |
| A heading that is no heading | "## and then the gateway forwards it" | a paragraph, joined to the sentence it belongs to |
| A list read as one paragraph | "• Open the page • Pick the tenant • Save" | a markdown list, one item a line |

- Repair only where the right text is certain from the words around it. A number, a name, an id or a code line you
  cannot be sure of stays as it reads: a wrong repair is worse than a visible break.
- Never repair code, a command or a value inside a code block or a table cell by guess: "tenant_1d" may be a real id.
- A repair changes the form, never the meaning: no fact is added, dropped or reworded while repairing.
