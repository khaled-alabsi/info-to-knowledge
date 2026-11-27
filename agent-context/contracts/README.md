# contracts/ - what is CHECKED on disk

A file belongs here only if **a validator enforces it**. A "contract" with no validator is an
instruction or a fact: move it to `../instructions/` or `../base/`.

Injected at the point the rule applies, via `{{INJECT:contracts}}` in the instruction file - not
handed over as a read order, which a worker may skip with nothing reporting it.

Any value a validator compares against is defined ONCE in `output_contract.PROMPT_PLACEHOLDERS` and rendered
here through `{{NAME}}`. Prose that restates a config constant is deleted, never kept in sync.
