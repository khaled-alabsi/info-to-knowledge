# compaction/

The texts a model is sent when its work outgrows its room. They are read at every compaction by
`src/prompts/compaction_prompt_loader.py`, so an edit reaches the next compaction. A word in braces, `{allowance}`, is filled
by the program and must stay in the text.

## conversation/

Phases 4 and 5 run as one conversation each. When a conversation reaches its size limit, the model of the follow-up
writes a summary of its own work, and the conversation restarts from the starting messages and one handoff message.

| File | What it is | Its placeholders |
|---|---|---|
| `<worker>/summary.md` | asks the worker to summarise its own work | `{allowance}`, `{messages}` |
| `<worker>/handoff.md` | the message after the compaction: what the program measured, the summary, the program's check | `{summary}`, the measured values and checks of that worker |
| `tail-line.md` | added under a handoff when the last turns were kept | none |
| `write-shorter.md` | asks for the summary again when it is over its allowance | `{first_tokens}`, `{allowance}`, `{summary}` |

The workers, by the follow-up's submit tool: `executive_summary_draft`, `parts` (the agenda), `draft_headings`,
`chapter`, `summary` (the final title, summary and agenda), `review_critique`, `review_resolution`.

## cut-thinking/

Phase 3's follow-ups are one call each. When a reply is cut at its output limit while still thinking, one more call
writes down what that thinking settled.

| File | What it is | Its placeholders |
|---|---|---|
| `<step>.md` | asks for what the cut thinking settled, for `critique`, `second_pass`, `open_questions`, `answer_questions` | `{allowance}`, `{thinking}` |
| `handoff.md` | what the next attempt opens with | `{summary}` |
| `finish.md` | asks for the answer from the summary, in the same attempt | `{summary}` |
| `finish-mistakes.md` | asks a critique for its rules from the mistakes its thinking found | `{mistakes}` |
