import json
from dataclasses import replace

import pytest
from sqlalchemy import insert, select

from jerome.core.auto_apply import (
    DEFAULT_POLICY,
    OFF_POLICY,
    AutoApplyPolicy,
    apply_clear_cut,
    get_policy,
    is_auto_applied,
    policy_from_json,
    policy_to_json,
    set_policy,
)
from jerome.core.books import import_book
from jerome.core.style_sheets import get_style_sheet
from jerome.core.suggestions import add_candidates, decide
from jerome.orm import AUTO_APPLY_DEFAULT, Block, Book, Decision, Suggestion
from jerome.proofread.consistency import consistency_candidates
from jerome.types import BlockText, Category, Severity, Source
from tests.fixtures.docx_builders import make_docx
from tests.fixtures.suggestions import cand, insert_raw_open

TEXT = 'teh cat sat on the mat ."'  # separate spans: teh, cat, sat, mat, " .", a straight quote


@pytest.fixture
def para(session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="p.docx", data=make_docx([TEXT]), variant="en-US"
    )
    return session.query(Block).filter_by(book_id=book.id).one()


def model(block, start, end, original, replacement, category, severity, **kw):
    return cand(
        block,
        start,
        end,
        original,
        replacement,
        source=Source.MODEL,
        category=category,
        severity=severity,
        **kw,
    )


def add(session, block, *cands):
    return add_candidates(session, block.book_id, None, list(cands)).rows


def statuses(session, rows):
    return [session.get(Suggestion, r.id).status for r in rows]


def test_default_policy_applies_ai_high_typos_and_punctuation_only(session, para):
    rows = add(
        session,
        para,
        model(para, 0, 3, "teh", "the", Category.TYPO, Severity.HIGH),
        model(para, 4, 7, "cat", "cats", Category.GRAMMAR, Severity.HIGH),
        model(para, 8, 11, "sat", "sits", Category.TYPO, Severity.MEDIUM),
        model(para, 19, 22, "mat", "Mat", Category.STYLE, Severity.HIGH),
        model(para, 22, 24, " .", ".", Category.PUNCTUATION, Severity.HIGH),
    )
    assert apply_clear_cut(session, rows, DEFAULT_POLICY) == 2
    assert statuses(session, rows) == ["accepted", "open", "open", "open", "accepted"]


def test_languagetool_alone_never_applies(session, tmp_storage):
    book = import_book(
        session, tmp_storage, filename="f.docx", data=make_docx(["Mon cœur bat."]), variant="en-US"
    )
    block = session.query(Block).filter_by(book_id=book.id).one()
    rows = add(
        session,
        block,
        cand(block, 4, 8, "cœur", "cur", category=Category.TYPO, severity=Severity.HIGH),
    )
    assert rows[0].source == "languagetool"
    assert apply_clear_cut(session, rows, DEFAULT_POLICY) == 0
    assert statuses(session, rows) == ["open"]


def test_disputed_never_applies(session, para):
    rows = add(
        session,
        para,
        model(para, 0, 3, "teh", "the", Category.TYPO, Severity.HIGH, disputed=True),
        model(
            para,
            4,
            7,
            "cat",
            "colour",
            Category.CONSISTENCY,
            Severity.MEDIUM,
            disputed=True,
            rule="house_spelling",
        ),
    )
    assert apply_clear_cut(session, rows, DEFAULT_POLICY) == 0
    assert statuses(session, rows) == ["open", "open"]


def _consistency_rows(session, para):
    def c(start, end, original, replacement, rule, category=Category.CONSISTENCY):
        return cand(
            para,
            start,
            end,
            original,
            replacement,
            source=Source.CONSISTENCY,
            category=category,
            severity=Severity.MEDIUM,
            rule=rule,
        )

    return add(
        session,
        para,
        c(0, 3, "teh", "tea", "house_spelling"),
        c(24, 25, '"', "”", "quote_style", Category.PUNCTUATION),
        c(4, 7, "cat", "Cat", "variant"),
        c(8, 11, "sat", "Sat", "name"),
    )


def test_house_rules_apply_but_variants_and_names_do_not(session, para):
    rows = _consistency_rows(session, para)
    assert apply_clear_cut(session, rows, DEFAULT_POLICY) == 2
    assert statuses(session, rows) == ["accepted", "accepted", "open", "open"]


