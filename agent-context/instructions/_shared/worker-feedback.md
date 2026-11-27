# Reporting a problem: `report_problem`

This program is still being built, and you are the only one who sees it from the inside. `report_problem(kind,
summary, detail)` writes your report where the people building it read it, while the run goes on.

**Report the moment you hit one of these, not at the end:**

- A rejection you believe is wrong: your answer does what the instructions ask, and the finding misreads it.
- A rejection that does not say what would be accepted, so your correction is a guess.
- An instruction that can be read two ways, or that you had to read twice. Quote the sentence and say what you
  thought it could mean.
- Two instructions, or an instruction and a check, that disagree.
- A tool that cannot do what you need. Say what you wanted to do, not the tool you imagine: "I needed every frame
  that shows the login screen" is worth more than a tool name.

**Report what would make your work better, too, not only what broke.** You read these instructions and use these
tools more than anyone, so the people building this program count on you to say how they can be better. Report when:

- An instruction could be clearer, shorter or in a better order. Quote it and write the sentence you would have
  wanted (`unclear_instruction`).
- You had to work out something the instructions could have told you up front (`missing_instruction`).
- A tool would have saved you calls with one more argument, a larger range or another shape of result
  (`insufficient_tool`).
- You did the same steps by hand several times, where one tool could have done them (`missing_tool`).
- You spent calls on work that turned out not to be needed (`wasted_effort`).

A report never costs you an attempt, and never counts against your work, so report as soon as you notice, and never
save a report for later. One clear report with what would have helped is worth more than several vague ones.

**Asked why an answer was rejected, report even when the fault is yours:** use kind `other`, name the rule you did
not follow and what led you away from it. That tells the people building this program which rules are easy to miss.

**Then carry on.** A report never replaces your answer and never ends your attempt. When a finding is wrong, still
submit the answer you believe is right; never invent content only to satisfy a finding you believe is wrong.

**Say what you needed, not only what failed.** A good report (invented):

```
report_problem(kind="wrong_rejection",
               summary="the check says heading T<chapter>.<heading> is missing, but my chapter has it",
               detail="I wrote '### Refund limits (T<chapter>.<heading>)' as the third heading. The finding says it is missing.
               Maybe the check expects the heading without its id?")
```

**Never report that you finished.** The program already reads what you submitted. "Chapter written" or "all
questions answered" tells the people watching nothing new and costs a call.

Report one problem once. If you send it again you are told it is already recorded; that is the answer.

The kinds, and nothing else is accepted:

- `wrong_rejection`: a check rejected an answer that does what the instructions ask, or its finding misreads your answer
- `unclear_rejection`: a rejection did not say what would be accepted, so your correction was a guess
- `unclear_instruction`: an instruction can be read two ways, or you had to read it twice
- `wrong_instruction`: an instruction says something that is not true of what you found
- `missing_instruction`: you had to work something out yourself that should have been written down
- `contradiction`: two instructions, or an instruction and a check, disagree
- `missing_tool`: the work needs something no tool in your list can do
- `insufficient_tool`: a tool exists but cannot do what you need: a limit, a shape, an argument it does not take
- `deadlock`: there was no way forward: every route was refused, or two rules cannot both be followed
- `wasted_effort`: you spent calls on work that turned out not to be wanted
- `other`: anything worth saying that none of the kinds above fits
