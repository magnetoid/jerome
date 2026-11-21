import importlib.util
from pathlib import Path

import httpx
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "fetch_public_fixtures",
    Path(__file__).resolve().parent.parent / "scripts" / "fetch_public_fixtures.py",
)
fetch_script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fetch_script)


def test_oversized_download_aborts_and_does_not_try_next_url(monkeypatch):
    monkeypatch.setattr(fetch_script, "MAX_BYTES", 1000)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"x" * 5000)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(fetch_script.TooLarge):
        fetch_script.fetch(client, 932, "en")
    assert len(calls) == 1


def test_container_without_rootfile_is_invalid_not_a_crash():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            b"<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'/>",
        )
    with pytest.raises(ValueError):
        fetch_script.check_epub(buf.getvalue(), "en")
