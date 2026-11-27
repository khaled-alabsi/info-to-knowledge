YOUR CONVERSATION WAS COMPACTED. The older history is gone. The follow-up above is still your job and
overrides anything below. You are not finished: you were writing {chapter_label}, which has
{headings} headings.

<measured>
Follow-up: the text of {chapter_label}. Attempt {attempt} of {max_attempts}. Calls used: {calls} of {max_calls}.
Reads of this follow-up: {reads_list_or_none}. Reads spent in the document: {reads_used} of {read_budget}.
Frames you handled in this follow-up (looks, cuts, marks, masks), from the store:
{frame_table_or_none}
Your last submitted answer, word for word, and why it was rejected:
{last_submission_or_none}
{findings_or_none}
Size of this conversation now: {tokens} of {size_limit} tokens.
</measured>

<summary>
{summary}
</summary>

<check>
For the drafts in your summary, counted by the program:
{per_heading_missing_and_foreign_ids}
Ids in the summary that are not entries of this chapter: {unknown_ids_or_none}.
Cut ids in the summary that do not exist in the store: {unknown_cuts_or_none}.
</check>

Keep every block of a settled section, word for word; change only what a finding above names. A cut id
above is the one `cut` returned: use it exactly. Read a heading's entries again with `read_notes` or
`read_moments` before you write from a figure or an id you cannot see. Submit through `submit_chapter`.
