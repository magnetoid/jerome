import pytest

from jerome.documents.changes import Change, apply_changes, validate_changes
from jerome.documents.model import PLACEHOLDER
from jerome.errors import InvalidChangeError


def test_apply_non_overlapping_changes_right_to_left():
    changes = [Change(0, 3, "the"), Change(8, 11, "sits")]
    assert apply_changes("teh cat sat", changes) == "the cat sits"


def test_overlapping_changes_raise():
    with pytest.raises(InvalidChangeError):
        validate_changes("abcdef", [Change(0, 3, "x"), Change(2, 4, "y")])


def test_change_covering_placeholder_raises():
    with pytest.raises(InvalidChangeError):
        validate_changes(f"ab{PLACEHOLDER}cd", [Change(1, 3, "z")])


def test_out_of_range_raises():
    with pytest.raises(InvalidChangeError):
        validate_changes("abc", [Change(2, 5, "x")])


def test_two_insertions_at_same_offset_raise():
    with pytest.raises(InvalidChangeError):
        validate_changes("abc", [Change(1, 1, "x"), Change(1, 1, "y")])


def test_insertion_adjacent_to_replacement_is_allowed():
    assert apply_changes("abc", [Change(1, 1, "X"), Change(1, 2, "Y")]) == "aXYc"


def test_validate_returns_sorted():
    a, b = Change(5, 6, "x"), Change(0, 1, "y")
    assert validate_changes("abcdefg", [a, b]) == [b, a]
