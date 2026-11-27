# Your job

Every batch of this run read its own notes back and named what went wrong in them. Your job is to turn those findings
into one set of rules: the rules a worker reading the instructions before the next run should be given.

You are not reading any meeting. You are reading what workers said about their own work, already grouped by the
program. One group is one anti-pattern, seen by one batch or by many.

# What you decide, and what you do not

- **The counting is not yours.** The program grouped the findings by their names, and the counts beside each group are
  its arithmetic. Never split a group, and never write a count.
- **Join groups that are one anti-pattern under all their numbers.** The program groups only names that read alike, so
  one mistake described in other words can stand as several groups. When two or more groups say the same mistake,
  write one rule for them and open its heading with every number, `## Groups 3, 7: <name>`; the program adds their
  counts. Join groups only when a worker would avoid both by following one `How to avoid it`.
- **The wording is yours.** For each group, write the one rule that covers every wording it was given. The batches saw
  the same mistake and described it differently; the rule you write is the clearest of them, not a list of them.
- **Dropping a group is yours, and it is the only judgement you have.** Leave out a group that is not an anti-pattern:
  one meeting's content, a one-off nobody would repeat, or advice that would not help a worker on a different
  recording. Say nothing about what you dropped; leaving it out is the whole answer.

# The rule

**A rule holds for any meeting.** The worker who reads it has a different recording in front of it.

- **No frame number, and no number of any batch.** The counts are the program's, and they are already written.
- **No word off any meeting's screen.** Name what KIND of thing was missed - "the row labels of a table", "the button
  a step starts from" - never the words a meeting showed.
- **`How to avoid it` is an instruction a worker can follow on its next frame**, not a description of the mistake.
- **Open each heading with its group's number**, `## Group 3: <name>`, or with every number of the groups it joins,
  `## Groups 3, 7: <name>`: the numbers tell the program which groups your rule words, so the name can be the clearest
  one.

# What to return

Answer once, in markdown: `# Critique`, then one `## Group <n>: <name>` (or `## Groups <n>, <m>: <name>`) section per
rule you keep. Never add a heading of your
own: the program reads your answer by these headings, and one it does not know is refused.

~~~markdown
# Critique

## Group 1: An arrow is kept from the frame before without checking its head
What it is: the newest diagram is handed over in full, and a frame that only adds a box keeps an arrow whose direction
its own picture contradicts.
How to avoid it: read every arrow's head on the frame in front of you before you keep it, and drop the ones it does
not show.

## Group 2: A table is outlined without its row labels
What it is: the outline names the table and its columns and stops there, so a reader cannot tell which row a value
belongs to.
How to avoid it: quote every row's label, in the order the rows stand, as part of the table's own outline.
~~~

Keeping no group at all is an answer: write `# Nothing to criticise` when not one of the groups is an anti-pattern a
later worker should be warned about.

{{SHARED:worker-feedback}}
