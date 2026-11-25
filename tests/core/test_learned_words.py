"""Learned words: what Remember for all books saves, rejects and refuses."""

import pytest
from sqlalchemy import select

from jerome.core.books import import_book
from jerome.core.learned_words import covers, delete_word, list_words, remember, rules_for
from jerome.errors import Conflict, NotFound
from jerome.orm import Block, LearnedWord, Suggestion
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import insert_raw_open


def book(sf, storage, paragraphs, variant="en-US"):
    with sf() as s:
        b = import_book(
            s, storage, filename="b.docx", data=make_docx(list(paragraphs)), variant=variant
        )
        s.commit()
        stmt = select(Block).where(Block.book_id == b.id).order_by(Block.ordinal)
        blocks = list(s.scalars(stmt))
        s.expunge_all()
        return b.id, blocks


def mark(sf, block, start, end, replacement="x", **kw):
    with sf() as s:
        blk = s.get(Block, block.id)
        r = insert_raw_open(s, blk, start, end, blk.text[start:end], replacement, **kw)
        s.commit()
        return r.id


def status(sf, sid):
    with sf() as s:
        return s.get(Suggestion, sid).status


NAME = {
    "ai_verdict": "not_an_error",
    "ai_reason": "A name in the story.",
    "lt_rule": "MORFOLOGIK_RULE_EN_US",
}
TEXT = ("Katharina said teh cat sat.", "Katharina ran.")


def test_remember_saves_the_word_and_rejects_its_open_marks(session_factory, tmp_storage):
    sf = session_factory
    book_id, (b1, b2) = book(sf, tmp_storage, TEXT)
    first = mark(sf, b1, 0, 9, "Katherine", **NAME)
    same = mark(sf, b2, 0, 9, "Katherine", lt_rule="MORFOLOGIK_RULE_EN_US")
    other_rule = mark(sf, b2, 0, 9, "Katherine", category="grammar", lt_rule="SENT_START")
    model = mark(sf, b2, 0, 9, "Katherine", source="model")
    with sf() as s:
        word, rejected = remember(s, first, actor="ed@example.com", channel="web")
        s.commit()
        got = (word.language, word.word, word.rule, word.reason, word.created_by, word.book_id)
        assert got == ("en", "Katharina", "*", "A name in the story.", "ed@example.com", book_id)
    assert sorted(rejected) == sorted([first, same])
    assert [status(sf, i) for i in (first, same, other_rule, model)] == [
        "rejected", "rejected", "open", "open",
    ]  # fmt: skip


def test_a_non_spelling_mark_keeps_its_rule(session_factory, tmp_storage):
    sf = session_factory
    _, (b1, b2) = book(sf, tmp_storage, ("Sie gehen.", "Sie gehen."))
    first = mark(sf, b1, 0, 3, "sie", **{**NAME, "category": "grammar", "lt_rule": "SENT_START"})
    same_rule = mark(sf, b2, 0, 3, "sie", category="grammar", lt_rule="SENT_START")
    misspelling = mark(sf, b2, 0, 3, "sie", lt_rule="MORFOLOGIK_RULE_EN_US")
    with sf() as s:
        word, rejected = remember(s, first, actor="a", channel="web")
        s.commit()
        assert word.rule == "SENT_START"
    assert sorted(rejected) == sorted([first, same_rule])
    assert status(sf, misspelling) == "open"


def test_remember_leaves_other_books_and_decided_marks(session_factory, tmp_storage):
    sf = session_factory
    _, (b1, b2) = book(sf, tmp_storage, TEXT)
    _, (c1, _) = book(sf, tmp_storage, TEXT)
    first = mark(sf, b1, 0, 9, **NAME)
    accepted = mark(sf, b2, 0, 9, status="accepted")
    elsewhere = mark(sf, c1, 0, 9)
    with sf() as s:
        _, rejected = remember(s, first, actor="a", channel="web")
        s.commit()
    assert rejected == [first]
    assert (status(sf, accepted), status(sf, elsewhere)) == ("accepted", "open")


