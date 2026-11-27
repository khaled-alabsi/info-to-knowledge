# Contributing to info-to-knowledge

Thank you for helping. This page tells you how to set up, test and submit a change.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). A change you submit is licensed under
the GNU Affero General Public License v3.0, the license of this repository (see `LICENSE`).

## Report a bug or suggest a feature

Open an issue with the template that fits ([bug report](.github/ISSUE_TEMPLATE/bug_report.md) or
[feature request](.github/ISSUE_TEMPLATE/feature_request.md)). A good bug report holds:

1. What you ran, with the exact command line.
2. What you expected and what happened, with the last console lines.
3. Your system (macOS, Linux or Windows, its version, the chip or GPU), the Python and Node versions, and which model
   host and model you used.
4. The phase that failed, and the files of that phase's run folder that show it. Remove any private text first: a
   run folder holds the transcript and screenshots of your recording.

Report a security problem privately, as [SECURITY.md](SECURITY.md) says, never in a public issue.

Never attach a real recording, a transcript or a run folder of a private meeting to an issue.

## Set up a development environment

Install FFmpeg, Node.js 22, Tesseract with the English and German language data, and Pandoc first. Then, from the
repository root:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
(cd web && npm ci)
```

Use one virtual environment, the `.venv/` at the repository root, and one `requirements.txt`. When a change needs a
new package, add it to `requirements.txt` with an exact version. Do not create a second environment inside a folder.
The helper tools under `tools/` build their own environments with their `setup.sh`; those stay out of git.

Settings you change for your own machine go in `playbook.local.json` (start from `playbook.local.example.json`),
which git ignores. A secret never goes in a settings file.

## Run the tests

Every command below runs from the repository root.

| What | Command |
|---|---|
| The whole offline suite (no model host needed) | `.venv/bin/python tests/run_tests.py` |
| One case file | `.venv/bin/python tests/run_tests.py test_ui_services` |
| The layout and contract checks (run these first) | `.venv/bin/python tests/run_tests.py test_layout_conventions` |
| The web page's unit tests | `(cd web && npm test)` |
| The web page's browser tests | `(cd web && npm run test:e2e)` |

Notes:

- Run the case file your change touches first, and the whole suite before you open a pull request.
- A name given to `run_tests.py` runs that one case file.
- Some case files need a tool: FFmpeg, Tesseract or Pandoc. The cases that need Apple's text recognizer or MLX
  run only on an Apple silicon Mac.
- `tests/integration/` runs the real steps on rendered scenes and on frames. It needs Tesseract, takes minutes, and
  must not run beside a model on the same machine. Run it with `run_tests.py --integration`.
- A browser test needs Chromium once: `cd web && npx playwright install --with-deps chromium`.

## Branch and pull-request flow

1. Fork the repository and make a branch from `main` with a short, descriptive name (`fix-table-overflow`).
2. Keep one change in one branch. A bug fix, a feature and a clean-up are three pull requests.
3. Write a test that fails without your change and passes with it. Name a bug fix's test for what now works, and say
   in its comment what went wrong before.
4. Run the tests that your change touches, then the whole offline suite.
5. Commit with a message that says in plain words what was wrong and what works now ("a table no longer repeats a
   slide's sentence"), not the names of internal functions.
6. Open a pull request against `main` and fill in the template. Say what you tested and what you could not test.
7. A maintainer reviews it. Push fixes to the same branch. The pull request is merged when the tests pass and a
   maintainer approves.

## Code style

Python:

- Python 3.12, type hints as the neighbouring modules write them.
- The code in `src/` is in layers: `cli`, the graph (one node per module), chains, prompts, schemas, validators,
  services, models and utilities. A test in `tests/cases/` checks the layout, so a misplaced module fails the suite.
- A validator reads files and returns findings. It never calls a model and never writes a file.
- Every model call goes through one place, so every call is counted and logged.
- A model's answer names what it refers to (an id, a number). The program applies exactly what the answer names and
  never guesses a line from the wording.
- Names say what a thing is. A container's name ends with its type (`frame_list`, `settings_dict`).

Instructions to the models live in `agent-context/` as text files. A change to one changes what the next call is
told, so test it on a run before you open a pull request.

The web page (`web/src/`) is a React app built with Vite. A page text names what the user sees on the screen (a
button, a title), never a file path or an internal field. Test a page change at 390, 768, 1100 and 1440 pixels wide.

Documentation: write plain, active sentences. When you change what a phase reads or writes, change
[docs/FLOW.md](docs/FLOW.md) in the same pull request.

## Adding a model, a word list or an image

Anything you add needs a license that allows redistribution. Record its source and license in `NOTICE`. Do not add
a file whose license you cannot name. Never add a recording, a screenshot or a transcript of a real meeting; test
data uses invented names.

## Questions

Open an issue and say what you tried.
