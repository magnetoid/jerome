"""Classification fed by the real writer, Word simulator and reader (no hand-built revisions)."""

import random
import time
from datetime import UTC, datetime

import pytest

from jerome.core.review_imports import SuggestionView, _exact_assignments, classify_block
from jerome.documents.changes import Change
from jerome.documents.docx_review import read_reviewed_docx
from jerome.documents.docx_writer import DocxWriteOptions, write_docx
from tests.core.test_classify import _held
from tests.fixtures.docx_builders import all_blocks, make_docx
from tests.fixtures.word_sim import simulate_word_review

NOW = datetime(2026, 11, 22, tzinfo=UTC)


def through_word(text, specs, *, accept=(), reject=(), untracked=None, line=None):
    """specs: (start, end, replacement) -> (SuggestionViews, ReviewedBlock). ``line`` is
    ``text`` with inline markup (bold, links), which splits Jerome's deletions into runs."""
    original = make_docx([line or text])
    assert all_blocks(original)[0].text == text
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
    return text, specs, decisions, {w: w + "x" for w in typed}, text


CONS, VOWELS = "bcdfghjklmnpqrstvwxz", "aeiou"
# No glue is a mark plus " ": "," then " " shows exactly like " " -> ", " (two answers).
BIG_GLUE = [" - ", "; ", ": ", " -- ", " / ", " + "]
BIG_MARKS = [",", "!", "?", ".", "'", "%"]


def _made_up(rng, used):
    while True:
        w = "".join(rng.choice(CONS if k % 2 == 0 else VOWELS) for k in range(rng.randint(3, 6)))
        if not any(w in u or u in w for u in used):
            used.add(w)
            return w


def _big_block(rng, count):
    """``count`` suggestions over made-up words -> (text, line, specs).

    Word fixes, two-word rewrites, pure deletions of " word", glue changes touching a fix
    and zero-width marks right after one. Some words are bold or linked (partly, too),
    which splits Jerome's deletions into several runs. Nothing touches the end of a pure
    deletion (the simulator would take both as one change).
    """
    used: set[str] = set()
    words: list[str] = []
    specs: list[tuple[int, int, str]] = []
    glue, marks = rng.sample(BIG_GLUE, len(BIG_GLUE)), rng.sample(BIG_MARKS, len(BIG_MARKS))

    def word():
        start = sum(len(w) + 1 for w in words)
        words.append(_made_up(rng, used))
        return start, start + len(words[-1])

    while len(specs) < count:
        a, b = word()
        kind = rng.choice(["fix", "fix", "two", "del", "plain"])
        if kind == "fix":
            specs.append((a, b, _made_up(rng, used) + "e"))
            if glue and rng.random() < 0.4:
                specs.append((b, b + 1, glue.pop()))
            elif marks and rng.random() < 0.4:
                specs.append((b, b, marks.pop()))
        elif kind == "two":
            b = word()[1]
            specs.append((a, b, f"{_made_up(rng, used)} {_made_up(rng, used)}"))
        elif kind == "del" and a > 0 and (not specs or specs[-1][1] < a - 1):
            specs.append((a - 1, b, ""))
            word()
    word()
    tokens, k = [], 0
    while k < len(words):
        r, w = rng.random(), words[k]
        if r < 0.15 and k + 1 < len(words):
            n = rng.randint(1, 3)
            tokens.append(f"[{' '.join(words[k : k + n])}](http://e.com/{k})")
            k += n
            continue
        if r < 0.25:
            tokens.append(f"**{' '.join(words[k : k + 2])}**")
            k += 2
            continue
        if r < 0.32 and len(w) >= 4:
            cut = rng.randint(1, len(w) - 1)
            w = f"{w[:cut]}**{w[cut:]}**"
        tokens.append(w)
        k += 1
    return " ".join(words), " ".join(tokens), specs[:count]


