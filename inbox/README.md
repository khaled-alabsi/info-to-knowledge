# inbox

Drop recordings here, then run:

```bash
v2k inbox --list     # what would be processed
v2k inbox            # process it
```

A video in here is **new** until `../knowledge/<its-name>/05_knowledge.md` exists. Nothing records
which files have been seen: the document is the record. So to redo one, delete its folder under
`../knowledge/`; to skip one, move it out of here.

Only files directly in this folder are picked up, and only video files: `.mp4`, `.mov`, `.m4v`,
`.mkv`, `.webm`, `.avi`, `.mpg`, `.mpeg`, `.wmv`, `.flv`, `.ts`. Sub-folders, notes and subtitle
files are left alone, so you can keep a recording's companion files beside it.

A run that stopped partway leaves no document, so the item stays new. Running the inbox again picks
it up and reads the phase files already on disk instead of re-doing them, which is why an
interrupted transcription is not paid for twice.
