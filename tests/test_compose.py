import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.yml"


def test_worker_gets_time_to_finish_its_chunk_before_it_is_killed():
    text = COMPOSE.read_text()
    worker = re.search(r"^  worker:\n((?:    .*\n|\n)+)", text, re.M)
    assert worker is not None
    assert re.search(r"^    stop_grace_period: 180s\b", worker.group(1), re.M)