def _big_scenario(rng, edit):
    assert not edit
    text, line, specs = _big_block(rng, rng.randint(16, 28))
    pending = rng.choice([0.34, 0.6, 0.85])
    decisions = ["p" if rng.random() < pending else rng.choice("ar") for _ in specs]
    return text, specs, decisions, {}, line


def _check_exact_search(text, rb, views, decisions):
    """The exact search finds the editor's decisions, alone, within 50 nodes a suggestion."""
    stats = {}
    found = _exact_assignments(text, rb.text, views, rb.pending, stats)
    want = (
        {i + 1 for i, d in enumerate(decisions) if d == "a"},
        {i + 1 for i, d in enumerate(decisions) if d == "p"},
    )
    assert found == [want], (text, decisions, found)
    assert stats["nodes"] <= 50 * len(views), (text, decisions, stats)


def _run_scenarios(seed, count, edit, scenario=_scenario):
    rng = random.Random(seed)
    exact = fallbacks = 0
    for _ in range(count):
        text, specs, decisions, untracked, line = scenario(rng, edit)
        keys = [r or text[a:b] for a, b, r in specs]  # a pure deletion is named by its text
        accept = [k for k, d in zip(keys, decisions, strict=True) if d == "a"]
        reject = [k for k, d in zip(keys, decisions, strict=True) if d == "r"]
        views, rb = through_word(
            text, specs, accept=accept, reject=reject, untracked=untracked, line=line
        )
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
            assert out.editor_changes == [], (text, specs, decisions, out)
            _check_exact_search(text, rb, views, decisions)
        exact += 1
    return exact, fallbacks


def test_pipeline_fuzz_accept_reject_pending_exact():
    exact, fallbacks = _run_scenarios(1121, 1000, edit=False)
    assert exact == 1000 and fallbacks == 0


def test_pipeline_fuzz_big_blocks_exact():
    exact, fallbacks = _run_scenarios(1621, 150, edit=False, scenario=_big_scenario)
    assert exact == 150 and fallbacks == 0


@pytest.mark.parametrize(("count", "accepted", "rejected"), [(20, 3, 3), (30, 4, 3)])
def test_many_pending_suggestions_in_one_block_are_exact(count, accepted, rejected):
    text, line, specs = _big_block(random.Random(count), count)
    assert len(specs) == count
    assert any(b == c for (_, b, _), (c, _, _) in zip(specs, specs[1:], strict=False))
    assert any(a == b for a, b, _ in specs)
    picked = random.Random(count + 1).sample(range(count), accepted + rejected)
    decisions = ["p"] * count
    for k in picked:
        decisions[k] = "a" if k in picked[:accepted] else "r"
    assert decisions.count("p") >= 14
    keys = [r or text[a:b] for a, b, r in specs]
    views, rb = through_word(
        text,
        specs,
        accept=[k for k, d in zip(keys, decisions, strict=True) if d == "a"],
        reject=[k for k, d in zip(keys, decisions, strict=True) if d == "r"],
        line=line,
    )
    out = classify_block(text, rb, views)
    assert out.results == {
        i + 1: ({"a": "accepted", "r": "rejected", "p": "unchanged"}[d], None)
        for i, d in enumerate(decisions)
    }
    assert out.notes == [] and out.editor_changes == []
    _check_exact_search(text, rb, views, decisions)


def test_accepted_shorter_replacement_touching_a_pending_deletion():
    # base view "ab cat sat.": the accepted "ab" is followed at once by the pending " "
    text = "abcd cat sat."
    views, rb = through_word(text, [(0, 4, "ab"), (4, 5, ", ")], accept=["ab"])
    out = classify_block(text, rb, views)
    assert out.results == {1: ("accepted", None), 2: ("unchanged", None)} and not out.notes
    text = "tehh cat sat."
    views, rb = through_word(text, [(0, 4, "the"), (4, 5, ", "), (5, 8, "dog")], accept=["the"])
    out = classify_block(text, rb, views)
    assert out.results == {1: ("accepted", None), 2: ("unchanged", None), 3: ("unchanged", None)}


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
