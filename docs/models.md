# Reaching models by URL

The `providers` block of your settings tells i2k which server answers each kind of model, so a model can run on another machine, in another container, or at a provider, and nothing about it needs to be installed next to i2k.

`chat`, `speech` and `diarization` are reached by their URL now, and `i2k probe` reads every URL. Each other kind starts to use its URL when its in-process model is replaced, and a kind with an empty URL keeps running the way it always has.

## Set a server

Each kind of model has one block in `playbook.local.json`, and an empty `url` means "run it here, the old way":

```json
"providers": {
  "speech": {"url": "http://localhost:8791/v1", "apiKeyEnv": "", "model": "", "timeoutS": 3600}
}
```

A URL ends in `/v1`, and i2k adds the route to it. The same setting works from the environment, as in `PB_PROVIDERS__SPEECH__URL=http://localhost:8791/v1`.

A key never goes in a settings file. Put it in an environment variable, and write that variable's name in `apiKeyEnv`: i2k then sends its value as `Authorization: Bearer <key>`. Leave `apiKeyEnv` empty for a server with no login.

Check that every URL you set answers with `i2k probe`, which lists the models each server offers.

## The kinds

| Kind | What the server does | Route | Seconds one request may take |
|---|---|---|---|
| `chat` | Answers chat and vision requests of every phase | `POST /v1/chat/completions` | 600 |
| `speech` | Turns a recording into text with word times | `POST /v1/audio/transcriptions` | 3600 |
| `diarization` | Tells who speaks when | `POST /v1/audio/transcriptions` | 3600 |
| `embeddings` | Turns frame images into vectors | `POST /v1/embeddings` | 600 |
| `ocr` | Reads the words on an image, with their boxes | `POST /v1/ocr` | 600 |
| `detection` | Finds faces and people in a frame | `POST /v1/images/detections` | 120 |
| `decision` | Decides which frames carry knowledge | `POST /v1/classifications` | 600 |

Every server also answers `GET /v1/models` with the names of its models, which is what `i2k probe` reads.

Each block holds `url`, `apiKeyEnv` and `timeoutS`, and every kind except `chat` holds `model` too. For `chat` the model's name stays in each phase's own `models` entry, so `chat` has no `model`. A `model` you leave empty means the name i2k uses for that model.

A key that is not in the table, such as a misspelled kind, is refused with its name when the settings load.

## What each route sends and reads

The `chat`, `speech`, `diarization` and `embeddings` routes are the standard ones of OpenAI-compatible servers, so LM Studio, vLLM, Ollama and the providers' own services answer the ones they offer. The `ocr`, `detection` and `decision` routes are extensions of i2k, which no standard server offers; the `i2k serve-models` command, once it exists, serves them.

