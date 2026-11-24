#!/bin/sh
# Run pytest in the Docker test image, on this checkout, against the host's test database.
# Usage: scripts/docker-test.sh [pytest args]   (default: tests/publish)
set -e
cd "$(dirname "$0")/.."
docker build -q -f docker/test.Dockerfile -t jerome-test . >/dev/null
db="${JEROME_TEST_DATABASE_URL:-postgresql+psycopg://jerome:jerome@localhost:5432/jerome_test}"
db=$(printf %s "$db" | sed 's/@localhost:/@host.docker.internal:/; s/@127\.0\.0\.1:/@host.docker.internal:/')
exec docker run --rm -v "$PWD:/app" -e JEROME_TEST_DATABASE_URL="$db" jerome-test \
    uv run --frozen pytest -q "${@:-tests/publish}"
