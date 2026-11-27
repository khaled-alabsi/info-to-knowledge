# Models

i2k uses two kinds of models, and they are reached in two different ways.

- **Standard models** are reached by URL, and any OpenAI-compatible provider can serve them: the chat model, the speech model, the speaker model and the image embedding model.
- **Models inside i2k** run on the machine or in the container where i2k runs: Tesseract for the text on a frame, two small OpenCV models that find faces and people, and the decision model d1-2B. Nothing about them is set by URL, except the decision model's one optional route.

## Standard models: any OpenAI-compatible provider

Every server's address is written once, in the `hosts` list of your settings. Each model in `models` and each kind in `providers` names the host it runs on, so a model can run on another machine, in another container, or at a provider, and nothing about it needs to be installed next to i2k. A kind that names no host keeps running the way it always has. `i2k probe` reads every host a model or a kind names.

### Set a server

Add the server to `hosts` in `playbook.local.json`, then name it in the kind's block; a kind with an empty `host` means "run it here, the old way":

```json
"hosts": {
  "speech-box": {"url": "http://localhost:8791/v1", "apiKeyEnv": ""}
},
"providers": {
  "speech": {"host": "speech-box", "model": "", "timeoutS": 3600}
}
```

`hosts` holds `lmstudio` (`http://localhost:1234/v1`) and `ollama` (`http://localhost:11434`) from the start; a model whose name starts with `lmstudio:` or `ollama:` runs on that host when it names no `host` of its own. A URL ends in `/v1`, and i2k adds the route to it. The same setting works from the environment, as in `PB_HOSTS__LMSTUDIO__URL=http://localhost:1234/v1`.

A key never goes in a settings file. Put it in an environment variable, and write that variable's name in the host's `apiKeyEnv`: i2k then sends its value as `Authorization: Bearer <key>` to every model and kind on that host. Leave `apiKeyEnv` empty for a server with no login.

Check that every host you named answers with `i2k probe`, which lists the models each server offers.

### The kinds

| Kind | What the server does | Route | Seconds one request may take |
|---|---|---|---|
| `chat` | Answers chat and vision requests of every phase, and reads the pages of a scanned PDF | `POST /v1/chat/completions` | 600 |
| `speech` | Turns a recording into text with word times | `POST /v1/audio/transcriptions` | 3600 |
| `diarization` | Tells who speaks when | `POST /v1/audio/transcriptions` | 3600 |
| `embeddings` | Turns frame images into vectors | `POST /v1/embeddings` | 600 |

Every server also answers `GET /v1/models` with the names of its models, which is what `i2k probe` reads.

Each block holds `host` and `timeoutS`, and every kind except `chat` holds `model` too. For `chat` the model's name stays in each phase's own `models` entry, so `chat` has no `model`. A `model` you leave empty means the name i2k uses for that model.

A key that is not in the table, such as a misspelled kind, or the old `ocr` and `detection` kinds, is refused with its name when the settings load.

### What each route sends and reads

These are the standard routes of OpenAI-compatible servers, so LM Studio, vLLM, Ollama and the providers' own services answer the ones they offer.

