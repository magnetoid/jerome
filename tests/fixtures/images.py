"""Tiny generated images for cover and picture tests."""

import io
import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def make_png(width: int, height: int, rgb: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    """A valid one-colour RGB PNG."""
    row = b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def make_jpeg_header(width: int, height: int) -> bytes:
    """The start of a baseline JPEG: SOI, a JFIF APP0 and the SOF0 frame header (enough to
    read the size; not decodable)."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def make_jpeg(width: int, height: int) -> bytes:
    """A real, decodable one-colour JPEG."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(out, "JPEG", quality=60)
    return out.getvalue()


def corrupt_png(width: int, height: int) -> bytes:
    """A PNG whose header and size are right but whose image data is garbage."""
    good = make_png(width, height)
    start = good.index(b"IDAT") + 4
    return good[:start] + b"\x00" * 64 + good[start + 64 :]


GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"
