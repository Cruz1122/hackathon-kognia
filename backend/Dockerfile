FROM python:3.13.15-slim-trixie AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /build

# Build-only tools and dependencies. The final image receives only /opt/venv
# and the prepared models, not this layer or its download tools.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        bzip2 \
        ca-certificates \
        curl \
        findutils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt constraints-docker-cpu.txt ./
RUN python -m venv "$VIRTUAL_ENV" \
    && python -m pip install --upgrade pip \
    && pip install --only-binary=:all: \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.14.0+cpu" \
    && pip install --constraint constraints-docker-cpu.txt -r requirements.txt

COPY scripts/download-e5-model.py ./scripts/download-e5-model.py

RUN mkdir -p models/sherpa-nemotron-35-560 \
    && curl --fail --location --show-error \
        -o /tmp/sherpa-nemotron.tar.bz2 \
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2" \
    && tar --no-same-owner -xjf /tmp/sherpa-nemotron.tar.bz2 -C models \
    && extracted="$(find models -maxdepth 1 -type d -name 'sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-*' -print -quit)" \
    && test -n "$extracted" \
    && cp -a "$extracted"/. models/sherpa-nemotron-35-560/ \
    && rm -rf "$extracted" /tmp/sherpa-nemotron.tar.bz2 \
    && test -f models/sherpa-nemotron-35-560/tokens.txt

RUN python -m piper.download_voices \
        --download-dir models/piper-es \
        es_MX-claude-high \
    && test -f models/piper-es/es_MX-claude-high.onnx \
    && test -f models/piper-es/es_MX-claude-high.onnx.json

RUN python scripts/download-e5-model.py models/multilingual-e5-small

FROM python:3.13.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/app/backend \
    PATH=/opt/venv/bin:$PATH \
    VIRTUAL_ENV=/opt/venv \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    SHERPA_MODEL_DIR=backend/models/sherpa-nemotron-35-560 \
    PIPER_MODEL_DIR=backend/models/piper-es \
    PIPER_TTS_VOICE=es_MX-claude-high \
    E5_MODEL_DIR=/app/backend/models/multilingual-e5-small

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        ffmpeg \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/backend

COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /build/models /app/backend/models
COPY --chown=appuser:appuser app /app/backend/app
COPY --chown=appuser:appuser vendor /app/backend/vendor
COPY --chown=appuser:appuser scripts/seed_dashboard_demo.py /app/backend/scripts/seed_dashboard_demo.py
COPY --chown=appuser:appuser scripts/bootstrap-demo-admin.sh /app/backend/scripts/bootstrap-demo-admin.sh
COPY --chown=appuser:appuser alembic.ini /app/backend/alembic.ini
COPY --chown=appuser:appuser migrations /app/backend/migrations
RUN pip install --no-cache-dir "pynacl==1.5.0" \
    && mkdir -p /var/lib/kognia/recordings /tmp/telnyx-captures \
    && chown -R appuser:appuser /var/lib/kognia /tmp/telnyx-captures

USER appuser

EXPOSE 18474

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "18474"]
