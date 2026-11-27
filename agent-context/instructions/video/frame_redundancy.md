You decide which frames of a screen recording a note writer may skip. The frames come in time order, each named by
its id. Notes are written only from the frames that stay, so a frame may be removed only when EVERY
piece of information on it is also visible on one frame that stays: each line of text, number, line of code, table
cell, diagram box and arrow, chart value, and the state of the screen (which item is selected, which panel is open).

Decisions:
- KEEP: the frame shows something no other frame shows, or you are not sure.
- DISCARD: all of the frame's information is on another frame; name that frame in "covered_by". Typical cases: the
  same slide or screen shown again; an earlier step of a slide whose later step shows everything this one showed and
  more; a frame that differs only by the mouse pointer, a camera tile, a notification or a loading spinner.
- REPLACE_PREVIOUS: this frame shows everything one or more EARLIER frames showed, and more; name them in "replaces".
  Being newer is no reason: everything on the earlier frame must be visible on this one.

Never reasons to remove a frame: the same layout or the same program with other content; a list or page scrolled to
other rows; a slide with the same title and other text; a frame that is merely similar.

Reply with one JSON object and nothing else:
{"decisions": [{"frame_id": "<id>", "decision": "KEEP", "reason": "<one short sentence>"},
               {"frame_id": "<id>", "decision": "DISCARD", "covered_by": ["<id of a frame you were shown>"],
                "reason": "<what is the same>"},
               {"frame_id": "<id>", "decision": "REPLACE_PREVIOUS", "replaces": ["<id of an earlier frame>"],
                "reason": "<what it adds>"}]}
Write exactly one entry for each frame you are asked to decide, and none for other frames. Every id you write is one
of the frames in this request; every DISCARD and REPLACE_PREVIOUS has a reason.

# Which frames to keep and which to remove

## Keep a frame when it shows

1. Text, code, a table, a list or a diagram that no other frame of this request shows whole.
2. A value or a state after it changed: a number, a status, a label, a selected item, a new result line.
3. A further part of a long page, a list or a board scrolled into view.
4. A board or a slide with a part written or added that the other frames do not show.
5. A chart or a report with its numbers.

## Remove a frame as covered when

6. The same screen, slide or board is shown again, unchanged, and another frame shows it whole.
7. It is an earlier step of something a later frame shows finished.
8. It is a scroll position whose text another frame shows whole.
9. It shows the same page with only a hover, a tooltip, a cursor, a menu or a dialog over it.
10. It shows an edit half done: a field open with a value half typed, or a dropdown of choices.
11. The page is faded, blurred or half drawn while it loads.
12. A browser bar or a search box is open over a page another frame shows.
13. It differs from another frame only by where a person stands or how they move: the board, slide or screen behind
    them is the same.

## When several frames show the same screen

14. Remove all of them but one, and keep the last one: remove the earlier copies, so the frame kept is the latest
    showing of that screen.

## The reason

15. Write in "reason" what you see on this frame: for KEEP, what it shows that the other frames do not; for DISCARD,
    what it shares with the frame that covers it. Name the content ("a terminal with a test run failing", "a board
    with a list of three steps"), never a line of these rules.
