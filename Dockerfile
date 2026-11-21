FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY jerome/ jerome/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

ENV PATH=/app/.venv/bin:$PATH \
    JEROME_DATA_DIR=/data

RUN useradd --create-home --uid 1000 jerome \
    && mkdir -p /data \
    && chown jerome:jerome /data
USER jerome

EXPOSE 8000
CMD ["jerome", "serve", "--host", "0.0.0.0"]
