"""Closure-regression suite for the 2026-06-28 adversarial audit (plan 015).

One section per CRITICAL/HIGH finding, each test mapping a finding number to the
property that proves it closed. These characterize SHIPPED remediations (Waves
1-2), so they pass today; a future failure here means a fix regressed.

Findings (see AUDIT-phase2-2026-06-28.md):
  #1/#2/#6/#7  Tier-2 sierra_call cannot destroy (default-deny) ........ §1
  #3/#11       shared-conn + tracker are thread-safe ................... §2
  #5           authz (scopes + actor) is derived from the token ....... §3
  #9           Sierra HTTP/error envelopes surface, not swallowed ..... §4
  #14          redaction scrubs before/after/args + broadened regex ... §5
"""
import threading

import pytest

from sierra_core import parsing, transport
from sierra_core.errors import EndpointError
from sierra_mcp import audit, context, guards, tools_generic
from sierra_mcp.catalogue import endpoint_paths
from sierra_mcp.guards import ConfirmTokenError, ScopeError, VolumeCapError


# ========================================================================== #
# §1  #1/#2/#6/#7 — Tier-2 generic caller is default-deny: it cannot destroy.
# ========================================================================== #

def test_no_catalogued_destructive_path_is_executable():
    """The exact CRITICAL-1 class: NOTHING destructive in the 642-endpoint
    catalogue may classify as read/write — every one is refused."""
    destructive = [
        p for p in endpoint_paths()
        if tools_generic._is_destructive(tools_generic._method_of(p))
    ]
    assert destructive, "sanity: the catalogue contains destructive endpoints"
    leaked = [p for p in destructive if tools_generic.classify(p) != "refused"]
    assert leaked == [], f"destructive paths reachable via Tier-2: {leaked}"


@pytest.mark.parametrize("method", [
    # the plural / batch / alternate variants the original denylist missed
    "DeleteContentPages", "DeleteSection", "DeleteSavedSearches",
    "BulkDeleteLeads", "BulkDelete", "PurgeLeads",
    # #7 destructive verbs that used to under-classify as benign "write"
    "RemoveHtmlWidget", "MergeSavedSearches", "MergeLeads",
])
def test_known_destructive_variants_refused(method):
    assert tools_generic.classify(f"/anything.aspx/{method}") == "refused"


def test_unrecognized_verb_is_refused_fail_closed():
    """Default-deny: a verb in neither the read nor the write allowlist is refused,
    not silently treated as a write."""
    assert tools_generic.classify("/anything.aspx/FrobnicateThing") == "refused"
    assert tools_generic.classify("/anything.aspx/Teleport") == "refused"


def test_curated_read_and_write_verbs_still_pass():
    """Default-deny must not break legitimate Tier-2 reads/writes."""
    assert tools_generic.classify("/content-pages.aspx/GetFilters") == "read"
    assert tools_generic.classify("/content-pages.aspx/UpdateContentLabel") == "write"


def test_sierra_call_refuses_and_audits_a_real_destructive_path():
    """End-to-end: a catalogued destructive path raises ValueError, contacts Sierra
    NOT AT ALL, and leaves a result='rejected' audit row (closes #8 for Tier-2 too)."""
    conn = audit.connect(":memory:")
    context.use(conn=conn)
    try:
        dpath = next(
            p for p in endpoint_paths()
            if tools_generic._is_destructive(tools_generic._method_of(p))
        )
        with pytest.raises(ValueError):
            tools_generic.sierra_call(dpath)
        rejected = conn.execute(
            "SELECT endpoint FROM audit_log WHERE result = 'rejected'"
        ).fetchall()
        assert any(r["endpoint"] == dpath for r in rejected)
    finally:
        context.reset()
        conn.close()


# ========================================================================== #
# §2  #3/#11 — the safety subsystem is thread-safe (shared conn + tracker).
# ========================================================================== #

def test_shared_connection_survives_20_thread_full_stack():
    """20 threads each mint -> redeem -> audit on ONE shared connection. The pre-fix
    code raised sqlite3.ProgrammingError once two calls overlapped (#3)."""
    conn = audit.connect(":memory:")
    errors: list[str] = []

    def worker(i: int):
        try:
            payload = {"n": i}
            tok = guards.mint_token(
                conn, tenant_id="op", tool="t", scope_required="write", payload=payload
            )["confirm_token"]
            guards.redeem_token(conn, tok, tenant_id="op", tool="t", payload=payload)
            audit.audit_event(
                conn, tenant_id="op", actor="op", tool="t",
                action="commit", result="ok", args_redacted=payload,
            )
        except Exception as e:  # capture, never swallow
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrency errors: {errors[:3]}"
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 20
    used = conn.execute(
        "SELECT COUNT(*) FROM confirm_tokens WHERE used_at IS NOT NULL"
    ).fetchone()[0]
    assert used == 20  # every token spent exactly once
    conn.close()


def test_volume_tracker_cap_holds_under_50_thread_race():
    """The cap that bounds irreversible deletes can't be over-reserved concurrently (#11)."""
    tracker = guards.VolumeTracker(delete_cap=20)
    granted = 0
    glock = threading.Lock()

    def worker():
        nonlocal granted
        try:
            tracker.check_and_reserve("op", "delete", n=1)
        except VolumeCapError:
            return
        with glock:
            granted += 1

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert granted == 20
    assert tracker.current("op", "delete") == 20


