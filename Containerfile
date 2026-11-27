# info-to-knowledge in a container: the web page and the server that runs the pipeline. Builds with Podman or Docker:
#   podman build -t info-to-knowledge -f Containerfile .
#   podman compose up --build            # see compose.yaml and docs/container.md
# No model is inside the image: recordings, run results, settings and every downloaded model are mounted from the
# host. Language models run outside the container, in LM Studio, Ollama or llama.cpp, and are reached by URL.

# 1. The web page, built once; the second stage keeps only its built files, so the image needs no Node.
FROM docker.io/library/node:22.12.0-bookworm-slim AS page
WORKDIR /page
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# 2. The server and the pipeline.
FROM docker.io/library/python:3.12.8-slim-bookworm

# ffmpeg cuts and decodes the video, tesseract reads text on frames and scanned pages, pandoc reads Word, PowerPoint and
# OpenDocument files. Set WITH_OFFICE_TOOLS=1 to add LibreOffice Impress (`soffice`, turns .odt and slides into .docx
# and PDF) and librsvg (`rsvg-convert`, draws SVG pictures): about 700 MB more.
ARG WITH_OFFICE_TOOLS=0
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu pandoc \
    && if [ "$WITH_OFFICE_TOOLS" = "1" ]; then \
        apt-get install -y --no-install-recommends libreoffice-impress librsvg2-bin; \
    fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The Python packages before the code, so a code change does not install them again. The CPU build of torch: the
# container has no graphics card it can use, and the default build brings CUDA libraries of several GB.
COPY requirements.txt requirements-diarization.txt ./
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt -r requirements-diarization.txt

COPY . ./
COPY --from=page /page/dist web/dist

# The program is installed in place, so the `i2k` command runs the code in /app and reads its settings from /app.
RUN pip install --no-cache-dir --no-deps -e .

# I2K_WORKSPACE holds inbox/, output/ and ui/; I2K_MODELS_DIR and HF_HOME hold every downloaded model.
ENV I2K_PROFILE=container \
    I2K_WORKSPACE=/data \
    I2K_MODELS_DIR=/models \
    HF_HOME=/models/huggingface \
    PYTHONUNBUFFERED=1
VOLUME ["/data", "/models"]
EXPOSE 8765

# The default runs the server; any `i2k ...` command can be given instead.
CMD ["python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8765"]
