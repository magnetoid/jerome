"""Run EPUBCheck on an EPUB in tests; skip when it is not installed."""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from jerome.publish.build import epubcheck_command


def run_epubcheck(data: bytes) -> list[str]:
    """EPUBCheck's ERROR and FATAL messages for ``data`` (warnings are not errors)."""
    command = epubcheck_command()
    if command is None:
        pytest.skip("epubcheck is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        book, report = Path(tmp) / "book.epub", Path(tmp) / "report.json"
        book.write_bytes(data)
        subprocess.run(
            [*command, str(book), "--json", str(report)], capture_output=True, timeout=300
        )
        messages = json.loads(report.read_text())["messages"]
    return [
        f"{m['ID']}: {m['message']} "
        + ", ".join(f"{loc.get('path')}:{loc.get('line')}" for loc in m.get("locations", []))
        for m in messages
        if m.get("severity") in ("ERROR", "FATAL")
    ]
