from jerome.core import books
from jerome.core.agent_actions import list_actions, record_action


def test_record_and_list_newest_first(session):
    a = record_action(session, actor="janus", tool="t1", summary="first", after={"x": 1})
    b = record_action(session, actor="janus", tool="t2", summary="second", before={"y": 2})
    assert a.id and a.channel == "mcp" and a.book_id is None
    rows, total = list_actions(session, book_id=None, limit=10, offset=0)
    assert [r.id for r in rows] == [b.id, a.id]
    assert total == 2
    assert rows[1].after == {"x": 1} and rows[0].before == {"y": 2}
    rows, total = list_actions(session, book_id=None, limit=1, offset=1)
    assert [r.id for r in rows] == [a.id] and total == 2


def test_list_filters_by_book(session, imported_docx_book):
    mine = record_action(
        session, actor="janus", tool="t", summary="in book", book_id=imported_docx_book.id
    )
    record_action(session, actor="janus", tool="t", summary="elsewhere")
    rows, total = list_actions(session, book_id=imported_docx_book.id, limit=10, offset=0)
    assert [r.id for r in rows] == [mine.id] and total == 1


def test_deleting_book_keeps_action_with_null_book(session, imported_docx_book, tmp_storage):
    action = record_action(
        session, actor="janus", tool="t", summary="kept", book_id=imported_docx_book.id
    )
    books.delete_book(session, tmp_storage, imported_docx_book.id)
    session.refresh(action)
    assert action.book_id is None and action.summary == "kept"
