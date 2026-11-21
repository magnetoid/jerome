"""Download the public-domain Project Gutenberg texts listed in sources.toml.

    uv run python -m eval.fetch_texts [--force]

The raw files go to eval/data/ (gitignored, never committed).
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SOURCES = HERE / "sources.toml"
USER_AGENT = "jerome-fixture-fetch/0.1"
MAX_BYTES = 20 * 1024 * 1024
MIN_WORDS = 20
URLS = (
    "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt",
    "https://gutenberg.pglaf.org/cache/epub/{id}/pg{id}.txt",
)
_START = re.compile(r"^\*\*\* ?START OF (?:TH(?:E|IS) )?PROJECT GUTENBERG EBOOK.*$", re.M)
_END = re.compile(r"^\*\*\* ?END OF (?:TH(?:E|IS) )?PROJECT GUTENBERG EBOOK.*$", re.M)


def load_sources() -> dict[str, list[dict]]:
    with SOURCES.open("rb") as f:
        return tomllib.load(f)


def strip_gutenberg(text: str) -> str:
    """The text between the START and END markers. Raises ValueError if either is missing."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    start = _START.search(text)
    if start is None:
        raise ValueError("Project Gutenberg START marker not found")
    end = _END.search(text, start.end())
    if end is None:
        raise ValueError("Project Gutenberg END marker not found")
    return text[start.end() : end.start()]


def paragraphs_from(body: str) -> list[str]:
    """Blank-line-separated blocks as single-spaced lines; only those of >= 20 words."""
    out = []
    for block in re.split(r"\n\s*\n", body):
        para = " ".join(block.split())
        if len(para.split()) >= MIN_WORDS:
            out.append(para)
    return out


def load_paragraphs(ebook_id: int) -> list[str]:
    raw = (DATA / f"{ebook_id}.txt").read_text(encoding="utf-8-sig")
    return paragraphs_from(strip_gutenberg(raw))


def fetch(client: httpx.Client, ebook_id: int) -> str:
    errors = []
    for template in URLS:
        url = template.format(id=ebook_id)
        try:
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > MAX_BYTES:
                raise ValueError("over the size limit")
            text = response.content.decode("utf-8-sig")
            strip_gutenberg(text)  # validates the markers
            return text
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="download files that already exist")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    status = 0
    with httpx.Client(
        follow_redirects=True, timeout=60, headers={"User-Agent": USER_AGENT}
    ) as client:
        for language, entries in load_sources().items():
            for entry in entries:
                ebook_id = entry["id"]
                target = DATA / f"{ebook_id}.txt"
                if not target.exists() or args.force:
                    try:
                        target.write_text(fetch(client, ebook_id), encoding="utf-8")
                    except RuntimeError as exc:
                        print(f"FAILED {ebook_id}: {exc}", file=sys.stderr)
                        status = 1
                        continue
                print(f"{language} {ebook_id}: {len(load_paragraphs(ebook_id))} paragraphs")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