- **`chat`:** the request carries messages with text and `image_url` parts. i2k reads `choices[0].message.content`, and `reasoning_content` when the server sends it. A model in `models` that names no `baseUrl` of its own is called at `providers.chat.url`, as `openai-compatible:<model>`. The window a prompt may fill comes from the model's `numCtx` setting, unless the server reports one in its `GET /v1/models` entry (as `loaded_context_length`, `max_model_len` or `context_length`): then the server's number is used, and a difference is said once on the console. i2k asks the server nothing else about a model: it never loads, unloads or holds one, so the server decides what is loaded, and a run whose model is missing from `GET /v1/models` waits for the server to bring it back. The `ollama` and `anthropic` providers of `models` are no longer built by i2k: serve them behind an OpenAI-compatible address, as the model name `openai-compatible:<model>` with its `baseUrl`.
- **`speech`:** a multipart request with the audio as `file`, `response_format=verbose_json` and `timestamp_granularities[]` for `word` and `segment`. The audio goes compressed, as an Ogg file of Opus (about 14 MB an hour), never as the WAV. A recording longer than 20 minutes goes in windows of 20 minutes, one request each, and i2k moves every answer back to its place on the recording's time. i2k reads `segments`, each with `start`, `end` and `text`, and the words either in each segment's `words` or once in the reply's `words` (it places each word in the segment it falls in); a word's own text is in `word` or in `text`. A word may carry `probability`, an extra field that Whisper itself gives. A word without it is read as unknown, never as zero: the two clean-ups that remove invented speech then leave it alone, so a server that leaves `probability` out still works and removes a little less. The language setting goes as `language`; a server that is given none detects it.
- **`diarization`:** the same multipart request with `response_format=diarized_json`. The whole meeting goes as one request, every part at its place on the meeting's time with silence between them, so a speaker keeps one label for the whole meeting. The settings' `minSpeakers` and `maxSpeakers` go along as the extra fields `min_speakers` and `max_speakers` when set. i2k reads `segments`, each with `speaker`, `start` and `end`, as the turns; when the reply also holds `exclusive_segments` (the same turns with the overlap taken out), those are the exclusive turns, and otherwise the turns are used for both. With a diarization URL set, nothing of pyannote or torch is loaded on this machine, and the run's header names the server.
- **`embeddings`:** `input` is a list of images, each as a `data:` URL, and i2k reads one vector per image, in the order sent. The frame classifier sends 5 images for each frame (the whole frame, then its four quarters) in one request and expects 768 numbers back for each, from the SigLIP 2 B/16 image model; i2k scales each vector to length 1 and runs the tags head and the ink rule itself, so the tags, and the vectors file the similarity recheck reads, are the ones the in-process model gives.
- **`ocr`:** two requests go to this one server. The words on a frame or a cut are asked at `POST /v1/ocr`: a JSON request with `image` as a `data:` URL (PNG, at the picture's own size) and `languages` (`eng`, `deu`, as Tesseract names them). i2k reads `words`, each with `text`, `box` as `[x0, y0, x1, y1]` in the pixels of the image it sent, `line` and `confidence` from 0 to 100; a word below the confidence floor of the step that asked is dropped. A server may add `block`, the number of the text block a word belongs to, and i2k then groups words into blocks as it does Tesseract's; without it each line is one block. A request that fails gives no words for that image, as a failed Tesseract run does. A scanned PDF page is read at `POST /v1/chat/completions` on the same server: one `image_url` part with the page (PNG) and the text "Multi page parsing.", at temperature 0 with at most 8192 tokens; a reply that stopped at that limit is asked again with `repetition_penalty` 1.1 beside the standard fields. i2k reads `choices[0].message.content` in the model's block format.
- **`detection`:** a JSON request with `image` as a `data:` URL (PNG, shrunk to at most 1,000 pixels on its longer side) and `kinds`, a list with one of `face` or `person`. i2k reads `detections`, each with `kind`, `box` as `[x0, y0, x1, y1]` in the pixels of the image it sent, and `score`; it keeps the faces scoring 0.6 or more and the people scoring 0.35 or more, scales the boxes back to the frame, and widens each person's box by 10% on every side, as the in-process detector does. A failed request stops the step by naming the URL, since an empty list would read as a frame with nobody on it. A frame costs one request for faces, and one for people, or up to three when none stands at a side of the frame. On a 1,280 x 720 slide the request is about 6 KB for faces and 12 KB for people, and costs the program 6 to 7 ms beside the server's own time, against 19 ms (faces) and 29 ms (people) for the in-process detector (`tests/cases/test_detection_request_cost.py` prints them). Whether that is under 5% of phase 2's capture loop depends on the server's own time per frame, which only a real model gives, so the in-process detectors stay in the image and a URL is opt-in.
- **`decision`:** a JSON request with `images`, a list of `data:` URLs (JPEG, 640 pixels wide), and one `question`; i2k sends 8 frames at a time and asks the five questions of the d1-2B tool one after another for each batch, the knowledge question first. i2k reads `results`, one `decision` and `score` for each image, in the order sent: the `score` is the probability the answer is yes, from which i2k builds the `pKnowledge` and the classes of empty frames the tool gave, and applies `frames.decisionFilterCut` itself. The `decision` word is not read.

A request that fails is reported with the URL it called and the HTTP status the server answered, or with "no answer" when nobody did. A connection that drops before the server answers is tried once more.
