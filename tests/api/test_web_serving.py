import pytest
from fastapi.testclient import TestClient

from jerome.api.app import create_app

NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def web_dir(tmp_path):
    root = tmp_path / "site"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>app</html>")
    (root / "assets" / "app.js").write_text("console.log(1)")
    (root / "favicon.ico").write_bytes(b"ICO")
    (tmp_path / "secret.txt").write_text("top secret")
    (root / "secret.txt").write_text("top-level secret")
    return root


@pytest.fixture
def web_app_client(test_settings, web_dir):
    settings = test_settings.model_copy(update={"web_dir": web_dir})
    with TestClient(create_app(settings)) as c:
        yield c


def test_deep_link_returns_index_html_no_cache(web_app_client):
    for path in ("/", "/projects/3/chapters/2", "/login"):
        r = web_app_client.get(path)
        assert r.status_code == 200
        assert r.text == "<html>app</html>"
        assert r.headers["cache-control"] == NO_CACHE
        assert r.headers["content-type"].startswith("text/html")


def test_assets_are_long_cached(web_app_client):
    r = web_app_client.get("/assets/app.js")
    assert r.status_code == 200
    assert r.text == "console.log(1)"
    assert r.headers["cache-control"] == IMMUTABLE


def test_missing_asset_is_404_not_index(web_app_client):
    r = web_app_client.get("/assets/nope.js")
    assert r.status_code == 404
    assert "<html>" not in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/assets/..%2Fsecret.txt",
        "/assets/..%2F..%2Fsecret.txt",
        "/assets/%2e%2e/secret.txt",
        "/assets//etc/passwd",
    ],
)
def test_asset_traversal_never_escapes(web_app_client, path):
    r = web_app_client.get(path)
    assert "secret" not in r.text
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.text == "<html>app</html>"


def test_assets_directory_itself_is_404(web_app_client):
    assert web_app_client.get("/assets/").status_code == 404


def test_top_level_file_is_served_no_cache(web_app_client):
    r = web_app_client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.content == b"ICO"
    assert r.headers["cache-control"] == NO_CACHE


def test_api_and_docs_paths_are_not_swallowed(web_app_client):
    r = web_app_client.get("/v1/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    assert web_app_client.get("/v1/auth/me").status_code == 401
    assert web_app_client.get("/openapi.json").json()["info"]["title"] == "Jerome"
    assert "swagger" in web_app_client.get("/docs").text.lower()
    assert web_app_client.get("/redoc").status_code == 200


def test_no_web_dir_means_api_only(client):
    assert client.get("/").status_code == 404
    assert client.get("/projects/1").status_code == 404
