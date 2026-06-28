"""Tests for sierra_mcp.audit — schema, append-only triggers, redaction,
ledger round-trip, and the delete snapshot_sink."""
import json
import sqlite3
import threading

import pytest

from sierra_mcp import audit


@pytest.fixture
def conn():
    c = audit.connect(":memory:")
    try:
        yield c
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #

def test_connect_creates_tables_indexes_and_triggers(conn):
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger','index')"
        )
    }
    assert {"audit_log", "ledger", "confirm_tokens"} <= names
    assert {"audit_no_update", "audit_no_delete"} <= names
    assert {"ix_audit_tenant_ts", "ix_audit_entity", "ix_ledger_tenant_type"} <= names


def test_connect_is_idempotent_and_sets_row_factory():
    c = audit.connect(":memory:")
    assert c.row_factory is sqlite3.Row
    audit.init_schema(c)  # second call must not raise (IF NOT EXISTS)
    c.close()


def test_now_iso_is_utc_isoformat():
    ts = audit.now_iso()
    # Round-trips to a tz-aware datetime in UTC.
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)


# --------------------------------------------------------------------------- #
# audit_event
# --------------------------------------------------------------------------- #

def test_audit_event_inserts_and_returns_rowid(conn):
    rid = audit.audit_event(
        conn,
        tenant_id="op",
        actor="user@example.com",
        tool="delete_content_page",
        action="delete",
        result="ok",
        scope="delete",
        endpoint="/content-pages.aspx/DeleteContentPage",
        entity_type="content_page",
        entity_id=8801,
        title_snapshot="About Us",
        reversible=False,
        request_id="req-1",
    )
    assert isinstance(rid, int) and rid > 0

    row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (rid,)).fetchone()
    assert row["tenant_id"] == "op"
    assert row["actor"] == "user@example.com"
    assert row["tool"] == "delete_content_page"
    assert row["action"] == "delete"
    assert row["result"] == "ok"
    assert row["entity_id"] == "8801"          # coerced to TEXT
    assert row["reversible"] == 0              # False -> 0
    assert row["ts"]                           # ts populated


def test_audit_event_redacts_secret_keys_recursively(conn):
    rid = audit.audit_event(
        conn,
        tenant_id="op",
        actor="user",
        tool="save_html_widget",
        action="write",
        result="ok",
        args_redacted={
            "username": "bob",
            "password": "hunter2",
            "api_key": "sk-live-123",
            "SecretValue": "nope",
            "nested": {"auth_token": "xyz", "keep": 1},
            "list": [{"client_secret": "zzz"}, "plain"],
        },
    )
    stored = json.loads(
        conn.execute(
            "SELECT args_redacted FROM audit_log WHERE id = ?", (rid,)
        ).fetchone()["args_redacted"]
    )
    assert stored["username"] == "bob"          # non-secret preserved
    assert stored["password"] == "***"
    assert stored["api_key"] == "***"           # matches 'key'
    assert stored["SecretValue"] == "***"       # case-insensitive
    assert stored["nested"]["auth_token"] == "***"
    assert stored["nested"]["keep"] == 1        # non-secret nested preserved
    assert stored["list"][0]["client_secret"] == "***"
    assert stored["list"][1] == "plain"


def test_audit_event_json_encodes_dict_before_after(conn):
    rid = audit.audit_event(
        conn,
        tenant_id="op",
        actor="user",
        tool="save_content_page",
        action="write",
        result="ok",
        before_json={"name": "old"},
        after_json={"name": "new"},
    )
    row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (rid,)).fetchone()
    assert json.loads(row["before_json"]) == {"name": "old"}
    assert json.loads(row["after_json"]) == {"name": "new"}


def test_audit_event_passes_through_prebuilt_json_string(conn):
    rid = audit.audit_event(
        conn,
        tenant_id="op",
        actor="user",
        tool="t",
        action="write",
        result="ok",
        args_redacted='{"already":"json"}',
    )
    row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (rid,)).fetchone()
    assert row["args_redacted"] == '{"already":"json"}'


