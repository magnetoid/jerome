import zipfile
from datetime import UTC, datetime
from io import BytesIO

import pytest

from jerome.documents import docx_xml
from jerome.documents.changes import Change
from jerome.documents.docx_review import PendingRevision, read_reviewed_docx
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from jerome.errors import UnsupportedDocumentError
from tests.fixtures.docx_builders import make_docx, package_docx
from tests.fixtures.word_sim import simulate_word_review

NOW = datetime(2026, 11, 21, tzinfo=UTC)
P0 = "word/document.xml#0"


def tracked_teh_cat() -> bytes:
    return write_docx(
        make_docx(["teh cat"]), {P0: [Change(0, 3, "the")]}, DocxWriteOptions("tracked", NOW)
    )


def test_plain_paragraph_has_no_pending():
    (b,) = read_reviewed_docx(make_docx(["plain"])).blocks
    assert (b.text, b.pending) == ("plain", [])


def test_pending_jerome_revision_positions():
    (b,) = read_reviewed_docx(tracked_teh_cat()).blocks
    assert b.text == "teh cat"
    assert b.pending == [
        PendingRevision(0, 3, "del", "Jerome", "teh"),
        PendingRevision(3, 3, "ins", "Jerome", "the"),
    ]


def test_accepted_revision_becomes_plain_text():
    reviewed = simulate_word_review(tracked_teh_cat(), accept=["the"])
    (b,) = read_reviewed_docx(reviewed).blocks
    assert (b.text, b.pending) == ("the cat", [])


def test_rejected_revision_restores_the_original_text():
    reviewed = simulate_word_review(tracked_teh_cat(), reject=["the"])
    (b,) = read_reviewed_docx(reviewed).blocks
    assert (b.text, b.pending) == ("teh cat", [])


def test_editor_tracked_revision_author():
    reviewed = simulate_word_review(tracked_teh_cat(), tracked_edits={"cat": "dog"})
    (b,) = read_reviewed_docx(reviewed).blocks
    assert b.text == "teh cat"
    assert {p.author for p in b.pending} == {"Jerome", "Editor"}
    assert PendingRevision(4, 7, "del", "Editor", "cat") in b.pending
    assert PendingRevision(7, 7, "ins", "Editor", "dog") in b.pending


def test_untracked_edit_changes_base_text():
    reviewed = simulate_word_review(tracked_teh_cat(), untracked_edits={" cat": " dog"})
    (b,) = read_reviewed_docx(reviewed).blocks
    assert b.text == "teh dog"
    assert [p.kind for p in b.pending] == ["del", "ins"]


def test_untracked_edit_of_missing_text_raises():
    with pytest.raises(ValueError, match="not found"):
        simulate_word_review(make_docx(["plain"]), untracked_edits={"absent": "x"})


def test_deleted_paragraph_is_gone():
    reviewed = simulate_word_review(make_docx(["one", "two"]), delete_paragraphs_containing=["two"])
    assert [b.text for b in read_reviewed_docx(reviewed).blocks] == ["one"]


def test_bookmark_survives_renumbering():
    opts = DocxWriteOptions("tracked", NOW, bookmarks={P0: "_jb7"})
    reviewed = simulate_word_review(write_docx(make_docx(["x"]), {}, opts), renumber_ids=True)
    assert read_reviewed_docx(reviewed).blocks[0].bookmark == "_jb7"


def test_renumbering_changes_ids_but_not_the_reading():
    before = tracked_teh_cat()
    after = simulate_word_review(before)
    assert after != before
    assert read_reviewed_docx(after) == read_reviewed_docx(before)


def test_custom_properties_read():
    opts = DocxWriteOptions("tracked", NOW, custom_properties={"jerome_review_export_id": "42"})
    out = write_docx(make_docx(["x"]), {}, opts)
    assert read_reviewed_docx(out).properties == {"jerome_review_export_id": "42"}


def test_missing_custom_properties_part_gives_none():
    assert read_reviewed_docx(make_docx(["x"])).properties == {}


def test_paragraph_without_bookmark_has_none():
    assert read_reviewed_docx(make_docx(["x"])).blocks[0].bookmark is None


