"""Download the two public-domain Project Gutenberg EPUBs used as test fixtures.

    uv run python scripts/fetch_public_fixtures.py [--force]

Only these two books are allowed in the (public) repository. The downloaded bytes are
written unchanged, so the Project Gutenberg license header stays intact.
"""

import argparse
import io
import sys
import zipfile
from pathlib import Path

import httpx
from lxml import etree

DEST = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "public"
MAX_BYTES = 5 * 1024 * 1024
USER_AGENT = "jerome-fixture-fetch/0.1"
# file name -> (Project Gutenberg ebook id, expected dc:language)
BOOKS = {"pg932.epub": (932, "en"), "pg22367.epub": (22367, "de")}
URLS = (
    "https://www.gutenberg.org/ebooks/{id}.epub3.images",
    "https://www.gutenberg.org/ebooks/{id}.epub.noimages",
    "https://www.gutenberg.org/cache/epub/{id}/pg{id}-images-3.epub",
    "https://gutenberg.pglaf.org/cache/epub/{id}/pg{id}-images-3.epub",
)
_DC = "http://purl.org/dc/elements/1.1/"
_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"


class TooLarge(Exception):
    """A download exceeded MAX_BYTES; the script stops instead of trying another URL."""


def _parse(data: bytes) -> etree._Element:
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def check_epub(data: bytes, language: str) -> None:
    """Raise ValueError unless ``data`` is an EPUB whose dc:language is ``language``."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if zf.read("mimetype").strip() != b"application/epub+zip":
            raise ValueError("mimetype is not application/epub+zip")
        container = _parse(zf.read("META-INF/container.xml"))
        rootfile = container.find(f".//{_CONTAINER}")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("container.xml has no rootfile")
        opf = _parse(zf.read(rootfile.get("full-path")))
    found = [el.text.strip() for el in opf.iter(f"{{{_DC}}}language") if el.text]
    if not any(lang.lower().startswith(language) for lang in found):
        raise ValueError(f"dc:language is {found}, expected {language}")


def fetch(client: httpx.Client, ebook_id: int, language: str) -> tuple[bytes, str]:
    errors = []
    for template in URLS:
        url = template.format(id=ebook_id)
        try:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise TooLarge(f"{url} is over the {MAX_BYTES} byte limit")
                    chunks.append(chunk)
                final_url = str(response.url)
            data = b"".join(chunks)
            check_epub(data, language)
            return data, final_url
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            zipfile.BadZipFile,
            etree.XMLSyntaxError,
        ) as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="download files that already exist")
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    status = 0
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(follow_redirects=True, timeout=60, headers=headers) as client:
        for name, (ebook_id, language) in BOOKS.items():
            target = DEST / name
            if target.exists() and not args.force:
                print(f"skip {name}: exists")
                continue
            try:
                data, url = fetch(client, ebook_id, language)
            except TooLarge as exc:
                print(f"STOP {name}: {exc}", file=sys.stderr)
                return 2
            except RuntimeError as exc:
                print(f"FAILED {name}: {exc}", file=sys.stderr)
                status = 1
                continue
            target.write_bytes(data)
            print(f"wrote {name}: {len(data)} bytes from {url}")
    return status


if __name__ == "__main__":
    sys.exit(main())
