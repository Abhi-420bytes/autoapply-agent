FROM python:3.11-slim

ARG TARGETARCH
ARG TECTONIC_VERSION=0.17.0
ARG TECTONIC_SHA256_ARM64=b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8
ARG TECTONIC_SHA256_AMD64=8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# poppler-utils: pdftotext for ATS text extraction. Tectonic: pinned + checksum-verified.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils curl ca-certificates fontconfig \
 && rm -rf /var/lib/apt/lists/* \
 && case "$TARGETARCH" in \
      arm64) T=aarch64-unknown-linux-musl; SUM=$TECTONIC_SHA256_ARM64 ;; \
      amd64) T=x86_64-unknown-linux-musl;  SUM=$TECTONIC_SHA256_AMD64 ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/tectonic.tgz \
      "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-${T}.tar.gz" \
 && echo "${SUM}  /tmp/tectonic.tgz" | sha256sum -c - \
 && tar -xzf /tmp/tectonic.tgz -C /usr/local/bin tectonic \
 && rm /tmp/tectonic.tgz && tectonic --version

WORKDIR /app
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data

# 1) Pre-warm Tectonic's package cache through the real compiler, so first compiles are
#    fast and a broken compile path fails the build. Placed before the dependency install
#    (it only needs PyMuPDF) so editing pyproject.toml doesn't redo the slow download.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv pip install --no-cache "pymupdf>=1.24" "playwright>=1.47" \
 && playwright install --with-deps chromium \
 && chmod -R a+rx /ms-playwright
COPY app/__init__.py ./app/__init__.py
COPY app/latex ./app/latex
COPY templates ./templates
USER app
RUN python -m app.latex.warmup
USER root

# 2) Dependencies (cached until pyproject.toml changes).
COPY pyproject.toml ./
ARG INSTALL_LOCAL_EMBEDDINGS=false
RUN if [ "$INSTALL_LOCAL_EMBEDDINGS" = "true" ]; then \
      uv pip install --no-cache -r pyproject.toml --extra local-embeddings; \
    else uv pip install --no-cache -r pyproject.toml; fi

# 3) Application code (cheap layers).
COPY app ./app
COPY scripts ./scripts
COPY alembic.ini ./
COPY alembic ./alembic
COPY prompts ./prompts
COPY portal_configs ./portal_configs
RUN uv pip install --no-cache --no-deps -e . && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