def test_change_split_across_a_hyperlink_is_one_deletion():
    original = make_docx(["a [bc](https://example.com) d"])  # "a bc d"
    out = write_docx(original, {P0: [Change(1, 5, "X")]}, DocxWriteOptions("tracked", NOW))
    with zipfile.ZipFile(BytesIO(out)) as zf:
        assert zf.read("word/document.xml").count(b"<w:del ") > 1
    (b,) = read_reviewed_docx(out).blocks
    assert b.text == "a bc d"
    assert b.pending == [
        PendingRevision(1, 5, "del", "Jerome", " bc "),
        PendingRevision(5, 5, "ins", "Jerome", "X"),
    ]
    accepted = simulate_word_review(out, accept=["X"])
    assert read_reviewed_docx(accepted).blocks[0].text == "aXd"


def test_field_inside_a_pending_deletion_is_one_placeholder():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">x </w:t></w:r>'
        '<w:del w:id="1" w:author="Jerome" w:date="2026-11-21T00:00:00Z">'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        "<w:r><w:delInstrText> PAGE </w:delInstrText></w:r>"
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:delText>7</w:delText></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:del>'
        '<w:r><w:t xml:space="preserve"> y</w:t></w:r></w:p>'
    )
    (b,) = read_reviewed_docx(package_docx(body)).blocks
    assert b.text == "x ￼ y"
    assert b.pending == [PendingRevision(2, 3, "del", "Jerome", "￼")]


def test_emptied_bookmarked_paragraph_still_appears():
    opts = DocxWriteOptions("clean", NOW, bookmarks={P0: "_jb1"})
    original = write_docx(make_docx(["x", "y"]), {}, opts)
    reviewed = simulate_word_review(original, untracked_edits={"x": ""})
    blocks = read_reviewed_docx(reviewed).blocks
    assert [(b.bookmark, b.text) for b in blocks] == [("_jb1", ""), (None, "y")]


def test_unbookmarked_empty_paragraph_is_not_a_block():
    reviewed = simulate_word_review(make_docx(["x", "y"]), untracked_edits={"x": ""})
    assert [b.text for b in read_reviewed_docx(reviewed).blocks] == ["y"]


def test_zip_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(docx_xml, "MAX_UNCOMPRESSED_BYTES", 100)
    with pytest.raises(UnsupportedDocumentError, match="too large"):
        read_reviewed_docx(make_docx(["x"]))


def test_separate_changes_in_a_paragraph_are_selected_independently():
    original = make_docx(["a x teh b"])  # delete "a ", replace "teh" -> "the"
    changes = {P0: [Change(0, 2, ""), Change(4, 7, "the")]}
    out = write_docx(original, changes, DocxWriteOptions("tracked", NOW))
    reviewed = simulate_word_review(out, accept=["the"])
    (b,) = read_reviewed_docx(reviewed).blocks
    assert b.text == "a x the b"
    assert b.pending == [PendingRevision(0, 2, "del", "Jerome", "a ")]
    rejected = simulate_word_review(out, reject=["a "])  # the deletion, by its deleted text
    (b,) = read_reviewed_docx(rejected).blocks
    assert b.pending == [
        PendingRevision(4, 7, "del", "Jerome", "teh"),
        PendingRevision(7, 7, "ins", "Jerome", "the"),
    ]


def test_changes_in_a_paragraph_with_a_text_box_are_resolved_once():
    box = "<w:txbxContent><w:p><w:r><w:t>Box</w:t></w:r></w:p></w:txbxContent>"
    body = (
        '<w:p><w:del w:id="1" w:author="Jerome" w:date="2026-11-21T00:00:00Z">'
        "<w:r><w:delText>teh</w:delText></w:r></w:del>"
        '<w:ins w:id="2" w:author="Jerome" w:date="2026-11-21T00:00:00Z">'
        "<w:r><w:t>the</w:t></w:r></w:ins>"
        f'<w:r><w:pict><v:shape id="b"><v:textbox>{box}</v:textbox></v:shape></w:pict></w:r>'
        '<w:ins w:id="3" w:author="Jerome" w:date="2026-11-21T00:00:00Z">'
        "<w:r><w:t>!</w:t></w:r></w:ins></w:p>"
    )
    inner = (
        '<w:ins w:id="4" w:author="Jerome" w:date="2026-11-21T00:00:00Z">'
        "<w:r><w:t>in</w:t></w:r></w:ins>"
    )
    body = body.replace("<w:r><w:t>Box</w:t></w:r>", f"<w:r><w:t>Box</w:t></w:r>{inner}")
    reviewed = simulate_word_review(package_docx(body), accept=["the", "in", "!"])
    (b,) = read_reviewed_docx(reviewed).blocks
    assert (b.text, b.pending) == ("the￼!", [])
