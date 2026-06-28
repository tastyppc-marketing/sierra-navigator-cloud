"""W4-T5/T6 (re-audit New-3 HIGH, #8/New-4 MED): per-identity access is ENFORCED and
AUDITED at every tool entry.

New-3: the 10 Tier-1 read tools called runtime.read() directly and never consulted the
subject allowlist, so a non-allowlisted WorkOS token could read the whole backend.
#8/New-4: an allowlist PermissionError (and the Tier-2 read scope check) sat outside the
audited path, so an authenticated-but-unauthorized caller could probe the surface with
ZERO audit rows. context.authorize() closes both: it enforces allowlist + scope and
writes a result='rejected' row before re-raising.
"""
import pytest

from sierra_mcp import audit, context, server, tools_write
from sierra_mcp.guards import ScopeError


class _Tok:
    def __init__(self, claims):
        self.claims = claims


@pytest.fixture
def conn():
    c = audit.connect(":memory:")
    context.use(conn=c)
    try:
        yield c
    finally:
        context.reset()
        c.close()


def _rejected(conn):
    return [
        (r["tool"], r["action"], r["actor"])
        for r in conn.execute(
            "SELECT tool, action, actor FROM audit_log WHERE result = 'rejected'"
        ).fetchall()
    ]


# --- context.authorize: the shared enforcement+audit primitive ------------ #

def test_authorize_allows_allowlisted_subject_and_returns_actor(conn, monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "allowed@firm.com")
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"email": "allowed@firm.com"}))
    actor = context.authorize(conn, tool="get_page", action="read", scope="read")
    assert actor == "allowed@firm.com"
    assert _rejected(conn) == []  # no denial row on success


def test_authorize_denies_non_allowlisted_subject_and_audits(conn, monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "allowed@firm.com")
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"email": "intruder@evil.com"}))
    with pytest.raises(PermissionError):
        context.authorize(conn, tool="get_page", action="read", scope="read")
    # audited with the DENIED subject as actor (non-repudiation), not a placeholder
    assert ("get_page", "read", "intruder@evil.com") in _rejected(conn)


def test_authorize_audits_scope_denial(conn, monkeypatch):
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read"})
    with pytest.raises(ScopeError):
        context.authorize(conn, tool="confirm_deletions", action="delete", scope="delete")
    assert any(t == "confirm_deletions" and a == "delete" for t, a, _ in _rejected(conn))


def test_token_subject_never_raises_even_off_allowlist(monkeypatch):
    # token_subject() is for audit labelling only — it must NEVER gate or raise, even
    # for a subject the allowlist would reject.
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "allowed@firm.com")
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"email": "intruder@evil.com"}))
    assert context.token_subject() == "intruder@evil.com"


# --- New-3: the Tier-1 read surface is gated --------------------------------#

def test_guarded_read_denies_non_allowlisted_and_never_runs_the_read(conn, monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "allowed@firm.com")
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"email": "intruder@evil.com"}))
    ran = []
    with pytest.raises(PermissionError):
        server._guarded_read("list_saved_searches", lambda c: ran.append(1))
    assert ran == []  # the backend read never executed
    assert any(t == "list_saved_searches" for t, _, _ in _rejected(conn))


# --- #8/New-4: write/delete denials are audited too ------------------------ #

def test_write_tool_denies_non_allowlisted_subject_and_audits(conn, monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "allowed@firm.com")
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"email": "intruder@evil.com"}))
    with pytest.raises(PermissionError):
        tools_write.create_content_label("L")  # dry-run; authorize denies before mint
    assert any(t == "create_content_label" for t, _, _ in _rejected(conn))


# --- re-audit #5 MEDIUM: catalogue RESOURCES are gated too (not just tools) -------- #

def test_catalogue_resource_denies_non_allowlisted_and_audits(conn, monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "allowed@firm.com")
    monkeypatch.setattr(context, "get_access_token", lambda: _Tok({"email": "intruder@evil.com"}))
    with pytest.raises(PermissionError):
        server.sierra_endpoints()
    with pytest.raises(PermissionError):
        server.sierra_endpoints_verified()
    rejected_tools = {t for t, _, _ in _rejected(conn)}
    assert "resource:endpoints" in rejected_tools
    assert "resource:endpoints/verified" in rejected_tools


def test_catalogue_resource_allows_authorized_caller(conn, monkeypatch):
    monkeypatch.setattr(context, "get_access_token", lambda: None)  # no token -> operator
    out = server.sierra_endpoints()
    assert out.startswith("{")           # returns the catalogue JSON
    assert _rejected(conn) == []         # no denial row on an authorized read
