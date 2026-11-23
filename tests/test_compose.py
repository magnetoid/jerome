import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


def test_worker_gets_time_to_finish_its_chunk_before_it_is_killed():
    text = COMPOSE.read_text()
    worker = re.search(r"^  worker:\n((?:    .*\n|\n)+)", text, re.M)
    assert worker is not None
    assert re.search(r"^    stop_grace_period: 180s\b", worker.group(1), re.M)


def test_database_password_comes_from_env_with_a_local_default():
    text = COMPOSE.read_text()
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-jerome}" in text
    assert "jerome:${POSTGRES_PASSWORD:-jerome}@db:5432/jerome" in text
    assert "jerome:jerome@db" not in text  # api and worker must use the same password as db


def test_coolify_api_and_worker_share_the_secret_key():
    text = (COMPOSE.parent / "docker-compose.coolify.yml").read_text()
    assert text.count("JEROME_SECRET_KEY: ${JEROME_SECRET_KEY:-}") == 1  # in the shared x-app block
    assert "environment: *id002" in text
