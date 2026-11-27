## What was wrong, and what works now

<!-- One or two plain sentences, for example: "A table cut off its last column; now every column fits." -->

## What changed

<!-- The main parts of the change. -->

## How you tested it

Tick what you ran and say what you could not run (a platform, a model host).

- [ ] The whole offline suite: `.venv/bin/python tests/run_tests.py`
- [ ] The case file for the change: `.venv/bin/python tests/run_tests.py <case>`
- [ ] The web page tests, if `web/` changed: `cd web && npm test`
- [ ] A real run on a short recording, if an instruction, a check or a phase changed

Not tested, and why:

## Checklist

- [ ] The change has a test that fails without it.
- [ ] An instruction, its answer's shape and the check on it changed together, if a model's answer changed.
- [ ] docs/FLOW.md is updated if a phase reads or writes other files.
- [ ] A resource I added (model, picture, word list) has a license that allows redistribution, recorded in NOTICE.
- [ ] No secret, private path, recording or private text is in the change.
- [ ] I read CONTRIBUTING.md and agree to the Code of Conduct.

## Related issue

<!-- Closes #... -->
