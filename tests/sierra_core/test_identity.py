import pytest
from sierra_core.identity import assert_identity, titles_match
from sierra_core.errors import IdentityLockError

def test_match_is_normalized():
    assert titles_match("  About  Us ", "about us")

def test_assert_passes_on_match():
    assert_identity(supplied_title="About Us", stored_title="about us", entity_id=42)  # no raise

def test_assert_raises_on_mismatch():
    with pytest.raises(IdentityLockError):
        assert_identity(supplied_title="Contact", stored_title="About Us", entity_id=42)

def test_empty_stored_title_always_fails():
    with pytest.raises(IdentityLockError):
        assert_identity(supplied_title="", stored_title="", entity_id=1)

def test_empty_supplied_title_always_fails():
    with pytest.raises(IdentityLockError):
        assert_identity(supplied_title="", stored_title="About Us", entity_id=2)
