"""Classification fed by the real writer, Word simulator and reader (no hand-built revisions)."""

import random
import time
from datetime import UTC, datetime

from jerome.core.review_imports import SuggestionView, classify_block
from jerome.documents.changes import Change
from jerome.documents.docx_review import read_reviewed_docx
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from tests.core.test_classify import _held
from tests.fixtures.docx_builders import all_blocks, make_docx
from tests.fixtures.word_sim import simulate_word_review

NOW = datetime(2026, 11, 22, tzinfo=UTC)


def through_word(text, specs, *, accept=(), reject=(), untracked=None):
    """specs: (start, end, replacement) -> (SuggestionViews, ReviewedBlock)."""
    original = make_docx([text])
    key = all_blocks(original)[0].key
    changes = [
        Change(a, b, r, ref=str(i + 1), comment="[typo · low] x")
        for i, (a, b, r) in enumerate(specs)
    ]
    exported = write_docx(
        original, {key: changes}, DocxWriteOptions("tracked", NOW, {key: "_jb1"}, {})
    )
    reviewed = simulate_word_review(
        exported, accept=accept, reject=reject, untracked_edits=untracked or {}
    )
    block = read_reviewed_docx(reviewed).blocks[0]
    views = [SuggestionView(i + 1, a, b, text[a:b], r) for i, (a, b, r) in enumerate(specs)]
    return views, block


def test_touching_pair_left_pending_through_real_reader():
    text = "teh cat sat."
    views, rb = through_word(text, [(0, 3, "the"), (3, 4, ", ")])
    out = classify_block(text, rb, views)
    assert out.results == {1: ("unchanged", None), 2: ("unchanged", None)} and not out.notes
    views, rb = through_word(text, [(0, 3, "the"), (3, 4, ", ")], accept=["the", ", "])
    out = classify_block(text, rb, views)
    assert out.results == {1: ("accepted", None), 2: ("accepted", None)}


def test_zero_width_after_replacement_through_real_reader():
    text = "quick adn we go."
    views, rb = through_word(text, [(6, 9, "and"), (9, 9, ",")])
    assert classify_block(text, rb, views).results == {
        1: ("unchanged", None),
        2: ("unchanged", None),
    }


def test_dog_dog_dog_with_zero_width_through_real_reader():
    text = "dog dog dog"
    views, rb = through_word(
        text, [(0, 3, "Dog"), (3, 3, ","), (4, 7, "DOG"), (8, 11, "dOg")], accept=["DOG"]
    )
    out = classify_block(text, rb, views)
    assert out.results == {
        1: ("unchanged", None),
        2: ("unchanged", None),
        3: ("accepted", None),
        4: ("unchanged", None),
    }


FIX = {
    "teh": "the",
    "adn": "and",
    "recieve": "receive",
    "wierd": "weird",
    "alot": "a lot",
    "dont": "don't",
    "form": "from",
    "sat": "sits",
    "thier": "their",
}
FILLER = "quick brown fox jumps over lazy river banks again we on near".split()
GLUE = [("", ","), (" ", ", "), (" ", " - ")]


def _scenario(rng, edit):
    fixes = rng.sample(list(FIX), rng.randint(1, 5))
    fillers = rng.sample(FILLER, rng.randint(2, 4))
    words = fixes + fillers
    rng.shuffle(words)
    text = " ".join(words)
    specs, starts, pos = [], {}, 0
    for w in words:
        starts[w] = pos
        pos += len(w) + 1
    glue = list(GLUE)
    rng.shuffle(glue)
    for w in words:
        if w in FIX and w in fixes:
            end = starts[w] + len(w)
            specs.append((starts[w], end, FIX[w]))
            if glue and end < len(text) and rng.random() < 0.6:
                o, r = glue.pop()
                specs.append((end, end + len(o), r))
    specs.sort()
    decisions = [rng.choice("arp") for _ in specs]
    covered = set()
    for a, b, _ in specs:
        covered |= set(range(a - 1, b + 1))
    free = [w for w in fillers if not covered & set(range(starts[w], starts[w] + len(w)))]
    typed = {rng.choice(free): None} if edit and free else {}
    return text, specs, decisions, {w: w + "x" for w in typed}


def _run_scenarios(seed, count, edit):
    rng = random.Random(seed)
    exact = fallbacks = 0
    for _ in range(count):
        text, specs, decisions, untracked = _scenario(rng, edit)
        accept = [r for (a, b, r), d in zip(specs, decisions, strict=True) if d == "a"]
        reject = [r for (a, b, r), d in zip(specs, decisions, strict=True) if d == "r"]
        views, rb = through_word(text, specs, accept=accept, reject=reject, untracked=untracked)
        out = classify_block(text, rb, views)
        truth = {
            i + 1: ({"a": "accepted", "r": "rejected", "p": "unchanged"}[d], None)
            for i, d in enumerate(decisions)
        }
        if out.notes:
            assert edit, (text, specs, decisions, out.notes)
            assert out.editor_changes == []
            assert {r for r, _ in out.results.values()} == {"unchanged"}
            fallbacks += 1
            continue
        held = _held(text, out, views)
        assert held == rb.text, (text, specs, decisions, untracked, out)
        if not edit:
            assert out.results == truth, (text, specs, decisions, out)
        exact += 1
    return exact, fallbacks


def test_pipeline_fuzz_accept_reject_pending_exact():
    exact, fallbacks = _run_scenarios(1121, 1000, edit=False)
    assert exact == 1000 and fallbacks == 0


def test_pipeline_fuzz_with_untracked_edit_is_exact_or_falls_back():
    exact, fallbacks = _run_scenarios(2211, 400, edit=True)
    assert exact >= 300


def test_large_paragraph_with_eight_suggestions_classifies_quickly():
    from jerome.documents.docx_review import ReviewedBlock

    words = [f"w{i}" for i in range(700)]
    for i in range(10, 690, 85):
        words[i] = "teh"
    original = " ".join(words)
    specs, pos = [], 0
    for w in words:
        if w == "teh":
            specs.append((pos, pos + 3, f"the{len(specs)}"))
        pos += len(w) + 1
    assert len(specs) == 8 and len(original) > 3000
    views = [SuggestionView(i + 1, a, b, "teh", r) for i, (a, b, r) in enumerate(specs)]
    parts, pos = [], 0
    for a, b, r in specs:
        parts += [original[pos:a], r]
        pos = b
    parts.append(original[pos:])
    reviewed = "".join(parts).replace("w300", "w3x00")  # one untracked edit
    start = time.perf_counter()
    out = classify_block(original, ReviewedBlock(None, reviewed, []), views)
    assert time.perf_counter() - start < 1.0
    assert not out.notes and {r for r, _ in out.results.values()} == {"accepted"}
    assert len(out.editor_changes) == 1
