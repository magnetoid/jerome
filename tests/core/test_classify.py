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


def _apply(original, out, views):
    by_id = {v.id: v for v in views}
    segs = []
    for sid, (result, final) in out.results.items():
        v = by_id[sid]
        text = {"accepted": v.replacement, "rejected": v.original, "edited": final}[result]
        segs.append((v.start, v.end, text))
    segs += [(c.start, c.end, c.replacement) for c in out.editor_changes if not c.pending]
    pos, parts = 0, []
    for a, b, t in sorted(segs, key=lambda x: (x[0], x[1])):
        assert a >= pos
        parts += [original[pos:a], t]
        pos = b
    return "".join(parts) + original[pos:]


def test_word_expansion_uses_original_coordinates():
    for old, new in [("wierd", "weird"), ("recieved", "received"), ("beleive", "believe")]:
        out = classify_block(f"teh {old} cat.", RB(f"teh {new} cat."), [])
        assert out.editor_changes == [EditorChange(4, 4 + len(old), old, new, pending=False)]
        assert out.notes == []


def test_right_crossing_splits_remainder_into_editor_change():
    out = classify_block("teh cat sat.", RB("the-cat sat."), [S])
    assert out.results[1] == ("accepted", None)
    assert out.editor_changes == [EditorChange(3, 4, " ", "-", pending=False)]


def test_left_crossing_splits_remainder_into_editor_change():
    s = SuggestionView(1, 3, 5, "cd", "CD")
    out = classify_block("ab cd", RB("ab-CD"), [s])
    assert out.results[1] == ("accepted", None)
    assert out.editor_changes == [EditorChange(2, 3, " ", "-", pending=False)]


def test_both_sides_crossing_is_skipped_with_note():
    s = SuggestionView(1, 3, 5, "cd", "CD")
    out = classify_block("ab cd ef", RB("abQef"), [s])
    assert out.results[1] == ("unchanged", None) and out.editor_changes == []
    assert out.notes == ["suggestion 1: the editor's change runs across its edges; left as it was"]


def test_touching_suggestions_edge_insertion_claimed_once():
    a = SuggestionView(1, 0, 3, "teh", "the")
    b = SuggestionView(2, 3, 4, " ", ", ")
    out = classify_block("teh cat sat.", RB("teh, cat sat."), [a, b])
    assert out.results == {1: ("rejected", None), 2: ("accepted", None)}
    assert out.editor_changes == []


def test_pending_jerome_insertion_at_shared_point_does_not_mark_neighbour():
    a = SuggestionView(1, 0, 3, "teh", "the")
    b = SuggestionView(2, 3, 4, " ", ", ")
    rb = RB(
        "teh cat",
        [
            PendingRevision(0, 3, "del", "Jerome", "teh"),
            PendingRevision(3, 3, "ins", "Jerome", "the"),
        ],
    )
    out = classify_block("teh cat", rb, [a, b])
    assert out.results == {1: ("unchanged", None), 2: ("rejected", None)}


def test_unclaimed_edge_insertions_become_editor_changes():
    s = SuggestionView(1, 0, 6, "Howevr", "However")
    out = classify_block("Howevr we go", RB("However, we go"), [s])
    assert out.results[1] == ("accepted", None)
    assert out.editor_changes == [EditorChange(6, 6, "", ",", pending=False)]
    out = classify_block("teh cat", RB("Oh, the cat"), [S])
    assert out.results[1] == ("accepted", None)
    assert out.editor_changes == [EditorChange(0, 0, "", "Oh, ", pending=False)]


def test_random_single_word_edits_reconstruct():
    import random

    rng = random.Random(20261121)
    words = "the quick brown fox jumps over lazy dogs near river banks again".split()

    def typo(w):
        i = rng.randrange(len(w))
        return rng.choice(
            [
                w[:i] + w[i + 1 :],
                w[:i] + "x" + w[i:],
                w[:i] + "Z" + w[i + 1 :],
                w + "s",
                w + ",",
                "-" + w,
            ]
        )

    checked = 0
    for _ in range(300):
        original = " ".join(words)
        starts, pos = [], 0
        for w in words:
            starts.append(pos)
            pos += len(w) + 1
        picks = rng.sample(range(len(words)), rng.randint(0, 3))
        sugg_idx, edit_idx = picks[:2], (picks[2] if len(picks) == 3 else None)
        if edit_idx is None and rng.random() < 0.5 and sugg_idx:
            edit_idx = rng.choice(sugg_idx)  # the editor also touches a suggestion's word
        views, reviewed = [], list(words)
        for n, i in enumerate(sugg_idx):
            repl = typo(words[i])
            views.append(
                SuggestionView(n + 1, starts[i], starts[i] + len(words[i]), words[i], repl)
            )
            reviewed[i] = rng.choice([repl, words[i], repl + "!"])
        if edit_idx is not None and edit_idx not in sugg_idx:
            reviewed[edit_idx] = typo(words[edit_idx])
        elif edit_idx is not None:
            reviewed[edit_idx] = typo(reviewed[edit_idx])
        text = " ".join(reviewed)
        out = classify_block(original, RB(text), views)
        assert not any("could not align" in n for n in out.notes), (original, text, out)
        if not out.notes:
            assert _apply(original, out, views) == text, (original, text, out)
            checked += 1
    assert checked >= 200
