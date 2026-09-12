FROM python:3.13.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/app/backend \
    SHERPA_MODEL_DIR=backend/models/sherpa-es \
    PIPER_MODEL_DIR=backend/models/piper-es \
    PIPER_TTS_VOICE=es_MX-claude-high

WORKDIR /app

# Runtime tools required by audio transcription and local TTS.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        bzip2 \
        ca-certificates \
        curl \
        ffmpeg \
        findutils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt backend/requirements.txt
RUN python -m pip install --no-cache-dir -r backend/requirements.txt

COPY app backend/app

# Download the speech-recognition model into the backend image.
RUN mkdir -p backend/models/sherpa-es \
    && curl --fail --location --show-error \
        -o /tmp/sherpa-es.tar.bz2 \
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06.tar.bz2" \
    && tar -xjf /tmp/sherpa-es.tar.bz2 -C backend/models \
    && extracted="$(find backend/models -maxdepth 1 -type d -name 'sherpa-onnx-streaming-zipformer-es-kroko-*' | head -n 1)" \
    && test -n "$extracted" \
    && cp -a "$extracted"/. backend/models/sherpa-es/ \
    && rm -rf "$extracted" /tmp/sherpa-es.tar.bz2 \
    && test -f backend/models/sherpa-es/tokens.txt

# Piper model — local TTS voice used by /synthesize and the voice call demo.
RUN python -m piper.download_voices \
        --download-dir "$PIPER_MODEL_DIR" \
        "$PIPER_TTS_VOICE" \
    && test -f "$PIPER_MODEL_DIR/$PIPER_TTS_VOICE.onnx" \
    && test -f "$PIPER_MODEL_DIR/$PIPER_TTS_VOICE.onnx.json"

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 18474

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "18474"]
