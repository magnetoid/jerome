"""A translation through the REST API, start to finish: import, translate, proofread with the
source in view, accept a fix, export, and read the export back. Once for a DOCX built in code
(English to German) and once for the public German EPUB (German to English).

The shared ``client`` fixture runs on a savepoint session that never commits, so the worker
(which opens its own sessions) would never see its data. This module builds the app on real
transactions instead; ``session_factory`` truncates afterwards.
"""

import io
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy.orm import sessionmaker

from jerome.api.app import create_app
from jerome.api.deps import get_registry
from jerome.core.keys import create_api_key
from jerome.documents.docx_xml import NS, read_package
from jerome.documents.epub_reader import read_epub
from jerome.documents.model import PLACEHOLDER
from jerome.models import FakeProvider
from tests.fixtures.docx_builders import all_blocks, make_docx
from tests.fixtures.translation import deps_for, drain, echo, quiet_languagetool, registry_for

PUBLIC = Path(__file__).resolve().parent / "fixtures" / "public"


@pytest.fixture
def e2e(test_settings, session_factory):
    """(client, auth, provider, worker deps): the client on real transactions, one fake
    provider for the translate and AI passes, and a LanguageTool server that finds nothing."""
    provider = FakeProvider([], token_count=1000)
    registry = registry_for(test_settings, provider, ai=True)
    with session_factory() as s:
        key = create_api_key(s, "e2e")
        s.commit()
    app = create_app(test_settings)  # the real get_session: every request commits
    app.dependency_overrides[get_registry] = lambda: registry
    with TestClient(app) as c:
        yield (
            c,
            {"Authorization": f"Bearer {key}"},
            provider,
            deps_for(registry, quiet_languagetool()),
        )


def one_fix(prefix: str, pick: str | None = None):
    """An AI reply proposing one ``translation`` fix: upper-case the word right after
    ``prefix`` in the first block where that word (``pick``, or any word with lower-case
    letters) is. Later calls propose nothing. ``reply.fix`` records the fix as (block id,
    original, replacement)."""
    word = re.escape(pick) if pick else "[A-Z]?[a-z]{3,}"
    pattern = re.compile(rf'<block id="(\d+)">\n{PLACEHOLDER}*{re.escape(prefix)}({word})')

    def reply(kwargs: dict) -> dict:
        if reply.fix is not None or (m := pattern.search(kwargs["user"])) is None:
            return {"suggestions": []}
        assert "<source_block" in kwargs["user"]  # proofreading sees the source
        reply.fix = (int(m[1]), m[2], m[2].upper())
        return {
            "suggestions": [
                {
                    "block_id": reply.fix[0],
                    "original": reply.fix[1],
                    "replacement": reply.fix[2],
                    "category": "translation",
                    "severity": "medium",
                    "explanation": "Closer to the source.",
                    "occurrence": 1,
                }
            ]
        }

    reply.fix = None
    return reply


def stage(client, auth, book_id) -> str:
    (row,) = [r for r in client.get("/v1/projects", headers=auth).json() if r["id"] == book_id]
    return row["stage"]


def translate_and_proofread(e2e, *, data, filename, src, tgt, prefix, pick=None):
    """Steps 1 to 4: upload, translate, proofread, accept the one fix. Returns (book id,
    the fix as (block id, original, replacement))."""
    client, auth, provider, deps = e2e
    r = client.post(
        "/v1/books",
        headers=auth,
        files={"file": (filename, data)},
        # auto-apply off: the fix stays open, for the editor to accept
        data={"variant": src, "work": "translate", "target_variant": tgt, "auto_apply": "false"},
    )
    assert r.status_code == 201, r.text
    book_id = r.json()["id"]
    assert stage(client, auth, book_id) == "set_up"

    est = client.post(f"/v1/books/{book_id}/estimate", json={"type": "translate"}, headers=auth)
    assert est.status_code == 200, est.text
    assert est.json()["chunks"] >= 1
    job = client.post(
        f"/v1/books/{book_id}/jobs", json={"type": "translate", "max_cost": 100}, headers=auth
    )
    assert job.status_code == 201, job.text
    assert stage(client, auth, book_id) == "translating"
    provider.responses = echo(n=1000, prefix=prefix)
    drain(sessionmaker(client.app.state.engine), deps)
    assert client.get(f"/v1/jobs/{job.json()['id']}", headers=auth).json()["status"] == (
        "completed"
    )
    assert stage(client, auth, book_id) == "translated"

    ai = one_fix(prefix, pick)
    provider.responses = [ai] * 1000
    proof = client.post(
        f"/v1/books/{book_id}/jobs", json={"type": "proofread", "max_cost": 100}, headers=auth
    )
    assert proof.status_code == 201, proof.text
    drain(sessionmaker(client.app.state.engine), deps)
    assert client.get(f"/v1/jobs/{proof.json()['id']}", headers=auth).json()["status"] == (
        "completed"
    )
    assert stage(client, auth, book_id) == "to_review"

    assert ai.fix is not None
    found = client.get(
        f"/v1/books/{book_id}/suggestions", params={"source": "model"}, headers=auth
    ).json()
    (suggestion,) = found
    assert (suggestion["block_id"], suggestion["original"], suggestion["replacement"]) == ai.fix
    assert (suggestion["category"], suggestion["status"]) == ("translation", "open")
    accepted = client.post(f"/v1/suggestions/{suggestion['id']}/accept", headers=auth)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    return book_id, ai.fix