def _quote_rows(session, tmp_storage, text, variant):
    """The consistency pass's own quote-style findings for one paragraph of a new book."""
    book = import_book(
        session, tmp_storage, filename="q.docx", data=make_docx([text]), variant=variant
    )
    block = session.query(Block).filter_by(book_id=book.id).one()
    found = consistency_candidates(
        [BlockText(block.id, block.text)], get_style_sheet(session, book.id)
    )
    return add(session, block, *[c for c in found if c.rule == "quote_style"])


@pytest.mark.parametrize(
    ("variant", "text", "curly", "swap"),
    [
        # in de-DE “ is a closing quote, so ” is swapped for it and “Hallo” would become “Hallo“
        ("de-DE", 'Er sagte “Hallo”. Sie sagte "Tschüss".', "”", "“"),
        # and the other way round: „Hallo“ in an English book would become “Hallo“
        ("en-US", 'He said „Hallo“. She said "bye".', "„", "“"),
        ("en-GB", 'He said „Hallo“. She said "bye".', "„", "“"),
    ],
)
def test_quote_style_applies_straight_quotes_only(session, tmp_storage, variant, text, curly, swap):
    """A curly quote of another style may be one half of a pair the rule cannot see whole, so
    swapping it can leave mismatched quotes. Only straight quotes are applied; a curly-to-curly
    change waits for a person."""
    rows = _quote_rows(session, tmp_storage, text, variant)
    assert sorted((r.original, r.replacement) for r in rows if r.original == curly) == [
        (curly, swap)
    ]
    assert [r.original for r in rows if r.original != curly] == ['"', '"']

    assert apply_clear_cut(session, rows, DEFAULT_POLICY) == 2
    assert sorted((r.original, session.get(Suggestion, r.id).status) for r in rows) == sorted(
        [(curly, "open"), ('"', "accepted"), ('"', "accepted")]
    )


def test_house_rules_toggle_off(session, para):
    rows = _consistency_rows(session, para)
    assert apply_clear_cut(session, rows, replace(DEFAULT_POLICY, house_rules=False)) == 0
    assert statuses(session, rows) == ["open"] * 4


def test_house_rule_twin_from_another_checker_applies(session, para):
    """The kept twin of a house spelling keeps its rule whatever its source."""
    lt_twin = cand(
        para,
        0,
        3,
        "teh",
        "tea",
        source=Source.LANGUAGETOOL,
        category=Category.TYPO,
        severity=Severity.MEDIUM,
        rule="house_spelling",
    )
    twin = {"rule": "house_spelling", "agreed": True}
    ai_twin = model(para, 4, 7, "cat", "kat", Category.CONSISTENCY, Severity.MEDIUM, **twin)
    ai_clear_cut = model(para, 8, 11, "sat", "sit", Category.TYPO, Severity.HIGH, **twin)

    rows = add(session, para, lt_twin, ai_twin, ai_clear_cut)
    off = replace(DEFAULT_POLICY, house_rules=False)
    # house rules off: only the AI twin that qualifies on its own merits applies
    assert apply_clear_cut(session, rows, off) == 1
    assert statuses(session, rows) == ["open", "open", "accepted"]
    # house rules on: the house-spelling twins apply too
    assert apply_clear_cut(session, rows, DEFAULT_POLICY) == 2
    assert statuses(session, rows) == ["accepted", "accepted", "accepted"]


def test_disabled_policy_applies_nothing(session, para):
    rows = add(
        session,
        para,
        model(para, 0, 3, "teh", "the", Category.TYPO, Severity.HIGH),
        cand(
            para,
            4,
            7,
            "cat",
            "kat",
            source=Source.CONSISTENCY,
            category=Category.CONSISTENCY,
            rule="house_spelling",
        ),
    )
    assert apply_clear_cut(session, rows, OFF_POLICY) == 0
    assert statuses(session, rows) == ["open", "open"]


