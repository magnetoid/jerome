import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from jerome.core.transfer_links import (
    LINK_TTL,
    consume_upload,
    create_download,
    create_upload,
    discard_upload,
    purge_expired,
    receive_upload,
    serve_download,
)
from jerome.errors import InvalidChangeError, NotFound
from jerome.orm import TransferLink

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
JUST_BEFORE = NOW + timedelta(minutes=14, seconds=59)
EXPIRED = NOW + timedelta(minutes=15)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _files(storage) -> list:
    folder = storage.root / "transfers"
    return sorted(folder.iterdir()) if folder.exists() else []


def _download(session, storage, now=NOW, actor="k"):
    return create_download(session, storage, actor, "book.docx", DOCX, b"exported", now)


def test_ttl_is_fifteen_minutes(session, tmp_storage):
    assert LINK_TTL == timedelta(minutes=15)
    up, _ = create_upload(session, tmp_storage, "k", NOW)
    down, _ = _download(session, tmp_storage)
    assert up.expires_at == down.expires_at == NOW + LINK_TTL
    assert (up.kind, up.actor, down.kind) == ("upload", "k", "download")


def test_upload_round_trip_and_expiry(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, JUST_BEFORE)
    assert (link.size, link.received_at) == (3, JUST_BEFORE)
    assert (tmp_storage.root / link.path).read_bytes() == b"abc"
    with pytest.raises(NotFound):
        consume_upload(session, tmp_storage, link.id, "k", EXPIRED)
    assert consume_upload(session, tmp_storage, link.id, "k", JUST_BEFORE) == b"abc"


def test_receive_after_expiry_is_not_found(session, tmp_storage):
    _, token = create_upload(session, tmp_storage, "k", NOW)
    with pytest.raises(NotFound):
        receive_upload(session, tmp_storage, token, b"abc", 10, EXPIRED)
    assert _files(tmp_storage) == []


def test_upload_is_single_use(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, NOW)
    with pytest.raises(NotFound):
        receive_upload(session, tmp_storage, token, b"other", 10, NOW)
    assert consume_upload(session, tmp_storage, link.id, "k", NOW) == b"abc"
    assert link.used_at == NOW
    with pytest.raises(NotFound, match="already used"):
        consume_upload(session, tmp_storage, link.id, "k", NOW)


def test_consume_keeps_the_file_until_discarded(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, NOW)
    consume_upload(session, tmp_storage, link.id, "k", NOW)
    assert len(_files(tmp_storage)) == 1  # still there: the import may yet roll back
    discard_upload(session, tmp_storage, link.id)
    assert _files(tmp_storage) == [] and link.path is None
    assert link.used_at == NOW
    discard_upload(session, tmp_storage, link.id)  # twice is harmless


def test_rolled_back_consume_leaves_the_upload_usable(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, NOW)
    attempt = session.begin_nested()
    consume_upload(session, tmp_storage, link.id, "k", NOW)
    attempt.rollback()  # the import that used the bytes failed
    assert consume_upload(session, tmp_storage, link.id, "k", NOW) == b"abc"


def test_missing_file_says_to_upload_again(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, NOW)
    (tmp_storage.root / link.path).unlink()
    with pytest.raises(NotFound, match="call request_upload again"):
        consume_upload(session, tmp_storage, link.id, "k", NOW)
    assert link.used_at is None


def test_purge_removes_the_file_of_a_used_upload(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, NOW)
    consume_upload(session, tmp_storage, link.id, "k", NOW)  # never discarded (a crash)
    assert purge_expired(session, tmp_storage, EXPIRED) == 1
    assert _files(tmp_storage) == []


def test_another_actor_cannot_consume(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, NOW)
    with pytest.raises(NotFound):
        consume_upload(session, tmp_storage, link.id, "someone-else", NOW)
    assert link.used_at is None
    assert consume_upload(session, tmp_storage, link.id, "k", NOW) == b"abc"


