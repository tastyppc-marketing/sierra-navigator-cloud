"""Tests for sierra_mcp.guards — confirm-token machine, scopes, volume caps."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from sierra_mcp import audit, guards
from sierra_mcp.guards import (
    ConfirmTokenError,
    GuardError,
    ScopeError,
    VolumeCapError,
    VolumeTracker,
)


@pytest.fixture
def conn():
    c = audit.connect(":memory:")
    try:
        yield c
    finally:
        c.close()


PAYLOAD = {"pageId": 8801, "expected_title": "About Us"}


def _mint(conn, **overrides):
    kw = dict(
        tenant_id="op",
        tool="delete_content_page",
        scope_required="delete",
        payload=PAYLOAD,
    )
    kw.update(overrides)
    return guards.mint_token(conn, **kw)


# --------------------------------------------------------------------------- #
# exception hierarchy
# --------------------------------------------------------------------------- #

def test_guard_exceptions_share_base():
    for exc in (ConfirmTokenError, ScopeError, VolumeCapError):
        assert issubclass(exc, GuardError)


# --------------------------------------------------------------------------- #
# canonical_hash
# --------------------------------------------------------------------------- #

def test_canonical_hash_is_order_independent_and_distinguishing():
    assert guards.canonical_hash({"a": 1, "b": 2}) == guards.canonical_hash(
        {"b": 2, "a": 1}
    )
    assert guards.canonical_hash({"a": 1}) != guards.canonical_hash({"a": 2})


# --------------------------------------------------------------------------- #
# mint / redeem happy path
# --------------------------------------------------------------------------- #

def test_mint_returns_token_envelope(conn):
    out = _mint(conn, ttl_seconds=90)
    assert out["confirm_token"].startswith("ct_")
    assert out["ttl_seconds"] == 90
    assert out["expires_at"]
    # Row persisted, unused.
    row = conn.execute(
        "SELECT * FROM confirm_tokens WHERE token = ?", (out["confirm_token"],)
    ).fetchone()
    assert row["used_at"] is None
    assert row["scope_required"] == "delete"


def test_redeem_happy_path_marks_used(conn):
    token = _mint(conn)["confirm_token"]
    # Returns None, does not raise.
    assert (
        guards.redeem_token(
            conn, token, tenant_id="op", tool="delete_content_page", payload=PAYLOAD
        )
        is None
    )
    row = conn.execute(
        "SELECT used_at FROM confirm_tokens WHERE token = ?", (token,)
    ).fetchone()
    assert row["used_at"] is not None


# --------------------------------------------------------------------------- #
# redeem rejections
# --------------------------------------------------------------------------- #

def test_redeem_rejects_unknown_token(conn):
    with pytest.raises(ConfirmTokenError, match="unknown"):
        guards.redeem_token(
            conn, "ct_nope", tenant_id="op", tool="delete_content_page", payload=PAYLOAD
        )


def test_redeem_rejects_reused_token(conn):
    token = _mint(conn)["confirm_token"]
    guards.redeem_token(
        conn, token, tenant_id="op", tool="delete_content_page", payload=PAYLOAD
    )
    with pytest.raises(ConfirmTokenError, match="already used"):
        guards.redeem_token(
            conn, token, tenant_id="op", tool="delete_content_page", payload=PAYLOAD
        )


def test_redeem_rejects_expired_token(conn):
    token = _mint(conn, ttl_seconds=120)["confirm_token"]
    # Force expiry deterministically (no sleep / no race).
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute(
        "UPDATE confirm_tokens SET expires_at = ? WHERE token = ?", (past, token)
    )
    conn.commit()
    with pytest.raises(ConfirmTokenError, match="expired"):
        guards.redeem_token(
            conn, token, tenant_id="op", tool="delete_content_page", payload=PAYLOAD
        )
    # A rejected redeem must NOT spend the token.
    assert (
        conn.execute(
            "SELECT used_at FROM confirm_tokens WHERE token = ?", (token,)
        ).fetchone()["used_at"]
        is None
    )


def test_redeem_rejects_wrong_tenant(conn):
    token = _mint(conn)["confirm_token"]
    with pytest.raises(ConfirmTokenError, match="tenant"):
        guards.redeem_token(
            conn, token, tenant_id="other", tool="delete_content_page", payload=PAYLOAD
        )


def test_redeem_rejects_wrong_tool(conn):
    token = _mint(conn)["confirm_token"]
    with pytest.raises(ConfirmTokenError, match="tool"):
        guards.redeem_token(
            conn, token, tenant_id="op", tool="delete_saved_search", payload=PAYLOAD
        )


def test_redeem_rejects_mutated_payload(conn):
    token = _mint(conn)["confirm_token"]
    mutated = {"pageId": 9999, "expected_title": "About Us"}  # id changed
    with pytest.raises(ConfirmTokenError, match="payload"):
        guards.redeem_token(
            conn, token, tenant_id="op", tool="delete_content_page", payload=mutated
        )
    # Still unused, so a correct retry could still succeed.
    assert (
        conn.execute(
            "SELECT used_at FROM confirm_tokens WHERE token = ?", (token,)
        ).fetchone()["used_at"]
        is None
    )


# --------------------------------------------------------------------------- #
# scopes
# --------------------------------------------------------------------------- #

def test_require_scope_allows_when_present():
    assert guards.require_scope({"read", "write"}, "write") is None
    assert guards.require_scope(["read", "delete"], "delete") is None


def test_require_scope_raises_when_missing():
    with pytest.raises(ScopeError, match="delete"):
        guards.require_scope({"read", "write"}, "delete")
    with pytest.raises(ScopeError):
        guards.require_scope(set(), "read")


# --------------------------------------------------------------------------- #
# per-call delete cap
# --------------------------------------------------------------------------- #

def test_enforce_delete_call_cap_default_limit():
    guards.enforce_delete_call_cap(list(range(10)))            # exactly at cap -> ok
    with pytest.raises(VolumeCapError):
        guards.enforce_delete_call_cap(list(range(11)))        # over default 10


def test_enforce_delete_call_cap_explicit_cap():
    guards.enforce_delete_call_cap([1, 2], cap=2)
    with pytest.raises(VolumeCapError, match="cap"):
        guards.enforce_delete_call_cap([1, 2, 3], cap=2)


def test_enforce_delete_call_cap_reads_env(monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_DELETE_CALL_CAP", "1")
    guards.enforce_delete_call_cap([1])
    with pytest.raises(VolumeCapError):
        guards.enforce_delete_call_cap([1, 2])


# --------------------------------------------------------------------------- #
# VolumeTracker
# --------------------------------------------------------------------------- #

def test_volume_tracker_caps_and_is_per_tenant():
    t = VolumeTracker(delete_cap=2)
    t.check_and_reserve("tenantA", "delete")
    t.check_and_reserve("tenantA", "delete")
    with pytest.raises(VolumeCapError, match="delete session cap"):
        t.check_and_reserve("tenantA", "delete")          # 3rd over cap=2
    # Independent tenant unaffected.
    t.check_and_reserve("tenantB", "delete")
    assert t.current("tenantA", "delete") == 2
    assert t.current("tenantB", "delete") == 1


def test_volume_tracker_write_and_delete_kinds_are_separate():
    t = VolumeTracker(write_cap=1, delete_cap=1)
    t.check_and_reserve("op", "write")
    t.check_and_reserve("op", "delete")                   # different kind, own counter
    with pytest.raises(VolumeCapError):
        t.check_and_reserve("op", "write", n=1)


def test_volume_tracker_reserve_n_at_once():
    t = VolumeTracker(write_cap=5)
    t.check_and_reserve("op", "write", n=5)               # exactly fills
    with pytest.raises(VolumeCapError):
        t.check_and_reserve("op", "write", n=1)


def test_volume_tracker_unknown_kind_raises():
    t = VolumeTracker()
    with pytest.raises(ValueError, match="unknown volume kind"):
        t.check_and_reserve("op", "publish")


def test_module_singleton_reset_clears_counts():
    guards.TRACKER.reset()
    guards.TRACKER.check_and_reserve("op", "write")
    assert guards.TRACKER.current("op", "write") == 1
    guards.TRACKER.reset()
    assert guards.TRACKER.current("op", "write") == 0


# --------------------------------------------------------------------------- #
# W2-T9 (#11/#3): concurrency — atomic cap reserve + one-time redeem under load
# --------------------------------------------------------------------------- #

def test_concurrent_check_and_reserve_never_exceeds_cap():
    t = VolumeTracker(write_cap=50, delete_cap=20)
    granted = 0
    glock = threading.Lock()

    def worker():
        nonlocal granted
        try:
            t.check_and_reserve("op", "delete", n=1)
        except VolumeCapError:
            return
        with glock:
            granted += 1

    threads = [threading.Thread(target=worker) for _ in range(100)]  # 100 racing, cap 20
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert granted == 20                    # exactly the cap, never more
    assert t.current("op", "delete") == 20


def test_concurrent_redeem_spends_token_exactly_once(conn):
    token = _mint(conn)["confirm_token"]
    successes = 0
    slock = threading.Lock()

    def worker():
        nonlocal successes
        try:
            guards.redeem_token(conn, token, tenant_id="op",
                                tool="delete_content_page", payload=PAYLOAD)
        except ConfirmTokenError:
            return
        with slock:
            successes += 1

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert successes == 1                    # one-time, even under a 12-way race