# --------------------------------------------------------------------------- #
# append-only triggers  (the headline guarantee)
# --------------------------------------------------------------------------- #

def _seed_one(conn):
    return audit.audit_event(
        conn, tenant_id="op", actor="u", tool="t", action="write", result="ok"
    )


def test_audit_log_rejects_update(conn):
    rid = _seed_one(conn)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)) as ei:
        conn.execute("UPDATE audit_log SET result = 'tampered' WHERE id = ?", (rid,))
    assert "append-only" in str(ei.value)
    # Row is unchanged.
    assert conn.execute(
        "SELECT result FROM audit_log WHERE id = ?", (rid,)
    ).fetchone()["result"] == "ok"


def test_audit_log_rejects_delete(conn):
    rid = _seed_one(conn)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)) as ei:
        conn.execute("DELETE FROM audit_log WHERE id = ?", (rid,))
    assert "append-only" in str(ei.value)
    assert conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"] == 1


def test_audit_log_rejects_bulk_delete(conn):
    _seed_one(conn)
    _seed_one(conn)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        conn.execute("DELETE FROM audit_log")
    assert conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"] == 2


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #

def test_ledger_record_and_mark_cleanup_roundtrip(conn):
    lid = audit.ledger_record(
        conn,
        tenant_id="op",
        entity_type="content_page",
        action="created",
        entity_id=999,
        title_snapshot="Test Page",
        payload_snapshot={"page": {"id": 999}},
        reversible=True,
        cleanup_status="pending",
        authorization="user-approved-2026-06-27",
    )
    row = conn.execute("SELECT * FROM ledger WHERE id = ?", (lid,)).fetchone()
    assert row["entity_type"] == "content_page"
    assert row["action"] == "created"
    assert row["entity_id"] == "999"
    assert row["reversible"] == 1
    assert row["cleanup_status"] == "pending"
    assert row["authorization"] == "user-approved-2026-06-27"
    assert json.loads(row["payload_snapshot"]) == {"page": {"id": 999}}
    assert row["created_at"]
    assert row["deleted_at"] is None

    # Non-deleting status update: deleted_at stays NULL.
    audit.ledger_mark_cleanup(conn, lid, "in_progress")
    row = conn.execute("SELECT * FROM ledger WHERE id = ?", (lid,)).fetchone()
    assert row["cleanup_status"] == "in_progress"
    assert row["deleted_at"] is None

    # Deleting status update: stamps deleted_at.
    audit.ledger_mark_cleanup(conn, lid, "done", deleted=True)
    row = conn.execute("SELECT * FROM ledger WHERE id = ?", (lid,)).fetchone()
    assert row["cleanup_status"] == "done"
    assert row["deleted_at"]


# --------------------------------------------------------------------------- #
# make_snapshot_sink
# --------------------------------------------------------------------------- #

def test_snapshot_sink_pulls_identity_from_page_record(conn):
    sink = audit.make_snapshot_sink(
        conn, tenant_id="op", entity_type="content_page", authorization="auth-1"
    )
    record = {"page": {"id": 8801, "name": "About Us"}, "components": [{"x": 1}]}
    lid = sink(record)

    row = conn.execute("SELECT * FROM ledger WHERE id = ?", (lid,)).fetchone()
    assert row["action"] == "deleted"
    assert row["entity_type"] == "content_page"
    assert row["entity_id"] == "8801"
    assert row["title_snapshot"] == "About Us"
    assert row["authorization"] == "auth-1"
    # Full record preserved for recovery.
    assert json.loads(row["payload_snapshot"]) == record


def test_snapshot_sink_pulls_identity_from_searchname_record(conn):
    sink = audit.make_snapshot_sink(
        conn, tenant_id="op", entity_type="saved_search"
    )
    record = {"searchName": "Waterfront Homes", "criteria": {"minPrice": 500000}}
    lid = sink(record)

    row = conn.execute("SELECT * FROM ledger WHERE id = ?", (lid,)).fetchone()
    assert row["action"] == "deleted"
    assert row["title_snapshot"] == "Waterfront Homes"
    assert row["entity_id"] is None             # no id in this record shape
    assert json.loads(row["payload_snapshot"]) == record