def test_remembering_twice_keeps_one_word(session_factory, tmp_storage):
    sf = session_factory
    _, (b1, b2) = book(sf, tmp_storage, TEXT)
    first = mark(sf, b1, 0, 9, **NAME)
    second = mark(sf, b2, 0, 9, **NAME)
    _, (c1, _) = book(sf, tmp_storage, TEXT)
    third = mark(sf, c1, 0, 9, **NAME)
    with sf() as s:
        remember(s, first, actor="a", channel="web")
        s.commit()
        assert status(sf, second) == "rejected"
        word, rejected = remember(s, third, actor="b", channel="web")
        s.commit()
        assert (word.created_by, rejected) == ("a", [third])
        assert len(list(s.scalars(select(LearnedWord)))) == 1


@pytest.mark.parametrize(
    "over",
    [
        {"ai_verdict": "maybe_error"},
        {"ai_verdict": None},
        {"status": "rejected"},
        {"source": "model"},
        {"category": "grammar", "lt_rule": None},
    ],
)
def test_remember_refuses_marks_it_cannot_learn(session_factory, tmp_storage, over):
    sf = session_factory
    _, (b1, _) = book(sf, tmp_storage, TEXT)
    sid = mark(sf, b1, 0, 9, **{**NAME, **over})
    with sf() as s:
        with pytest.raises(Conflict):
            remember(s, sid, actor="a", channel="web")
        s.rollback()
        assert list(s.scalars(select(LearnedWord))) == []
    assert status(sf, sid) == over.get("status", "open")


def test_an_old_misspelling_without_a_rule_can_be_learned(session_factory, tmp_storage):
    sf = session_factory
    _, (b1, _) = book(sf, tmp_storage, TEXT)
    sid = mark(sf, b1, 0, 9, **{**NAME, "lt_rule": None})
    with sf() as s:
        word, _ = remember(s, sid, actor="a", channel="web")
        assert word.rule == "*"


def test_remember_a_missing_mark_is_not_found(session_factory):
    with session_factory() as s, pytest.raises(NotFound):
        remember(s, 999999, actor="a", channel="web")


def test_rules_for_a_language_and_covers(session_factory, tmp_storage):
    sf = session_factory
    _, (b1, _) = book(sf, tmp_storage, TEXT)
    _, (d1,) = book(sf, tmp_storage, ("Sie gehen.",), variant="de-DE")
    mark_en = mark(sf, b1, 0, 9, **NAME)
    mark_de = mark(sf, d1, 0, 3, **{**NAME, "category": "grammar", "lt_rule": "SENT_START"})
    with sf() as s:
        remember(s, mark_en, actor="a", channel="web")
        remember(s, mark_de, actor="a", channel="web")
        s.commit()
        en, de = rules_for(s, "en"), rules_for(s, "de")
    assert en == frozenset({("Katharina", "*")})
    assert de == frozenset({("Sie", "SENT_START")})
    assert covers(en, "Katharina", "ANY_SPELLER", misspelling=True)
    assert not covers(en, "Katharina", "SENT_START", misspelling=False)
    assert not covers(en, "katharina", "ANY_SPELLER", misspelling=True)
    assert covers(de, "Sie", "SENT_START", misspelling=False)
    assert not covers(de, "Sie", "OTHER", misspelling=False)


def test_list_and_delete(session_factory, tmp_storage):
    sf = session_factory
    _, (b1, b2) = book(sf, tmp_storage, TEXT)
    first = mark(sf, b1, 0, 4, **NAME)
    second = mark(sf, b2, 0, 9, **NAME)
    with sf() as s:
        older, _ = remember(s, first, actor="a", channel="web")
        newer, _ = remember(s, second, actor="a", channel="web")
        s.commit()
        assert [w.word for w in list_words(s)] == ["Katharina", "Kath"]
        delete_word(s, older.id)
        s.commit()
        assert [w.id for w in list_words(s)] == [newer.id]
        with pytest.raises(NotFound):
            delete_word(s, older.id)
    assert status(sf, first) == "rejected"
