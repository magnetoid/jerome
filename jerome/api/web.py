import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"
_API_PREFIXES = ("v1", "fonts", "docs", "redoc", "openapi.json")


def _inside(root: Path, relative: str) -> Path | None:
    """The regular file at root/relative, or None if it is missing or escapes root."""
    candidate = (root / relative).resolve()
    if candidate.is_file() and candidate.is_relative_to(root):
        return candidate
    return None


FONTS = Path(__file__).resolve().parent.parent / "publish" / "fonts"
_FONT_NAME = re.compile(r"^[A-Za-z0-9-]+\.ttf$")


def mount_fonts(app: FastAPI) -> None:
    """The book fonts, for the preview's book view. Open to all: they are free (OFL)."""

    @app.get("/fonts/{name}", include_in_schema=False)
    def font(name: str) -> FileResponse:
        file = FONTS / name
        if not _FONT_NAME.match(name) or not file.is_file():
            raise HTTPException(404)
        return FileResponse(file, media_type="font/ttf", headers={"Cache-Control": IMMUTABLE})


def mount_web(app: FastAPI, web_dir: Path) -> None:
    """Serve the built single-page app. Register after every API router: this is a catch-all."""
    root = web_dir.resolve()
    assets = root / "assets"

    @app.get("/assets/{path:path}", include_in_schema=False)
    def asset(path: str) -> FileResponse:
        file = _inside(assets.resolve(), path)
        if file is None:
            raise HTTPException(404)
        return FileResponse(file, headers={"Cache-Control": IMMUTABLE})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/", 1)[0] in _API_PREFIXES:
            raise HTTPException(404)
        # A file at the top of web_dir (a favicon from Vite's public/), else the app shell.
        file = _inside(root, path) if path and "/" not in path else None
        return FileResponse(file or root / "index.html", headers={"Cache-Control": NO_CACHE})