- **`chat`:** the request carries messages with text and `image_url` parts. i2k reads `choices[0].message.content`, and `reasoning_content` when the server sends it. A model in `models` is called at the host its `host` names, else at the host named like the start of its name (`lmstudio:<model>` on `lmstudio`); a model named `openai-compatible:<model>` must name its `host`. The window a prompt may fill comes from the model's `numCtx` setting, unless the server reports one in its `GET /v1/models` entry (as `loaded_context_length`, `max_model_len` or `context_length`): then the server's number is used, and a difference is said once on the console. i2k asks the server nothing else about a model: it never loads, unloads or holds one, so the server decides what is loaded, and a run whose model is missing from `GET /v1/models` waits for the server to bring it back. The `ollama` and `anthropic` providers of `models` are no longer built by i2k: serve them behind an OpenAI-compatible address, as the model name `openai-compatible:<model>` with its `host`.
- **`chat`, for a scanned PDF page:** with `providers.chat.host` set, each scanned page is read through that chat server, naming the model in `pdf.unlimitedOcrModel`. The request is one `image_url` part with the page (PNG) and the text "Multi page parsing.", at temperature 0 with at most 8192 tokens; a reply that stopped at that limit is asked again with `repetition_penalty` 1.1 beside the standard fields. i2k reads `choices[0].message.content` in the model's block format. With `providers.chat.host` empty, the scanned-page reader installed on this machine is used, as before.
- **`speech`:** a multipart request with the audio as `file`, `response_format=verbose_json` and `timestamp_granularities[]` for `word` and `segment`. The audio goes compressed, as an Ogg file of Opus (about 14 MB an hour), never as the WAV. A recording longer than 20 minutes goes in windows of 20 minutes, one request each, and i2k moves every answer back to its place on the recording's time. i2k reads `segments`, each with `start`, `end` and `text`, and the words either in each segment's `words` or once in the reply's `words` (it places each word in the segment it falls in); a word's own text is in `word` or in `text`. A word may carry `probability`, an extra field that Whisper itself gives. A word without it is read as unknown, never as zero: the two clean-ups that remove invented speech then leave it alone, so a server that leaves `probability` out still works and removes a little less. The language setting goes as `language`; a server that is given none detects it.
- **`diarization`:** the same multipart request with `response_format=diarized_json`. The whole meeting goes as one request, every part at its place on the meeting's time with silence between them, so a speaker keeps one label for the whole meeting. The settings' `minSpeakers` and `maxSpeakers` go along as the extra fields `min_speakers` and `max_speakers` when set. i2k reads `segments`, each with `speaker`, `start` and `end`, as the turns; when the reply also holds `exclusive_segments` (the same turns with the overlap taken out), those are the exclusive turns, and otherwise the turns are used for both. With a diarization host set, nothing of pyannote or torch is loaded on this machine, and the run's header names the server.
- **`embeddings`:** i2k sends each image as its own request, in the shape vLLM documents for image models: `messages` holds one user message whose content is an `image_url` part with the image as a `data:` URL, plus `"encoding_format": "float"`; it reads the one vector the reply holds. (Texts, where i2k has any to embed, go as the standard `input` list.) The frame classifier sends the 5 images of each frame (the whole frame, then its four quarters) one after the other, and expects 768 numbers back for each, from the SigLIP 2 B/16 image model; i2k scales each vector to length 1 and runs the tags head and the ink rule itself, so the tags, and the vectors file the similarity recheck reads, are the ones the in-process model gives.

A request that fails is reported with the URL it called and the HTTP status the server answered, or with "no answer" when nobody did. A connection that drops before the server answers is tried once more.

### Providers that answer each kind

Each list below holds only providers whose own documentation was read for the route and the fields i2k sends; the sources are at the end of this section. A provider not listed may work as well: it has not been checked. Keys come from the environment, named in the host's `apiKeyEnv`. Each example adds the host to `hosts` and names it in the kind's block.

**`chat`** (`POST /v1/chat/completions` with `image_url` parts):

| Provider | `url` | Checked in its documentation |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | The Chat Completions API analyses images; the images guide shows images as `data:image/jpeg;base64,...` |
| OpenRouter | `https://openrouter.ai/api/v1` | `image_url` parts with a URL or base64 data |
| LM Studio | `http://localhost:1234/v1` | Chat completions with text and images |
| Ollama | `http://localhost:11434/v1` | Vision input, base64 only: i2k sends `data:` URLs, which is base64, so it fits |
| vLLM | `http://localhost:8000/v1` | Image input follows the OpenAI vision API; start a vision model with `vllm serve <model> --runner generate` |
| llama.cpp server | `http://localhost:8080/v1` with `--port 8080` | `image_url` parts as URL, base64 or `data:` URI, when started with a multimodal projector (`--mmproj`, or `-hf` which downloads it) |

```json
"hosts": {"openrouter": {"url": "https://openrouter.ai/api/v1", "apiKeyEnv": "OPENROUTER_API_KEY"}},
"models": {"frame": {"spec": "openai-compatible:<model>", "host": "openrouter"}}
```

**`speech`** (`POST /v1/audio/transcriptions`, `verbose_json` with `timestamp_granularities[]` for `word` and `segment`). i2k needs the word times, so a provider that leaves them out is no fit:

| Provider | `url` | Checked in its documentation |
|---|---|---|
| OpenAI, model `whisper-1` | `https://api.openai.com/v1` | `verbose_json` with word timestamps; only `whisper-1` takes `timestamp_granularities[]`. The page lists `whisper-1` as deprecated with shutdown on February 26, 2027. 25 MB per file: i2k's Ogg Opus windows of 20 minutes are about 5 MB |
| Groq | `https://api.groq.com/openai/v1` | `whisper-large-v3` and `whisper-large-v3-turbo`, `verbose_json`, `timestamp_granularities[]` `word` and `segment`; 25 MB on the free tier, 100 MB on the developer tier |
| Speaches (faster-whisper) | `http://localhost:8000/v1` | OpenAI-compatible server on faster-whisper; its code accepts `verbose_json` and `timestamp_granularities[]` |
| LocalAI | `http://localhost:8080/v1` | `/v1/audio/transcriptions` on whisper.cpp, with faster-whisper among its other backends; word times in `verbose_json` were not checked |