def test_upload_never_received_cannot_be_consumed(session, tmp_storage):
    link, _ = create_upload(session, tmp_storage, "k", NOW)
    with pytest.raises(NotFound):
        consume_upload(session, tmp_storage, link.id, "k", NOW)
    with pytest.raises(NotFound):
        consume_upload(session, tmp_storage, 999999, "k", NOW)


def test_upload_over_the_limit_writes_nothing(session, tmp_storage):
    link, token = create_upload(session, tmp_storage, "k", NOW)
    with pytest.raises(InvalidChangeError, match="file too large"):
        receive_upload(session, tmp_storage, token, b"x" * 11, 10, NOW)
    assert _files(tmp_storage) == []
    assert link.received_at is None
    receive_upload(session, tmp_storage, token, b"x" * 10, 10, NOW)  # at the limit: fine


def test_download_is_single_use_and_expires(session, tmp_storage):
    link, token = _download(session, tmp_storage)
    assert link.size == len(b"exported")
    with pytest.raises(NotFound):
        serve_download(session, tmp_storage, token, EXPIRED)
    served, data = serve_download(session, tmp_storage, token, JUST_BEFORE)
    assert served is link and data == b"exported"
    assert (link.filename, link.media_type, link.used_at) == ("book.docx", DOCX, JUST_BEFORE)
    assert _files(tmp_storage) == []
    with pytest.raises(NotFound):
        serve_download(session, tmp_storage, token, JUST_BEFORE)


def test_unknown_token_and_wrong_kind_are_not_found(session, tmp_storage):
    up, up_token = create_upload(session, tmp_storage, "k", NOW)
    down, down_token = _download(session, tmp_storage)
    with pytest.raises(NotFound):
        receive_upload(session, tmp_storage, "not-a-token", b"abc", 10, NOW)
    with pytest.raises(NotFound):
        serve_download(session, tmp_storage, "not-a-token", NOW)
    with pytest.raises(NotFound):
        receive_upload(session, tmp_storage, down_token, b"abc", 10, NOW)
    with pytest.raises(NotFound):
        serve_download(session, tmp_storage, up_token, NOW)
    with pytest.raises(NotFound):
        consume_upload(session, tmp_storage, down.id, "k", NOW)


def test_only_the_token_hash_is_stored(session, tmp_storage):
    for link, token in (
        create_upload(session, tmp_storage, "k", NOW),
        _download(session, tmp_storage),
    ):
        assert len(token) >= 43  # secrets.token_urlsafe(32)
        assert link.token_hash == hashlib.sha256(token.encode()).hexdigest()
        row = session.get(TransferLink, link.id)
        values = [getattr(row, c.key) for c in TransferLink.__table__.columns]
        assert not any(token in str(v) for v in values)


def test_purge_expired_removes_rows_and_files(session, tmp_storage):
    old_up, token = create_upload(session, tmp_storage, "k", NOW)
    receive_upload(session, tmp_storage, token, b"abc", 10, NOW)
    old_down, _ = _download(session, tmp_storage)
    fresh, _ = _download(session, tmp_storage, now=NOW + timedelta(minutes=10))
    assert len(_files(tmp_storage)) == 3
    old_ids = {old_up.id, old_down.id}

    assert purge_expired(session, tmp_storage, EXPIRED) == 2
    assert [f.name for f in _files(tmp_storage)] == [str(fresh.id)]
    remaining = {link.id for link in session.query(TransferLink)}
    assert remaining == {fresh.id} and not remaining & old_ids


@pytest.mark.parametrize("create", ["upload", "download"])
def test_creating_a_link_purges_expired_ones(session, tmp_storage, create):
    old, _ = _download(session, tmp_storage)
    later = NOW + timedelta(minutes=20)
    if create == "upload":
        new, _ = create_upload(session, tmp_storage, "k", later)
    else:
        new, _ = _download(session, tmp_storage, now=later)
    assert {link.id for link in session.query(TransferLink)} == {new.id}
    assert str(old.id) not in [f.name for f in _files(tmp_storage)]
