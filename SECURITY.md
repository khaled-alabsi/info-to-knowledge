# Security Policy

## Report a vulnerability privately

Do not open a public issue, pull request or discussion for a security problem. Report it through GitHub's private
vulnerability reporting for this repository:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** (a private security advisory).
3. Describe the problem: what you found, the steps or command that show it, the version or commit, your system, and
   what an attacker could do with it.

We will read the report, ask for details if we need them, and tell you what we decide. Please give us a reasonable
time to fix the problem before you make it public. We credit a reporter in the advisory unless you ask us not to.

## What to report

Examples of what counts:

- The web server (`i2k serve`) letting someone read, change, run or delete work without the sign-in that accounts
  switch on. Accounts are off until the first one exists: with none, anyone who reaches the port can upload, run and
  reset. The server listens on every network address by default, so run it only on a network you trust, start it with
  `--host 127.0.0.1`, or create an account first (`i2k accounts add <name> --role admin`).
- A path in an upload, a run folder name or a request that lets a user read or write a file outside the workspace.
- A command that runs from the content of a recording, a PDF, an office file or a model answer without the user
  asking for it.
- A secret (a Hugging Face token, an API key) written to a settings file, a log or a run folder.
- A download of a model or a tool that skips its integrity check.

## What to know about your data

A run folder holds the transcript, the screenshots and the document of your recording. Treat it as private as the
recording. The program sends nothing to a hosted service. It reaches the network only to download models and to call
the model hosts you configure.

## What not to report

- A wrong or untrue sentence in a generated document. Open a normal issue.
- A fault in a third-party model, tool or library. Report it to that project. Tell us too if info-to-knowledge uses
  it in an unsafe way.

## Supported versions

Until the first release, the latest commit on `main` is the only supported version.

## Keeping your own keys safe

Give the Hugging Face token to the program through the `HF_TOKEN` environment variable. Do not put a key in
`playbook.default.json` or `playbook.local.json`, and do not attach a run folder to a public issue before you check
that it holds no private text.