def export(client, auth, book_id, fmt) -> bytes:
    r = client.get(f"/v1/books/{book_id}/export", params={"format": fmt}, headers=auth)
    assert r.status_code == 200, r.text
    return r.content


def footnote_links(data: bytes) -> tuple[list[str], set[str]]:
    """The ``w:id`` of every footnote reference in the body, and of every normal footnote."""
    package = read_package(data)
    w = NS["w"]
    refs = [
        el.get(f"{{{w}}}id")
        for el in etree.fromstring(package["word/document.xml"]).iter(f"{{{w}}}footnoteReference")
    ]
    notes = {
        el.get(f"{{{w}}}id")
        for el in etree.fromstring(package["word/footnotes.xml"]).iter(f"{{{w}}}footnote")
        if el.get(f"{{{w}}}type") is None
    }
    return refs, notes


def languages(data: bytes) -> set[str]:
    w = NS["w"]
    found: set[str] = set()
    for part in ("word/styles.xml", "word/document.xml", "word/footnotes.xml"):
        root = etree.fromstring(read_package(data)[part])
        found |= {el.get(f"{{{w}}}val") for el in root.iter(f"{{{w}}}lang")}
    return found


def toc_labels(data: bytes) -> tuple[list[str], list[str]]:
    """The labels of the nav document's TOC links and of the NCX navMap, in order."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        nav = etree.fromstring(zf.read("OEBPS/toc.xhtml"))
        ncx = etree.fromstring(zf.read("OEBPS/toc.ncx"))
    ns = {"x": "http://www.w3.org/1999/xhtml", "n": "http://www.daisy.org/z3986/2005/ncx/"}
    toc = '//x:nav[@epub:type="toc"]//x:a'
    ns["epub"] = "http://www.idpf.org/2007/ops"
    return (
        ["".join(a.itertext()).strip() for a in nav.xpath(toc, namespaces=ns)],
        [t.text.strip() for t in ncx.xpath("//n:navMap//n:navLabel/n:text", namespaces=ns)],
    )


def test_docx_translated_proofread_and_exported(e2e):
    client, auth, _, _ = e2e
    data = make_docx(["# One", "First para.[^1]", "Second para."], footnotes={1: "A *short* note."})
    book_id, (block_id, original, replacement) = translate_and_proofread(
        e2e, data=data, filename="b.docx", src="en-GB", tgt="de-DE", prefix="DE ", pick="Second"
    )
    assert (original, replacement) == ("Second", "SECOND")

    out = export(client, auth, book_id, "docx-clean")
    texts = [b.text for b in all_blocks(out)]
    assert all(t.lstrip(PLACEHOLDER).startswith("DE ") for t in texts), texts
    # the fix is in; the footnote reference stays at the end of its paragraph, and the note's
    # own mark at the start of the note
    assert texts == [
        "DE One",
        f"DE First para.{PLACEHOLDER}",
        "DE SECOND para.",
        f"{PLACEHOLDER}DE A short note.",
    ]
    assert languages(out) == {"de-DE"}
    refs, notes = footnote_links(out)
    assert refs == ["1"] and notes == {"1"}  # the reference still points at its note
    chapters = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()
    assert [c["title"] for c in chapters] == ["DE One"]


def test_public_german_epub_translated_proofread_and_exported(e2e):
    client, auth, _, _ = e2e
    data = (PUBLIC / "pg22367.epub").read_bytes()
    book_id, (block_id, original, replacement) = translate_and_proofread(
        e2e, data=data, filename="pg22367.epub", src="de-DE", tgt="en-GB", prefix="EN "
    )

    data = export(client, auth, book_id, "epub-clean")
    out = read_epub(data)
    assert out.language == "en-GB"
    exported = [b.text for c in out.chapters for b in c.blocks]
    assert len(exported) > 100
    assert all(t.lstrip(PLACEHOLDER).startswith("EN ") for t in exported), [
        t for t in exported if not t.lstrip(PLACEHOLDER).startswith("EN ")
    ][:5]
    fixed = client.get(f"/v1/books/{book_id}/blocks/{block_id}", headers=auth).json()
    assert fixed["current"] == fixed["original"].replace(f"EN {original}", f"EN {replacement}", 1)
    assert fixed["current"] != fixed["original"] and fixed["current"] in exported
    assert fixed["original"] not in exported
    # the TOC labels are the translated chapter titles, in the nav document and the NCX
    chapters = client.get(f"/v1/books/{book_id}/chapters", headers=auth).json()
    titles = [c["title"] for c in chapters]
    assert len(titles) == 6 and all(t.startswith("EN ") for t in titles), titles
    assert [c.title for c in out.chapters] == titles
    nav, ncx = toc_labels(data)
    assert nav == ncx == titles[1:]  # neither has an entry for the Gutenberg front matter
