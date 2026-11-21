import os
import shutil
import tempfile
from pathlib import Path

from jerome.errors import NotFound


class Storage:
    """Files of books under ``<root>/books/<id>/``."""

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

    def save_export(self, book_id: int, name: str, data: bytes) -> Path:
        return _write_atomic(self._book_dir(book_id) / "exports" / Path(name).name, data)

    def delete_book(self, book_id: int) -> None:
        shutil.rmtree(self._book_dir(book_id), ignore_errors=True)


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