def test_policy_json_round_trip(session, book_factory):
    assert DEFAULT_POLICY == AutoApplyPolicy(
        enabled=True,
        categories=frozenset({Category.TYPO, Category.PUNCTUATION}),
        min_severity=Severity.HIGH,
        house_rules=True,
    )
    assert OFF_POLICY == replace(DEFAULT_POLICY, enabled=False)
    # the column's server default (books from before auto-apply) is exactly the OFF policy
    assert policy_to_json(OFF_POLICY) == json.loads(AUTO_APPLY_DEFAULT)
    assert policy_to_json(DEFAULT_POLICY) == {
        "enabled": True,
        "categories": ["punctuation", "typo"],
        "min_severity": "high",
        "house_rules": True,
    }
    custom = AutoApplyPolicy(
        enabled=True,
        categories=frozenset({Category.STYLE, Category.GRAMMAR, Category.TYPO}),
        min_severity=Severity.LOW,
        house_rules=False,
    )
    assert policy_to_json(custom)["categories"] == ["grammar", "style", "typo"]
    empty = replace(custom, categories=frozenset())
    for p in (DEFAULT_POLICY, OFF_POLICY, custom, empty):
        assert policy_from_json(policy_to_json(p)) == p
        assert policy_from_json(json.loads(json.dumps(policy_to_json(p)))) == p

    book = book_factory()
    assert get_policy(session, book.id) == OFF_POLICY  # server default
    assert set_policy(session, book.id, custom) == custom
    session.expire_all()
    assert get_policy(session, book.id) == custom
    assert session.get(Book, book.id).auto_apply == policy_to_json(custom)

    raw = session.scalar(
        insert(Book).values(title="old", format="docx").returning(Book.id)
    )  # a row written without auto_apply, like one from before migration 0003
    assert get_policy(session, raw) == OFF_POLICY


def test_auto_decision_is_logged_as_ai_on_auto_channel(session, para):
    rows = add(session, para, model(para, 0, 3, "teh", "the", Category.TYPO, Severity.HIGH))
    assert apply_clear_cut(session, rows, DEFAULT_POLICY) == 1
    d = session.scalars(select(Decision).where(Decision.suggestion_id == rows[0].id)).one()
    assert (d.action, d.previous_status, d.new_status, d.actor, d.channel) == (
        "accept",
        "open",
        "accepted",
        "ai",
        "auto",
    )


def test_is_auto_applied_follows_the_latest_decision(session, para):
    auto, person = add(
        session,
        para,
        model(para, 0, 3, "teh", "the", Category.TYPO, Severity.HIGH),
        model(para, 4, 7, "cat", "cats", Category.GRAMMAR, Severity.HIGH),
    )
    apply_clear_cut(session, [auto, person], DEFAULT_POLICY)
    decide(session, person.id, "accept", actor="ed@example.com", channel="web")
    assert is_auto_applied(session, [auto.id, person.id]) == {auto.id}
    assert is_auto_applied(session, []) == set()

    decide(session, auto.id, "undo", actor="ed@example.com", channel="web")
    assert is_auto_applied(session, [auto.id]) == set()  # open again
    decide(session, auto.id, "accept", actor="ed@example.com", channel="web")
    assert is_auto_applied(session, [auto.id]) == set()  # now a person's accept


def test_apply_clear_cut_skips_conflicts_and_keeps_the_transaction_usable(session, para):
    """Two qualifying rows that overlap: the first applies and supersedes the second, which
    decide then refuses. The row after it still applies and the session keeps working."""
    kw = {"source": "model", "category": "typo", "severity": "high"}
    first = insert_raw_open(session, para, 0, 3, "teh", "the", **kw)
    clash = insert_raw_open(session, para, 0, 7, "teh cat", "the cat", **kw)
    later = insert_raw_open(session, para, 8, 11, "sat", "sit", **kw)

    assert apply_clear_cut(session, [first, clash, later], DEFAULT_POLICY) == 2
    assert statuses(session, [first, clash, later]) == ["accepted", "superseded", "accepted"]
    assert session.get(Suggestion, clash.id).superseded_by == first.id
    channels = session.scalars(
        select(Decision.channel).where(Decision.suggestion_id == clash.id)
    ).all()
    assert channels == ["system"]  # superseded, never accepted

    session.flush()  # the transaction is still usable
    assert session.scalar(select(Suggestion.status).where(Suggestion.id == later.id)) == (
        "accepted"
    )
