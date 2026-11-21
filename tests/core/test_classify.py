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
    assert out.notes == [
        "block: the editor's change runs across the edges of suggestion(s) 1; left as it was"
    ]


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
        assert not any("could not align" in n for n in out.notes), (original, text, views, reviewed)
        if not out.notes:
            assert _apply(original, out, views) == text, (original, text, out)
            checked += 1
    assert checked >= 200


def _held(original, out, views):
    """The text Jerome would hold: unchanged = original, plus the untracked editor changes."""
    by_id = {v.id: v for v in views}
    segs = [(c.start, c.end, c.replacement) for c in out.editor_changes if not c.pending]
    for sid, (result, final) in out.results.items():
        v = by_id[sid]
        segs.append(
            (
                v.start,
                v.end,
                {
                    "unchanged": v.original,
                    "accepted": v.replacement,
                    "rejected": v.original,
                    "edited": final,
                }[result],
            )
        )
    pos, parts = 0, []
    for a, b, t in sorted(segs, key=lambda x: (x[0], x[1])):
        assert a >= pos
        parts += [original[pos:a], t]
        pos = b
    return "".join(parts) + original[pos:]


def test_neighbouring_accepts_are_not_misread_as_edits():
    a, b = SuggestionView(1, 0, 3, "teh", "the"), SuggestionView(2, 4, 7, "hte", "the")
    out = classify_block("teh hte fox.", RB("the the fox."), [a, b])
    assert out.results == {1: ("accepted", None), 2: ("accepted", None)} and not out.notes
    a, b = SuggestionView(1, 0, 3, "teh", "the"), SuggestionView(2, 3, 4, " ", ", ")
    out = classify_block("teh cat sat.", RB("the, cat sat."), [a, b])
    assert out.results == {1: ("accepted", None), 2: ("accepted", None)}
    a, b = SuggestionView(1, 6, 9, "adn", "and"), SuggestionView(2, 9, 9, "", ",")
    out = classify_block("quick adn we go.", RB("quick and, we go."), [a, b])
    assert out.results == {1: ("accepted", None), 2: ("accepted", None)}
    assert out.editor_changes == []


def test_insertion_after_suggestion_edit_example():
    s = SuggestionView(1, 3, 10, "recieve", "receive")
    out = classify_block("we recieve it", RB("we receive it"), [s])
    assert out.results[1] == ("accepted", None)
    out = classify_block("we recieve it", RB("we receive, it"), [s])
    assert out.results[1] == ("accepted", None)
    assert out.editor_changes == [EditorChange(10, 10, "", ",", pending=False)]


def test_pending_and_accepted_mix_dog_dog_dog():
    vs = [
        SuggestionView(1, 0, 3, "dog", "Dog"),
        SuggestionView(2, 4, 7, "dog", "Dog"),
        SuggestionView(3, 8, 11, "dog", "Dog"),
    ]
    rb = RB(
        "dog Dog dog",
        [
            PendingRevision(0, 3, "del", "Jerome", "dog"),
            PendingRevision(3, 3, "ins", "Jerome", "Dog"),
            PendingRevision(8, 11, "del", "Jerome", "dog"),
            PendingRevision(11, 11, "ins", "Jerome", "Dog"),
        ],
    )
    out = classify_block("dog dog dog", rb, vs)
    assert out.results == {1: ("unchanged", None), 2: ("accepted", None), 3: ("unchanged", None)}


def test_pending_accepted_mix_with_untracked_edit_never_writes_wrong_text():
    original = "fox recieve teh near recieve recieve"
    vs = [
        SuggestionView(1, 4, 11, "recieve", "receive"),
        SuggestionView(2, 21, 28, "recieve", "receive"),
        SuggestionView(3, 28, 28, "", ","),
        SuggestionView(4, 29, 36, "recieve", "receive"),
    ]
    text = "fox recieve teh naer receive, recieve"
    pending = [
        PendingRevision(4, 11, "del", "Jerome", "recieve"),
        PendingRevision(11, 11, "ins", "Jerome", "receive"),
        PendingRevision(30, 37, "del", "Jerome", "recieve"),
        PendingRevision(37, 37, "ins", "Jerome", "receive"),
    ]
    out = classify_block(original, RB(text, pending), vs)
    if out.notes:
        assert {r for r, _ in out.results.values()} == {"unchanged"}
    else:
        assert _held(original, out, vs) == text


