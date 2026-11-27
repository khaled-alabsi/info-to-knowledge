# Word lists

Words the outline check counts as readable, beside the system's English list (`/usr/share/dict/words`).

- `deu.txt`: German, 89,378 words, the word list of Tesseract's `deu.traineddata` (tessdata, Apache License 2.0),
  taken out with `combine_tessdata -u deu.traineddata deu.` and `dawg2wordlist deu.lstm-unicharset deu.lstm-word-dawg deu.txt`.
  Every word holding a word of the repository's `.denylist` is taken out (first names, company and system names).
