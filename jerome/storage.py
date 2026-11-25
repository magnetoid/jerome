import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from jerome.errors import NotFound

TRANSFERS = "transfers"


class Storage:
    """Files of books under ``<root>/books/<id>/``; files waiting behind a single-use
    transfer link under ``<root>/transfers/<link id>``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _book_dir(self, book_id: int) -> Path:
        return self.root / "books" / str(book_id)

    def save_original(self, book_id: int, filename: str, data: bytes) -> Path:
        ext = Path(filename).suffix.lower()
        return _write_atomic(self._book_dir(book_id) / f"original{ext}", data)

    def read_original(self, book_id: int) -> bytes:
        found = sorted(self._book_dir(book_id).glob("original.*"))
        if not found:
            raise NotFound(f"original file of book {book_id}")
        return found[0].read_bytes()

    def save_source_pdf(self, book_id: int, data: bytes) -> Path:
        return _write_atomic(self._book_dir(book_id) / "source.pdf", data)

    def read_source_pdf(self, book_id: int) -> bytes:
        path = self._book_dir(book_id) / "source.pdf"
        if not path.is_file():
            raise NotFound(f"source PDF of book {book_id}")
        return path.read_bytes()

    def save_page(self, book_id: int, page: int, d: dict) -> Path:
        """The result of reading one page (0-based), kept so a resumed import skips it."""
        raw = json.dumps(d, ensure_ascii=False).encode()
        return _write_atomic(self._book_dir(book_id) / "import" / f"page-{page:04d}.json", raw)

    def read_page(self, book_id: int, page: int) -> dict | None:
        path = self._book_dir(book_id) / "import" / f"page-{page:04d}.json"
        return json.loads(path.read_bytes()) if path.is_file() else None

    def save_export(self, book_id: int, name: str, data: bytes) -> Path:
        return _write_atomic(self._book_dir(book_id) / "exports" / Path(name).name, data)

    def read_export(self, book_id: int, name: str) -> bytes:
        """An export by its bare file name; a name with a path in it is refused."""
        if not name or name in (".", "..") or Path(name).name != name or "/" in name:
            raise ValueError(f"not an export name: {name!r}")
        try:
            return (self._book_dir(book_id) / "exports" / name).read_bytes()
        except FileNotFoundError:
            raise NotFound(f"export {name} of book {book_id}") from None

    def save_cover(self, book_id: int, data: bytes, ext: str) -> Path:
        """Store the cover as ``cover.<ext>``; a cover of another type is removed."""
        path = _write_atomic(self._book_dir(book_id) / f"cover.{ext}", data)
        for other in self._book_dir(book_id).glob("cover.*"):
            if other != path:
                other.unlink(missing_ok=True)
        return path

    def read_cover(self, book_id: int) -> bytes | None:
        found = sorted(self._book_dir(book_id).glob("cover.*"))
        return found[0].read_bytes() if found else None

    def delete_cover(self, book_id: int) -> None:
        for path in self._book_dir(book_id).glob("cover.*"):
            path.unlink(missing_ok=True)

    def delete_book(self, book_id: int) -> None:
        shutil.rmtree(self._book_dir(book_id), ignore_errors=True)

    def save_transfer(self, link_id: int, data: bytes) -> str:
        """Write a link's file atomically; returns its path relative to the root."""
        rel = f"{TRANSFERS}/{int(link_id)}"
        _write_atomic(self._transfer_path(rel), data)
        return rel

    def read_transfer(self, rel: str) -> bytes:
        try:
            return self._transfer_path(rel).read_bytes()
        except FileNotFoundError:
            raise NotFound("transfer file") from None

    def delete_transfer(self, rel: str) -> None:
        self._transfer_path(rel).unlink(missing_ok=True)

    def _transfer_path(self, rel: str) -> Path:
        """Only ``transfers/<digits>``: a stored path can never point anywhere else."""
        parts = PurePosixPath(rel).parts
        if len(parts) != 2 or parts[0] != TRANSFERS or not parts[1].isdigit():
            raise ValueError(f"not a transfer path: {rel!r}")
        return self.root / TRANSFERS / parts[1]


def _write_atomic(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
