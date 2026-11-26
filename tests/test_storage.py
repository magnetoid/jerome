import pytest

from jerome.errors import NotFound
from jerome.storage import Storage


def test_cover_round_trip(tmp_path):
    s = Storage(tmp_path)
    assert s.read_cover(1) is None
    s.save_cover(1, b"png", "png")
    s.save_cover(1, b"jpg", "jpg")
    assert sorted(p.name for p in (tmp_path / "books" / "1").glob("cover.*")) == ["cover.jpg"]
    assert s.read_cover(1) == b"jpg"
    s.delete_cover(1)
    assert s.read_cover(1) is None
    s.delete_cover(1)  # nothing to delete is fine


def test_read_export(tmp_path):
    s = Storage(tmp_path)
    s.save_export(1, "book-7.pdf", b"%PDF")
    assert s.read_export(1, "book-7.pdf") == b"%PDF"
    with pytest.raises(NotFound):
        s.read_export(1, "missing.pdf")
    for bad in ("../x", "a/b.pdf", "..", ""):
        with pytest.raises(ValueError):
            s.read_export(1, bad)


def test_source_pdf_and_pages_round_trip(tmp_path):
    storage = Storage(tmp_path)
    storage.save_source_pdf(7, b"%PDF-1.7 x")
    assert storage.read_source_pdf(7) == b"%PDF-1.7 x"
    assert storage.read_page(7, 0) is None and not storage.has_page(7, 0)
    storage.save_page(7, 0, {"page": 0, "text": "Grüße"})
    assert storage.has_page(7, 0) and not storage.has_page(7, 1)
    assert storage.read_page(7, 0) == {"page": 0, "text": "Grüße"}
    assert (tmp_path / "books" / "7" / "import" / "page-0000.json").is_file()
    storage.save_page(7, 123, {"page": 123})
    assert (tmp_path / "books" / "7" / "import" / "page-0123.json").is_file()
    assert storage.read_page(7, 123) == {"page": 123}
    with pytest.raises(NotFound):
        storage.read_source_pdf(8)