Ollama also answers `/v1/audio/transcriptions` with `verbose_json`, but its page says `timestamp_granularities` is not supported, so it gives no word times and i2k's clean-ups run with less. vLLM answers the route for speech-recognition models; its page lists no response formats, so that is not checked.

```json
"hosts": {"groq": {"url": "https://api.groq.com/openai/v1", "apiKeyEnv": "GROQ_API_KEY"}},
"providers": {"speech": {"host": "groq", "model": "whisper-large-v3-turbo", "timeoutS": 3600}}
```

**`diarization`** (`POST /v1/audio/transcriptions` with `response_format=diarized_json`): only one provider's documentation was found to answer it.

| Provider | `url` | Checked in its documentation |
|---|---|---|
| OpenAI, model `gpt-4o-transcribe-diarize` | `https://api.openai.com/v1` | `response_format=diarized_json` returns segments with `speaker`, `start` and `end`. Audio over 30 seconds needs `chunking_strategy` set to `auto` or a VAD setting, and the page lists the model as deprecated with shutdown on February 26, 2027 |

i2k does not send `chunking_strategy`, and it sends the whole meeting as one file and the extra fields `min_speakers` and `max_speakers`. Whether OpenAI accepts that request unchanged has not been tried, and an OpenAI file may not pass 25 MB. For any other provider, the standard answer is a server you run yourself that returns `diarized_json`; no other hosted or local server's documentation was found to offer it.

```json
"hosts": {"openai": {"url": "https://api.openai.com/v1", "apiKeyEnv": "OPENAI_API_KEY"}},
"providers": {"diarization": {"host": "openai", "model": "gpt-4o-transcribe-diarize", "timeoutS": 3600}}
```

**`embeddings`** (`POST /v1/embeddings`, one 768-number vector per image from SigLIP 2 B/16). The frame classifier's tags were trained on the vectors of that one model, so another model's vectors give wrong tags. i2k sends an image the way vLLM's embedding-models page documents it (`messages` with an `image_url` part, one image for each request), and this is where the providers differ:

| Provider | Checked in its documentation |
|---|---|
| OpenAI | `input` is text only (`text-embedding-3-small`, `text-embedding-3-large`, `text-embedding-ada-002`): it cannot embed a frame |
| Ollama | `input` is a string or an array of strings: text only |
| LM Studio | Serves `/v1/embeddings`; the page does not say it accepts images |
| llama.cpp server | Serves `/v1/embeddings` with `--embeddings`, for embedding models; image input is not documented there |
| vLLM | The one server whose documentation matches i2k's request. Its embedding-models page lists `google/siglip2-base-patch16-224` (SigLIP 2 B/16) as an image and text embedding model, and its `vision_embedding_online.py` example sends `POST /v1/embeddings` with `messages` holding one `image_url` part and `encoding_format` `float` to a SigLIP server started with `vllm serve google/siglip-base-patch16-224 --runner pooling` |
| Jina (`https://api.jina.ai/v1/embeddings`) | Image input as objects, `{"image": "<URL or base64>"}`, documented for its v5-omni models; the page does not say whether data URLs work, and its models are not SigLIP 2 |
| Voyage | Own route `POST https://api.voyageai.com/v1/multimodalembeddings`, not `/v1/embeddings`, and models that are not SigLIP 2 |

To serve the SigLIP 2 model i2k needs, start vLLM like its SigLIP example, with the SigLIP 2 name:

```bash
vllm serve google/siglip2-base-patch16-224 --runner pooling
```

Checked: vLLM's own test of this route (`tests/entrypoints/pooling/embed/test_online_dual_encoder.py`) starts `google/siglip2-base-patch16-224` with `--runner pooling` and sends `POST /v1/embeddings` with `messages` holding one user message whose one part is an `image_url` with the image as a base64 `data:` URL, the request i2k sends, and expects one vector back. Not checked: nobody has run i2k against a vLLM server yet, and i2k's own SigLIP 2 is open_clip's copy of the same Google model (`ViT-B-16-SigLIP2/webli`), so whether vLLM's vectors, made with Hugging Face's copy and its image resizing, give the same tags is not measured. The other providers above take no image in `/v1/embeddings` in the shape i2k sends. With `providers.embeddings.host` empty, SigLIP 2 B/16 runs inside i2k as before.

```json
"hosts": {"vllm": {"url": "http://localhost:8000/v1", "apiKeyEnv": ""}},
"providers": {"embeddings": {"host": "vllm", "model": "google/siglip2-base-patch16-224", "timeoutS": 600}}
```

