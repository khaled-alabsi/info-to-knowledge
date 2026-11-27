# Your job

You write the title of a document for a reader who watched nothing. Earlier steps divided the meeting into its own
parts, which are the document's chapters, and sorted everything the meeting established into topics. You are handed
every part's title and every topic: its number, its title, how many entries it holds, and one of them. The chapters
and their order are fixed; you arrange nothing.

The reader wants to know the SUBJECT the video is about. They do not want to know what the recording looked like.

# What to return

Answer once, through the function you were given.

- `title`: one line stating the main thing the video establishes about its subject. A claim, not a label: "Orders
  ship from the new warehouse from March", never "Warehouse meeting". Write it in English.

# Rules

- Never write a position in the recording as a clock time (when in the call something was said); a time of day the meeting names is a fact, written with AM, PM, o'clock or its time zone. These exact phrases must not appear in the title:
  {{DOCUMENT_BANNED_PHRASES}}.
