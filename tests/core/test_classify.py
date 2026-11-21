from jerome.core.review_imports import EditorChange, SuggestionView, classify_block
from jerome.documents.docx_review import PendingRevision, ReviewedBlock

S = SuggestionView(1, 0, 3, "teh", "the")


def RB(text, pending=()):
    return ReviewedBlock(None, text, list(pending))


def test_accepted():
    assert classify_block("teh cat", RB("the cat"), [S]).results[1] == ("accepted", None)


def test_rejected():
    assert classify_block("teh cat", RB("teh cat"), [S]).results[1] == ("rejected", None)


def test_edited():
    assert classify_block("teh cat", RB("The cat"), [S]).results[1] == ("edited", "The")


def test_pending_means_unchanged():
    rb = RB(
        "teh cat",
        [
            PendingRevision(0, 3, "del", "Jerome", "teh"),
            PendingRevision(3, 3, "ins", "Jerome", "the"),
        ],
    )
    out = classify_block("teh cat", rb, [S])
    assert out.results[1] == ("unchanged", None) and out.editor_changes == []


def test_untracked_editor_change():
    out = classify_block("teh cat sat", RB("the cat sits"), [S])
    assert out.editor_changes == [EditorChange(8, 11, "sat", "sits", pending=False)]


def test_editor_pending_revision():
    rb = RB(
        "teh cat",
        [
            PendingRevision(4, 7, "del", "Editor", "cat"),
            PendingRevision(7, 7, "ins", "Editor", "dog"),
        ],
    )
    out = classify_block("teh cat", rb, [S])
    assert out.editor_changes == [EditorChange(4, 7, "cat", "dog", pending=True)]
    assert out.results[1] == ("rejected", None)


def test_lone_pending_editor_insertion_and_deletion():
    rb = RB(
        "teh cat sat",
        [
            PendingRevision(7, 7, "ins", "Editor", "s"),
            PendingRevision(8, 11, "del", "Editor", "sat"),
        ],
    )
    out = classify_block("teh cat sat", rb, [])
    assert out.editor_changes == [
        EditorChange(7, 7, "", "s", pending=True),
        EditorChange(8, 11, "sat", "", pending=True),
    ]


def test_two_suggestions_independent():
    s2 = SuggestionView(2, 8, 11, "sat", "sits")
    out = classify_block("teh cat sat", RB("the cat sat"), [S, s2])
    assert out.results == {1: ("accepted", None), 2: ("rejected", None)}
    assert out.editor_changes == []


def test_insertion_at_span_end_reads_as_accepted():
    s = SuggestionView(1, 0, 3, "cat", "cats")
    out = classify_block("cat sat", RB("cats sat"), [s])
    assert out.results[1] == ("accepted", None) and out.editor_changes == []


def test_pure_deletion_suggestion():
    s = SuggestionView(1, 0, 5, "very ", "")
    assert classify_block("very good", RB("good"), [s]).results[1] == ("accepted", None)
    assert classify_block("very good", RB("very good"), [s]).results[1] == ("rejected", None)


def test_expansion_stops_at_suggestion_edge():
    out = classify_block("teh cat", RB("the cats"), [S])
    assert out.editor_changes == [EditorChange(4, 7, "cat", "cats", pending=False)]


def test_non_word_insertion_does_not_expand():
    out = classify_block("a cat", RB("a cat dog"), [])
    assert out.editor_changes == [EditorChange(5, 5, "", " dog", pending=False)]


def test_offsets_are_code_points():
    s = SuggestionView(1, 2, 5, "teh", "the")
    out = classify_block("😀 teh cat sat", RB("😀 the cat sits"), [s])
    assert out.results[1] == ("accepted", None)
    assert out.editor_changes == [EditorChange(10, 13, "sat", "sits", pending=False)]