Sources, read for this section: OpenAI [speech to text](https://developers.openai.com/api/docs/guides/speech-to-text), [embeddings](https://developers.openai.com/api/docs/guides/embeddings) and [images](https://developers.openai.com/api/docs/guides/images-vision); [OpenRouter API overview](https://openrouter.ai/docs/api-reference/overview); [LM Studio OpenAI endpoints](https://lmstudio.ai/docs/app/api/endpoints/openai); [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility); vLLM [OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/), [multimodal inputs](https://docs.vllm.ai/en/latest/features/multimodal_inputs/), [embedding models](https://docs.vllm.ai/en/latest/models/pooling_models/embed/) and its example [`vision_embedding_online.py`](https://github.com/vllm-project/vllm/blob/main/examples/pooling/embed/vision_embedding_online.py); [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md); [Groq speech to text](https://console.groq.com/docs/speech-to-text); [Speaches](https://speaches.ai/) and its [speech-to-text page and `stt.py`](https://github.com/speaches-ai/speaches); [LocalAI audio to text](https://localai.io/docs/features/audio-to-text/); [Jina embeddings](https://jina.ai/embeddings/); [Voyage multimodal embeddings](https://docs.voyageai.com/docs/multimodal-embeddings).

## Inside i2k: text reading, face and person detection, the decision model

These models run where i2k runs, on the processor or the graphics card of that machine or container. They are part of the container image, except the decision model's 4.5 GB, which comes from the models volume.

| Model | What it answers | Where its files are | Size |
|---|---|---|---|
| Tesseract | The words on a frame or a cut, with their boxes | Installed in the image (`tesseract-ocr` and its language data) | in the image |
| OpenCV YuNet | Where the faces on a frame are | `weights/face_detection_yunet_2023mar.onnx`, shipped with i2k | 0.2 MB |
| OpenCV NanoDet | Where the people on a frame are | `weights/object_detection_nanodet_2022nov.onnx`, shipped with i2k | 3.8 MB |
| Liquid AI d1-2B | A yes-or-no question about a frame, with its probability | The Hugging Face cache, which the container keeps in `/models/huggingface` of the models volume | 4.5 GB |

- **Text reading, face detection and person detection** need nothing set: they run on the processor, and no provider kind can replace them. Faces score 0.6 or more to count, people 0.35 or more, and a person's box is widened by 10% on every side.
- **The decision model** is optional. The default way to drop frames that hold no knowledge is the SigLIP 2 model, served through the `embeddings` kind or in-process. Only a person who turns the decision model on needs d1-2B. It runs on the processor or on a CUDA card, from the models volume or from the Hugging Face cache of the machine, and needs `torch` and `transformers` (`tools/d1-knowledge-filter/requirements.txt`). Its first start downloads about 4.5 GB.

### Running the decision model natively, for speed on a Mac

A container on a Mac has no graphics card, so d1-2B inside it runs on the processor. To use the Mac's graphics card, run the decision model natively on the Mac, beside the container, and point i2k at it:

```
i2k serve-models
```

The command loads d1-2B on CUDA, then Apple's graphics card (MPS), then the processor, and serves two routes and no other:

| Route | What it does |
|---|---|
| `POST /v1/classifications` | A JSON body with `images`, a list of `data:` URLs, and one `question`. The reply is `results`, one `decision` (`keep` or `drop`) and one `score` (the probability the answer is yes) for each image, in the order sent. |
| `GET /v1/models` | The one model it serves, `LiquidAI/d1-2B`, which `i2k probe` reads. |

It listens on `127.0.0.1`, port 8790, so only this machine can reach it: the frames it receives come from your recordings and it has no sign-in. `--host` and `--port` change that. Its weights are read from the Hugging Face cache (`HF_HOME`) of the machine it runs on. One request is answered at a time, since one model holds the card.

Then add it as a host in `playbook.local.json` and name it for the one non-standard kind:

```json
"hosts": {"decision-server": {"url": "http://localhost:8790/v1", "apiKeyEnv": ""}},
"providers": {
  "decision": {"host": "decision-server", "model": "", "timeoutS": 600}
}
```

From a container, give that host the Mac's address: `http://host.containers.internal:8790/v1` with Podman, `http://host.docker.internal:8790/v1` with Docker, and start `i2k serve-models --host 0.0.0.0` on the Mac only when you accept that other machines of your network can reach it.

i2k sends 8 frames at a time as JPEG `data:` URLs, 640 pixels wide, and asks the five questions of the d1-2B tool one after another for each batch, the knowledge question first. It reads the `score`, builds the knowledge probability and the classes of empty frames from it, and applies `frames.decisionFilterCut` itself; the `decision` word is not read. With `providers.decision.host` empty, d1-2B runs inside i2k as described above. `decision` is the only kind a standard server does not offer.