def test_skipped_suggestion_falls_back_for_the_whole_block():
    s = SuggestionView(1, 3, 5, "cd", "CD")
    other = SuggestionView(2, 9, 11, "xx", "yy")
    out = classify_block("ab cd ef xx", RB("abQef yy"), [s, other])
    assert out.results == {1: ("unchanged", None), 2: ("unchanged", None)}
    assert out.editor_changes == [] and len(out.notes) == 1 and "suggestion(s) 1" in out.notes[0]


def test_seeded_accept_reject_pending_fuzz():
    import random

    rng = random.Random(33)
    fix = {"teh": "the", "adn": "and", "hte": "the", "recieve": "receive", "cat": "cats"}
    other = "quick brown fox jumps over lazy near river we it on".split()
    glue = [("", ","), (" ", ", "), (" ", "-")]
    exact = edits = 0
    for _ in range(3000):
        words = [rng.choice(list(fix) + other) for _ in range(rng.randint(3, 7))]
        original = " ".join(words)
        starts, pos = [], 0
        for w in words:
            starts.append(pos)
            pos += len(w) + 1
        items = []  # (start, end, original, replacement, decision)
        for i, w in enumerate(words):
            if w in fix and rng.random() < 0.7:
                end = starts[i] + len(w)
                items.append((starts[i], end, w, fix[w], rng.choice(["a", "r", "p"])))
                if i < len(words) - 1 and rng.random() < 0.6:
                    o, r = rng.choice(glue)
                    items.append((end, end + len(o), o, r, rng.choice(["a", "r", "p"])))
        views = [SuggestionView(k + 1, a, b, o, r) for k, (a, b, o, r, _) in enumerate(items)]
        parts, pending, cur, pos = [], [], 0, 0
        for a, b, o, r, d in sorted(items, key=lambda x: (x[0], x[1])):
            gap = original[pos:a]
            parts.append(gap)
            cur += len(gap)
            if d == "p":
                if o:
                    pending.append(PendingRevision(cur, cur + len(o), "del", "Jerome", o))
                pending.append(PendingRevision(cur + len(o), cur + len(o), "ins", "Jerome", r))
            written = r if d == "a" else o
            parts.append(written)
            cur += len(written)
            pos = b
        parts.append(original[pos:])
        text = "".join(parts)
        covered = set()
        for a, b, *_ in items:
            covered |= set(range(a - 1, b + 1))
        free = [
            i
            for i, w in enumerate(words)
            if not covered & set(range(starts[i], starts[i] + len(w)))
        ]
        edited = bool(free) and rng.random() < 0.4
        if edited:
            i = rng.choice(free)
            # locate the word in the base view by counting what the suggestions before it changed
            shift = sum(
                len(r if d == "a" else o) - (b - a) for a, b, o, r, d in items if b <= starts[i]
            )
            at = starts[i] + shift
            assert text[at : at + len(words[i])] == words[i]
            text = text[:at] + words[i] + "x" + text[at + len(words[i]) :]
            for pr in pending:
                if pr.start >= at + len(words[i]):
                    pr.start += 1
                    pr.end += 1
        out = classify_block(original, RB(text, pending), views)
        if out.notes:
            assert {r for r, _ in out.results.values()} == {
                "unchanged"
            } and out.editor_changes == []
            continue
        assert _held(original, out, views) == text, (original, text, items)
        if not edited:
            truth = {
                k + 1: ({"a": "accepted", "r": "rejected", "p": "unchanged"}[d], None)
                for k, (*_, d) in enumerate(items)
            }
            assert out.results == truth, (original, text, items, out)
            exact += 1
        else:
            edits += 1
    assert exact >= 1500 and edits >= 300
