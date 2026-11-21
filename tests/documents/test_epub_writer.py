import io
import random
import zipfile
from pathlib import Path
from zipfile import ZIP_STORED

import pytest

from jerome.documents.changes import Change, apply_changes, validate_changes
from jerome.documents.epub_reader import read_epub
from jerome.documents.epub_writer import write_epub
from jerome.documents.epub_xml import is_block_text
from jerome.documents.model import PLACEHOLDER, Mark, MarkKind, ParsedBlock
from jerome.errors import InvalidChangeError
from tests.fixtures.epub_builders import (
    all_entries_except,
    block,
    block_key,
    edit_entry,
    make_epub,
    unchanged_entries,
    zip_info,
    zip_names,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CH1 = "OEBPS/ch1.xhtml"
_DOCTYPE = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
    '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
)
_HTML = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'


def _pg932() -> bytes:
    path = FIXTURES / "public" / "pg932.epub"
    if not path.exists():
        pytest.skip("pg932.epub missing: run scripts/fetch_public_fixtures.py")
    return path.read_bytes()


def _with_entry(data: bytes, name: str, content: bytes) -> bytes:
    """The EPUB with entry ``name`` replaced by ``content`` (order and compression kept)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, content if info.filename == name else src.read(info))
    return out.getvalue()


def _page(body: str, *, prolog: str = '<?xml version="1.0" encoding="UTF-8"?>\n') -> str:
    return f"{prolog}{_HTML}<head><title>C</title></head><body>{body}</body></html>\n"


def _epub_with_page(page: str | bytes) -> bytes:
    """A one-chapter EPUB whose chapter file is ``page``."""
    data = page.encode() if isinstance(page, str) else page
    return _with_entry(make_epub([("C", ["x"])]), CH1, data)


def _entry(data: bytes, name: str = CH1) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name)


def _blocks(data: bytes) -> list[ParsedBlock]:
    return [b for c in read_epub(data).chapters for b in c.blocks]


def _only(data: bytes) -> ParsedBlock:
    (b,) = _blocks(data)
    return b


# --- from the brief -------------------------------------------------------------------


def test_roundtrip_without_changes():
    src = _pg932()
    out = write_epub(src, {})
    assert read_epub(out) == read_epub(src)
    assert zip_names(out)[0] == "mimetype" and zip_info(out, "mimetype").compress_type == ZIP_STORED


def test_untouched_files_byte_identical():
    src = make_epub([("One", ["a"]), ("Two", ["teh"])])
    key = block_key(src, chapter=1, block=1)
    out = write_epub(src, {key: [Change(0, 3, "the")]})
    assert unchanged_entries(src, out) == all_entries_except(src, key.split("#")[0])


def test_change_across_inline_elements_keeps_first_replaced_char_formatting():
    src = make_epub([("C", ["Hello *wor*ld"])])
    out = write_epub(src, {block_key(src, 0, 1): [Change(6, 11, "WORLD")]})
    assert block(out, 0, 1).text == "Hello WORLD"
    assert block(out, 0, 1).marks == [Mark(6, 11, MarkKind.ITALIC)]  # it lands in the <i>


# --- where new text goes --------------------------------------------------------------


def test_insertion_takes_previous_char_formatting():
    src = make_epub([("C", ["Hello *wor*ld"])])
    out = write_epub(src, {block_key(src, 0, 1): [Change(9, 9, "x"), Change(11, 11, "!")]})
    assert block(out, 0, 1).text == "Hello worxld!"
    assert block(out, 0, 1).marks == [Mark(6, 10, MarkKind.ITALIC)]


def test_insertion_at_block_start_goes_into_first_text_piece():
    src = make_epub([("C", ["**Bold** start"])])
    out = write_epub(src, {block_key(src, 0, 1): [Change(0, 0, "Very ")]})
    assert block(out, 0, 1).text == "Very Bold start"
    assert block(out, 0, 1).marks == [Mark(0, 9, MarkKind.BOLD)]


def test_replacement_of_whole_formatted_word_keeps_its_format():
    src = make_epub([("C", ["A *Hamlte* play"])])
    out = write_epub(src, {block_key(src, 0, 1): [Change(2, 8, "Hamlet")]})
    assert block(out, 0, 1).text == "A Hamlet play"
    assert block(out, 0, 1).marks == [Mark(2, 8, MarkKind.ITALIC)]


def test_text_is_escaped():
    src = make_epub([("C", ["a b"])])
    out = write_epub(src, {block_key(src, 0, 1): [Change(1, 2, " <&> ")]})
    assert block(out, 0, 1).text == "a <&> b"


def test_change_over_collapsed_whitespace_replaces_whole_run():
    src = _epub_with_page(_page("<p>\n  Hello   <b>big\n</b>  world \n</p>"))
    assert _only(src).text == "Hello big world"
    out = write_epub(src, {f"{CH1}#0": [Change(5, 6, "_")]})
    assert _only(out).text == "Hello_big world"
    assert b"<p>\n  Hello_<b>big\n</b>  world \n</p>" in _entry(out)  # leading/trailing kept


def test_whitespace_collapsed_across_nodes_goes_with_its_space():
    # The space after "big" is the "\n" in <b>; the two spaces after </b> collapsed into it.
    src = _epub_with_page(_page("<p>Hello <b>big\n</b>  world</p>"))
    key = f"{CH1}#0"
    assert _only(write_epub(src, {key: [Change(9, 10, "")]})).text == "Hello bigworld"
    assert _only(write_epub(src, {key: [Change(9, 10, "-")]})).text == "Hello big-world"
    # A letter restored at the start of a word whose leading space sits inside <em>.
    src = _epub_with_page(_page("<p>word <em> mphasis</em></p>"))
    out = write_epub(src, {key: [Change(5, 5, "e")]})
    assert _only(out).text == "word emphasis"
    assert _only(out).marks == [Mark(6, 13, MarkKind.ITALIC)]


def test_insertion_after_placeholder_goes_into_its_tail():
    src = make_epub([("C", ["Text[^1] more"])])
    key = block_key(src, 0, 1)
    out = write_epub(src, {key: [Change(5, 5, ",")]})
    assert block(out, 0, 1).text == f"Text{PLACEHOLDER}, more"
    assert b'href="notes.xhtml#n1">1</a>, more' in _entry(out)


def test_insertion_before_leading_placeholder():
    src = _epub_with_page(_page('<p><i><img src="a.png" alt=""/></i>Text</p>'))
    out = write_epub(src, {f"{CH1}#0": [Change(0, 0, "X")]})
    assert _only(out).text == f"X{PLACEHOLDER}Text"


# --- entity references ----------------------------------------------------------------

_ENTITIES = _page("<p>a&nbsp;b&mdash;c</p>", prolog=f'<?xml version="1.0"?>\n{_DOCTYPE}\n')


def test_change_covering_nbsp_entity_replaces_it_with_text():
    src = _epub_with_page(_ENTITIES)
    key = f"{CH1}#0"
    assert _only(src).text == "a\xa0b—c"
    out = write_epub(src, {key: [Change(1, 2, " ")]})
    assert _only(out).text == "a b—c"
    assert b"<p>a b&mdash;c</p>" in _entry(out)
    # covering the entity and the text after it, and two entities at once
    assert _only(write_epub(src, {key: [Change(1, 3, "-B")]})).text == "a-B—c"
    assert _only(write_epub(src, {key: [Change(1, 4, "")]})).text == "ac"
    two = write_epub(src, {key: [Change(1, 2, "_"), Change(3, 4, "--")]})
    assert _only(two).text == "a_b--c"


def test_untouched_entity_stays_a_reference():
    src = _epub_with_page(_ENTITIES)
    key = f"{CH1}#0"
    out = write_epub(src, {key: [Change(0, 1, "A"), Change(2, 2, "x"), Change(4, 4, "y")]})
    assert _only(out).text == "A\xa0xb—yc"
    assert b"<p>A&nbsp;xb&mdash;yc</p>" in _entry(out)


# --- documents ------------------------------------------------------------------------

_LOOSE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<html><head><title>Loose</title></head><body>\n"
    "<p>Loose&nbsp;markup<br>with a break and <i>teh</i> text.\n"
    "<p>Grüße &mdash; unclosed.\n"
    "</body></html>\n"
)


def test_change_in_html_fallback_document():
    src = _epub_with_page(_LOOSE)
    before = _blocks(src)
    assert [b.text for b in before] == [
        f"Loose\xa0markup{PLACEHOLDER}with a break and teh text.",
        "Grüße — unclosed.",
    ]
    out = write_epub(src, {f"{CH1}#0": [Change(30, 33, "the")]})
    after = _blocks(out)
    assert after[0].text == f"Loose\xa0markup{PLACEHOLDER}with a break and the text."
    assert after[0].marks == [Mark(30, 33, MarkKind.ITALIC)]
    assert after[1] == before[1]
    data = _entry(out)
    assert data.startswith(b'<?xml version="1.0" encoding="utf-8"?>\n<html>')
    assert b"<!--?xml" not in data and b"<!DOCTYPE" not in data  # nothing invented
    assert "Grüße".encode() in data


def test_xml_declaration_and_doctype_kept_as_written():
    prolog = (
        "<?xml version='1.0' encoding='utf-8' standalone='no'?>\n\n"
        "<!-- it's the prolog > -->\n"
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"\n'
        '  "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd" [\n'
        '  <!-- a ] "quote -->\n'
        '  <!ENTITY jerome "J&#233;r&#244;me">\n'
        "]>\n"
    )
    src = _epub_with_page(_page("<p>teh &jerome; &nbsp;end</p>", prolog=prolog))
    assert _only(src).text == f"teh {PLACEHOLDER} \xa0end"  # an entity it cannot read
    out = write_epub(src, {f"{CH1}#0": [Change(0, 3, "the")]})
    data = _entry(out)
    assert data.startswith(prolog.encode() + _HTML.encode())
    assert b"&jerome;" in data and b"&nbsp;" in data
    assert data.endswith(b"</html>\n")


def test_declared_encoding_is_kept():
    page = _page("<p>Grüße, teh café</p>", prolog='<?xml version="1.0" encoding="ISO-8859-1"?>\n')
    src = _epub_with_page(page.encode("iso-8859-1"))
    out = write_epub(src, {f"{CH1}#0": [Change(7, 10, "“the”")]})
    assert _only(out).text == "Grüße, “the” café"
    data = _entry(out)
    assert data.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>')
    assert b"Gr\xfc\xdfe, &#8220;the&#8221; caf\xe9" in data


def test_utf16_document_round_trips():
    page = _page("<p>teh Grüße</p>", prolog='<?xml version="1.0" encoding="UTF-16"?>\n')
    src = _epub_with_page(b"\xfe\xff" + page.encode("utf-16-be"))
    out = write_epub(src, {f"{CH1}#0": [Change(0, 3, "the")]})
    assert _only(out).text == "the Grüße"
    assert _entry(out).startswith(b"\xfe\xff\x00<\x00?\x00x\x00m\x00l")


def test_only_elements_emptied_by_a_change_are_removed():
    src = make_epub([("C", ["Hello wor*ld* and **big** words"])], cluttered=True)
    key = block_key(src, 0, 1)
    out = write_epub(src, {key: [Change(6, 11, "WORLD"), Change(16, 20, "")]})
    assert block(out, 0, 1).text == "Hello WORLD and words"
    data = _entry(out)
    assert b"<i>" not in data and b"<b>" not in data
    empty = b'class="CharOverride-2"'  # empty from the start: not removed
    assert data.count(empty) == _entry(src).count(empty) > 0
    # an emptied element that may be a link target stays
    src = _epub_with_page(_page('<p><a id="here">Gone</a> stays</p>'))
    out = write_epub(src, {f"{CH1}#0": [Change(0, 5, "")]})
    assert _only(out).text == "stays"
    assert b'<a id="here"></a>stays' in _entry(out)


def test_blocks_without_effective_changes_leave_their_file_alone():
    src = make_epub([("One", ["abc"])])
    out = write_epub(src, {block_key(src, 0, 1): [Change(1, 1, "")], block_key(src, 0, 0): []})
    assert unchanged_entries(src, out) == set(zip_names(src))


def test_several_files_and_blocks():
    src = make_epub([("One", ["teh a", "b"]), ("Two", ["c teh"])])
    changes = {
        block_key(src, 0, 1): [Change(0, 3, "the")],
        block_key(src, 0, 2): [Change(1, 1, "!")],
        block_key(src, 1, 1): [Change(2, 5, "the")],
    }
    out = write_epub(src, changes)
    assert [b.text for b in _blocks(out)] == ["One", "the a", "b!", "Two", "c the"]
    assert zip_names(out) == zip_names(src)
    assert unchanged_entries(src, out) == set(zip_names(src)) - {CH1, "OEBPS/ch2.xhtml"}


# --- refusals -------------------------------------------------------------------------


def _refusal_epub() -> bytes:
    page = _page('<h1>T</h1><p>abc</p><p><img src="a.png" alt=""/> </p><p>a<br/>b</p>')
    return _epub_with_page(page)


@pytest.mark.parametrize(
    "key",
    [
        "OEBPS/nothere.xhtml#0",  # no such file
        "OEBPS/nav.xhtml#0",  # a file, but not in the spine
        f"{CH1}#9",  # no such block
        f"{CH1}#2",  # only a placeholder: not a block
        f"{CH1}#01",
        f"{CH1}#",
        CH1,
        "OEBPS/content.opf#0",
    ],
)
def test_unknown_key_raises(key):
    with pytest.raises(InvalidChangeError, match="No block"):
        write_epub(_refusal_epub(), {key: [Change(0, 0, "x")]})


@pytest.mark.parametrize(
    "changes",
    [
        [Change(0, 4, "x")],  # out of range
        [Change(0, 2, "x"), Change(1, 3, "y")],  # overlapping
        [Change(0, 1, "bad\x01char")],  # not XML text
    ],
)
def test_invalid_changes_raise(changes):
    with pytest.raises(InvalidChangeError):
        write_epub(_refusal_epub(), {f"{CH1}#1": changes})


def test_change_covering_placeholder_raises():
    with pytest.raises(InvalidChangeError, match="placeholder"):
        write_epub(_refusal_epub(), {f"{CH1}#3": [Change(0, 3, "x")]})


# --- round trip over random changes ---------------------------------------------------

_MESSY = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    f"{_DOCTYPE}\n{_HTML}\n<head><title>Messy</title></head>\n<body>\n"
    '  <h1 id="c2">Messy&nbsp;chapter</h1>\n'
    "  <p>\n    Some text <em> emphasised </em> more&nbsp;text&mdash;and"
    " <b>bold<i> nested </i></b> words.\n  </p>\n"
    '  <p>A note<a epub:type="noteref" href="#n1">1</a> and <img src="x.png" alt=""/>'
    " an image,<br/>a break.</p>\n"
    '  <p><span> </span>Lead <span class="x"> </span> split   run'
    ' <a href="https://x.org">link <i>text</i></a>&#160;end &amp; more.</p>\n'
    "  <p>&ldquo;Quoted&rdquo; &nbsp; spaced &hellip;<span>  </span></p>\n"
    "</body>\n</html>\n"
)


def _cluttered() -> bytes:
    src = make_epub(
        [
            (
                "One",
                [
                    "Hello **big** world and *italic* words.",
                    "A [link *here*](https://x.org) and a note[^1] end.",
                    "Plain line with **bold *nested* words** here.",
                    "*A fully italic line.*",
                ],
            ),
            ("Two", ["x"]),
            ("Three", ["y"]),
        ],
        cluttered=True,
    )
    src = edit_entry(src, "OEBPS/ch2.xhtml", lambda _: _MESSY)
    return edit_entry(src, "OEBPS/ch3.xhtml", lambda _: _LOOSE)


@pytest.mark.parametrize("name, rate, rounds", [("pg932", 0.3, 12), ("cluttered", 0.7, 80)])
def test_random_changes_round_trip(name, rate, rounds):
    src = _pg932() if name == "pg932" else _cluttered()
    rng = random.Random(7)
    blocks = _blocks(src)
    assert len(blocks) >= 12
    for _ in range(rounds):
        changes = {b.key: _random_changes(rng, b.text) for b in blocks if rng.random() < rate}
        out = write_epub(src, changes)
        written = _blocks(out)
        expected = [
            (b.key, b.kind, apply_changes(b.text, changes[b.key]) if b.key in changes else b.text)
            for b in blocks
        ]
        # a block whose text is all deleted is no longer a block
        assert [(b.key, b.kind, b.text) for b in written] == [
            e for e in expected if is_block_text(e[2])
        ]
        # every other block is unchanged, marks included
        assert [b for b in written if b.key not in changes] == [
            b for b in blocks if b.key not in changes
        ]
        touched = {key.partition("#")[0] for key in changes}
        assert unchanged_entries(src, out) >= set(zip_names(src)) - touched
        assert zip_names(out) == zip_names(src)


def _random_changes(rng: random.Random, text: str) -> list[Change]:
    """Valid changes whose result a reader shows as is: no doubled, leading or trailing space."""
    while True:
        changes = []
        for _ in range(rng.randint(1, 3)):
            start = rng.randint(0, len(text))
            end = min(len(text), start + rng.choice([0, 0, 1, 2, 4, 9]))
            replacement = "".join(rng.choice("xy zÄ“<&\xa0") for _ in range(rng.randint(0, 4)))
            changes.append(Change(start, end, replacement))
        try:
            changes = validate_changes(text, changes)
        except InvalidChangeError:
            continue
        result = apply_changes(text, changes)
        if "  " not in result and result == result.strip(" "):
            return changes
