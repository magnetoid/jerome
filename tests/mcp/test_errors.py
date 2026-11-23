import asyncio

import pytest
from fastapi import HTTPException

from jerome.api.app import _ERROR_STATUS
from jerome.core import transfer_links
from jerome.mcp import errors
from jerome.mcp.files import put_file


def test_mcp_error_table_covers_the_same_classes_as_rest():
    assert set(errors._CODES) == set(_ERROR_STATUS)


def test_rate_limited_carries_the_retry_after_wait():
    exc = HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "7"})
    assert str(errors.to_tool_error(exc)) == "rate_limited: Rate limit exceeded (retry after 7 s)"


def test_rate_limited_without_a_header_is_just_the_detail():
    exc = HTTPException(429, "Daily cost cap reached")
    assert str(errors.to_tool_error(exc)) == "rate_limited: Daily cost cap reached"


class _NoBody:
    """A request whose body must never be read."""

    headers: dict[str, str] = {}

    def stream(self):
        raise AssertionError("the body was read")


def test_put_with_an_unknown_token_does_not_read_the_body(session, tmp_storage, test_settings):
    with pytest.raises(transfer_links.NotFound):
        asyncio.run(put_file("nope", _NoBody(), session, tmp_storage, test_settings))
