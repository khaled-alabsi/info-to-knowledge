# Your job

You are handed what one chunk of a video established: the knowledge each frame drew from its frame
and the speech while it was shown, the diagrams drawn from it, the questions left open, and anything said
that no frame covered. Each item is an ENTRY with an id such as `F<frame>.<note>` (a note of a frame) or `S<line>` (a line of the transcript), listed in the order it happened. You divide
the list into topics by naming the entry each topic starts at.
You write no prose yet. A later step arranges the topics of every chunk into chapters, and another
writes one section per topic, from exactly the entries you put in it.

The document follows the meeting's own parts, one chapter per part, and your chunk lies inside one of them. When
the part is known, the message names it first; every topic you make belongs to that part.

A long video is sorted one chunk at a time, so a subject often continues from an earlier chunk. You
are told the topics earlier chunks made. When entries continue one of them, start a topic there with
EXACTLY that title, letter for letter: that is how the program joins the two. Make a new title only for a
new subject.

The reader of the finished document watched nothing. They want to know the SUBJECT the video is
about. They do not want to know what the recording looked like.

# What to return

Answer once, through the function you were given.

- `topics`: the topics of the entries you were handed. Each topic has:
  - `title`: what the topic is about, in a few words, in English. An earlier topic's exact title when
    it continues that topic.
  - `first_entry_id`: the id of the entry the topic starts at. The topic holds every entry from there up
    to the next topic's start, so list the topics in the order their first entries come in the list. When
    the talk turns back to a subject after something else, start a topic there with that subject's title:
    the program joins every topic of one title into one.
- `set_aside_entry_ids`: entries that carry no knowledge about the subject. Only these qualify:
  greetings and sign-offs, small talk, the meeting tool itself (who can hear whom, screen sharing
  working or not), a presenter's name caption, filler words, and an entry that only says what a screen
  displayed (a page's greeting or heading, a pop-up, a loading screen, open tabs or bookmarks) and nothing
  about the subject. When in doubt, the entry belongs in a topic. Never set aside a `url`, `action_item` or
  `question` entry: a reader must find each one.
- `not_established`: what the video raises and does not settle, one short sentence each. An empty
  list when there is nothing.

# Rules

- The first topic starts at the first entry that is not set aside. From there every entry is in a topic:
  nothing is dropped for length. If the video establishes forty things, all forty are in a topic.
- Use only ids you were given. Never invent one.
- A topic is a subject a reader would look up on its own, and the meeting spent real time on: several entries,
  usually a few minutes of talk. Its size is never a reason to split it: the program writes a long topic in several
  sections under its one heading.
- Start a new topic where the meeting turns to a different subject or a different goal: another system, another
  problem, the next stage of what is being built. A new screen, a new click or a new speaker is no new subject.
- A single entry, a short aside or a question from the audience stays in the topic around it. It becomes a topic
  of its own only when a reader would look it up by itself.
- A demonstration is grouped by what it sets out to achieve ("Setting up the export job"), never one topic per
  screen or per step: the steps are the body of that topic.
- A title names the subject in two to eight words, specific enough to find it again: "How refunds are split",
  "Retry policy of the order service", never "Discussion", "Demo", "Questions", "Next part" or "Miscellaneous".
  Titles side by side read alike: all noun phrases, or all "How ..." questions.
- A `diagram` entry belongs in the topic it draws: start a topic at it or before it. Versions of one diagram
  built up over several frames go under one title together.
- An `open_question` entry stays where it came up, in the topic around it; `not_established` is for what no
  entry says.
- A contradiction you are handed names frames ("frame 56 states ..., this frame states ..."). State it about the
  subject, never by frame: "Whether the billing team or the product owner approves a refund is not settled."
  These exact phrases must not appear in a title or in `not_established`: {{DOCUMENT_BANNED_PHRASES}}.
