"""W1-T2 (#5): authz derived from the validated WorkOS token — actor +
subject-allowlist gate, no-token dev path preserved."""
import pytest

from sierra_mcp import context


class _Tok:
    def __init__(self, claims):
        self.claims = claims


def test_no_token_is_operator_full_grant(monkeypatch):
    """Auth-disabled loopback dev (and the whole existing test suite): no request
    token → constant operator + full grant. Preserves legacy behavior."""
    monkeypatch.setattr(context, "get_access_token", lambda: None)
    monkeypatch.delenv("SIERRA_MCP_SUBJECT_ALLOWLIST", raising=False)
    assert context.actor() == "operator"
    assert context.granted_scopes() == {"read", "write", "delete"}


def test_token_actor_is_email_and_full_grant(monkeypatch):
    monkeypatch.setattr(context, "get_access_token",
                        lambda: _Tok({"sub": "u_1", "email": "op@x.com"}))
    monkeypatch.delenv("SIERRA_MCP_SUBJECT_ALLOWLIST", raising=False)
    assert context.actor() == "op@x.com"               # real non-repudiation
    assert context.granted_scopes() == {"read", "write", "delete"}


def test_token_actor_falls_back_to_sub_without_email(monkeypatch):
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"sub": "user_abc"}))
    monkeypatch.delenv("SIERRA_MCP_SUBJECT_ALLOWLIST", raising=False)
    assert context.actor() == "user_abc"


def test_allowlist_allows_listed_subject(monkeypatch):
    monkeypatch.setattr(context, "get_access_token",
                        lambda: _Tok({"sub": "u_1", "email": "op@x.com"}))
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "op@x.com, other@y.com")
    assert context.actor() == "op@x.com"
    assert context.granted_scopes() == {"read", "write", "delete"}


def test_allowlist_matches_on_sub_too(monkeypatch):
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"sub": "user_abc"}))
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "user_abc")
    assert context.actor() == "user_abc"


def test_allowlist_denies_unlisted_subject(monkeypatch):
    """Fail-closed: a valid token whose subject isn't allowlisted is refused on BOTH
    the actor and scope paths, so no mutation can proceed."""
    monkeypatch.setattr(context, "get_access_token",
                        lambda: _Tok({"sub": "u_2", "email": "intruder@z.com"}))
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "op@x.com")
    with pytest.raises(PermissionError):
        context.actor()
    with pytest.raises(PermissionError):
        context.granted_scopes()
