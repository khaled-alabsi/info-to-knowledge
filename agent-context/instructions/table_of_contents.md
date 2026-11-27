# Your job

You write the table of contents of the document: its chapters, one for each point of the agenda, in the meeting's
order, and the headings under each chapter. It is the scaffold of the document: every heading gets a placeholder,
and a later follow-up writes the text of each one from exactly the entries that fall under it.

You name only where each chapter and each heading starts. The program fills in the rest: a heading holds every entry
from its start up to the next start, and it puts chapters and headings in the order their starts come, so nothing
has to be counted or ordered by you.

Your draft headings of each chapter are in the follow-up: the subjects you found there and where each starts. Build
the table of contents from them, the way an editor would: keep what a reader would look up, join neighbours that serve
one goal, leave short asides inside the subject around them.

# What to return

Answer once, through the function you were given.

- `chapters`: one per agenda point, in the agenda's order. Each has:
  - `title`: the point's title as the agenda gave it, or a clearer one for a reader: two to eight words.
  - `first_entry_id`: the entry the chapter starts at. The first chapter starts at the first entry.
  - `topics`: the headings of this chapter, in the order they came. Each has a `title` and a `first_entry_id`, the
    entry the heading starts at.
- `set_aside_entry_ids`: entries that carry no knowledge about the subject. Only these qualify: greetings and
  sign-offs, small talk, the meeting tool itself (who can hear whom, screen sharing working or not), a presenter's name
  caption, filler words, and an entry that only says what a screen displayed and nothing about the subject. When in
  doubt, the entry belongs in a heading. Never set aside a `url`, `action_item` or `question` entry.
- `not_established`: what the meeting raises and does not settle, one short sentence each. An empty list when there
  is nothing.

# Rules

- The follow-up says how long the meeting is and about how many headings that calls for: one for every three to five
  minutes of talk. A table far longer than that is a list of moments, not a table of contents.
- A heading is a subject a reader would look up on its own. Its size is never a reason to split it: the program writes
  a long heading's text in several parts under the one heading.
- A title names the subject in two to eight words, specific enough to find it again, never "Discussion", "Demo",
  "Questions", "Next part" or "Miscellaneous". Titles side by side read alike.
- A subject the talk comes back to later gets a heading there with the same title, letter for letter.
- A `diagram` entry belongs in the heading it draws.
- Use only ids you were given. A contradiction you are handed is stated about the subject, never by frame. These exact
  phrases must not appear in a title or in `not_established`: {{DOCUMENT_BANNED_PHRASES}}.
- When every chapter and heading is decided, answer; never check the answer a second time.