def test_snapshot_sink_pulls_identity_from_flat_id_name_record(conn):
    sink = audit.make_snapshot_sink(conn, tenant_id="op", entity_type="widget")
    lid = sink({"id": 42, "name": "Hero Widget"})
    row = conn.execute("SELECT * FROM ledger WHERE id = ?", (lid,)).fetchone()
    assert row["entity_id"] == "42"
    assert row["title_snapshot"] == "Hero Widget"


def test_snapshot_sink_raises_on_broken_conn():
    """A failed snapshot INSERT must propagate so an upstream hard-delete aborts."""
    c = audit.connect(":memory:")
    sink = audit.make_snapshot_sink(c, tenant_id="op", entity_type="content_page")
    c.close()  # break the connection
    with pytest.raises(sqlite3.Error):
        sink({"page": {"id": 1, "name": "X"}})


# --------------------------------------------------------------------------- #
# W1-T1: thread-safe DB (#3), broadened redaction of before/after/snapshot (#14),
# audit_reject (#8a)
# --------------------------------------------------------------------------- #

def test_connect_sets_wal_and_busy_timeout(tmp_path):
    c = audit.connect(str(tmp_path / "x.db"))
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert int(c.execute("PRAGMA busy_timeout").fetchone()[0]) == 5000
    finally:
        c.close()


def test_audit_event_redacts_before_and_after(conn):
    audit.audit_event(
        conn, tenant_id="op", actor="a", tool="t", action="x", result="ok",
        before_json={"name": "Home", "authorization": "Bearer abc", "cookie": "s=1"},
        after_json={"password": "p", "session": "sess", "title": "T"},
    )
    row = conn.execute(
        "SELECT before_json, after_json FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    before, after = json.loads(row["before_json"]), json.loads(row["after_json"])
    assert before["name"] == "Home"                       # non-secret preserved
    assert before["authorization"] == "***" and before["cookie"] == "***"
    assert after["password"] == "***" and after["session"] == "***"
    assert after["title"] == "T"


def test_ledger_payload_snapshot_redacts_secret_named_keys(conn):
    audit.ledger_record(
        conn, tenant_id="op", entity_type="widget", action="deleted",
        payload_snapshot={"id": 1, "name": "W", "apiKey": "xyz", "bearer": "b"},
    )
    snap = json.loads(
        conn.execute("SELECT payload_snapshot FROM ledger ORDER BY id DESC LIMIT 1").fetchone()[0]
    )
    assert snap["id"] == 1 and snap["name"] == "W"
    assert snap["apiKey"] == "***" and snap["bearer"] == "***"


def test_audit_reject_writes_a_rejected_row(conn):
    rid = audit.audit_reject(
        conn, tenant_id="op", actor="a@b.com", tool="sierra_call", action="call",
        scope="delete", error="refused: locked-destructive",
        args_redacted={"path": "/content-pages.aspx/DeleteContentPages", "password": "p"},
        endpoint="/content-pages.aspx/DeleteContentPages", entity_type="content_page",
    )
    row = conn.execute(
        "SELECT result, error, tool, scope, args_redacted FROM audit_log WHERE id = ?", (rid,)
    ).fetchone()
    assert row["result"] == "rejected"
    assert "refused" in row["error"] and row["scope"] == "delete"
    assert json.loads(row["args_redacted"])["password"] == "***"


def test_concurrent_writes_on_shared_conn_no_programmingerror(conn):
    """The #3 fix: one shared conn (check_same_thread=False) + _DB_LOCK serializes
    writes across FastMCP worker threads. Pre-fix this raised sqlite3.ProgrammingError."""
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for _ in range(20):
                audit.audit_event(conn, tenant_id="op", actor=f"t{n}", tool="x",
                                  action="a", result="ok")
        except Exception as e:  # noqa: BLE001 - capturing for the assertion
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"concurrent audit raised: {errors!r}"
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 8 * 20
