# Test image for what needs system libraries: WeasyPrint (Pango), EPUBCheck (Java) and
# Tesseract (PDF import OCR).
# Build:  docker build -f docker/test.Dockerfile -t jerome-test .
# Run:    scripts/docker-test.sh tests/publish
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fontconfig epubcheck \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/venv UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project