# ========================================================================== #
# §3  #5 — authz: scopes AND actor derive from the validated token (+allowlist).
# ========================================================================== #

class _FakeToken:
    def __init__(self, claims):
        self.claims = claims


def test_no_token_is_operator_with_full_grant(monkeypatch):
    monkeypatch.setattr(context, "get_access_token", lambda: None)
    assert context.actor() == "operator"
    assert context.granted_scopes() == {"read", "write", "delete"}


def test_actor_is_the_token_subject(monkeypatch):
    monkeypatch.delenv("SIERRA_MCP_SUBJECT_ALLOWLIST", raising=False)
    monkeypatch.setattr(
        context, "get_access_token",
        lambda: _FakeToken({"email": "agent@firm.com", "sub": "user_1"}),
    )
    assert context.actor() == "agent@firm.com"  # non-repudiation, not "operator"
    assert context.granted_scopes() == {"read", "write", "delete"}


def test_allowlist_denies_unlisted_subject_fail_closed(monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "alice@firm.com,bob@firm.com")
    monkeypatch.setattr(
        context, "get_access_token",
        lambda: _FakeToken({"email": "intruder@evil.com", "sub": "user_9"}),
    )
    with pytest.raises(PermissionError):
        context.actor()
    with pytest.raises(PermissionError):
        context.granted_scopes()


def test_allowlist_admits_listed_subject(monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_SUBJECT_ALLOWLIST", "alice@firm.com,bob@firm.com")
    monkeypatch.setattr(
        context, "get_access_token",
        lambda: _FakeToken({"email": "bob@firm.com", "sub": "user_2"}),
    )
    assert context.actor() == "bob@firm.com"
    assert context.granted_scopes() == {"read", "write", "delete"}


# ========================================================================== #
# §4  #9 — Sierra failures surface as errors, not swallowed as success.
# ========================================================================== #

class _FakeResp:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400


def _transport_with(resp: _FakeResp) -> transport.HttpxTransport:
    # Bypass __init__ (which would open a real httpx.Client) and inject a stub.
    t = transport.HttpxTransport.__new__(transport.HttpxTransport)

    class _Client:
        def post(self, path, content=None):
            return resp

    t._client = _Client()
    return t


def test_transport_raises_on_5xx():
    with pytest.raises(EndpointError, match="HTTP 500"):
        _transport_with(_FakeResp(500, "Internal Server Error")).post_json("/x", {})


def test_transport_raises_on_4xx():
    with pytest.raises(EndpointError, match="HTTP 404"):
        _transport_with(_FakeResp(404, "Not Found")).post_json("/x", {})


def test_transport_passes_3xx_through_for_logout_detection():
    """A 302 session-expiry redirect must NOT raise — the logout detector needs the
    body. Only 4xx/5xx raise."""
    assert _transport_with(_FakeResp(302, "<login redirect>")).post_json("/x", {}) == (
        "<login redirect>"
    )


def test_parsing_raises_on_aspnet_exception_envelope():
    """An ASP.NET page-method exception (even at HTTP 200) must raise, not be returned
    as if it were valid data — the bug that reported a failed delete as PASS (#9)."""
    body = '{"d": "{\\"Message\\": \\"boom\\", \\"StackTrace\\": \\"at X\\"}"}'
    with pytest.raises(EndpointError, match="server error"):
        parsing.unwrap_response(body)


def test_parsing_raises_on_nonzero_responsecode():
    body = '{"d": "{\\"responseCode\\": 1, \\"message\\": \\"denied\\"}"}'
    with pytest.raises(EndpointError, match="responseCode 1"):
        parsing.unwrap_response(body)


# ========================================================================== #
# §5  #14 — redaction scrubs before/after/args + the broadened secret regex.
# ========================================================================== #

def test_redaction_scrubs_all_three_state_columns():
    conn = audit.connect(":memory:")
    secrets = {"password": "hunter2", "page_name": "Keep Me"}
    audit.audit_event(
        conn, tenant_id="op", actor="op", tool="t", action="commit", result="ok",
        args_redacted=secrets, before_json=secrets, after_json=secrets,
    )
    row = conn.execute(
        "SELECT args_redacted, before_json, after_json FROM audit_log"
    ).fetchone()
    for col in ("args_redacted", "before_json", "after_json"):
        blob = row[col]
        assert "hunter2" not in blob, f"{col} leaked a secret value"
        assert "Keep Me" in blob, f"{col} dropped a non-secret value"
    conn.close()


@pytest.mark.parametrize("key", [
    "authorization", "cookie", "bearer", "session", "jwt", "api_key",
    "access_token", "x_auth", "Secret",
])
def test_broadened_regex_redacts_secret_named_keys(key):
    conn = audit.connect(":memory:")
    audit.audit_event(
        conn, tenant_id="op", actor="op", tool="t", action="commit", result="ok",
        args_redacted={key: "SENSITIVE", "keep": "ok"},
    )
    blob = conn.execute("SELECT args_redacted FROM audit_log").fetchone()["args_redacted"]
    assert "SENSITIVE" not in blob, f"key {key!r} was not redacted"
    assert "ok" in blob
    conn.close()
