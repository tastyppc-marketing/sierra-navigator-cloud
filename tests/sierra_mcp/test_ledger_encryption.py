import json

import pytest
from cryptography.fernet import Fernet, InvalidToken

from sierra_mcp import audit


def _fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def ledger_key(monkeypatch):
    key = _fernet_key()
    monkeypatch.setenv("SIERRA_MCP_LEDGER_KEY", key)
    audit._reset_ledger_cipher()
    try:
        yield key
    finally:
        audit._reset_ledger_cipher()


def test_ledger_record_encrypts_snapshot_on_disk_and_reads_full_fidelity(ledger_key):
    conn = audit.connect(":memory:")
    record = {"page": {"id": 123, "name": "Private", "password": "s3cret"}}

    ledger_id = audit.ledger_record(
        conn,
        tenant_id="op",
        entity_type="content_page",
        action="deleted",
        payload_snapshot=record,
    )

    raw = conn.execute(
        "SELECT payload_snapshot FROM ledger WHERE id = ?", (ledger_id,)
    ).fetchone()["payload_snapshot"]
    assert raw.startswith("enc:fernet:v1:")
    assert "s3cret" not in raw
    assert audit.ledger_snapshot(conn, ledger_id) == record
    conn.close()


def test_ledger_snapshot_reads_legacy_plaintext_row_with_key_active(ledger_key):
    conn = audit.connect(":memory:")
    record = {"page": {"id": 1, "password": "legacy-secret"}}
    with audit.transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO ledger (
                tenant_id, entity_type, action, payload_snapshot, created_at
            ) VALUES (?,?,?,?,?)
            """,
            ("op", "content_page", "deleted", json.dumps(record), audit.now_iso()),
        )
        ledger_id = cur.lastrowid

    assert audit.ledger_snapshot(conn, ledger_id) == record
    conn.close()


def test_no_key_writes_plaintext_and_reads_snapshot(monkeypatch):
    monkeypatch.delenv("SIERRA_MCP_LEDGER_KEY", raising=False)
    audit._reset_ledger_cipher()
    conn = audit.connect(":memory:")
    record = {"page": {"id": 55, "password": "plain-secret"}}

    ledger_id = audit.ledger_record(
        conn,
        tenant_id="op",
        entity_type="content_page",
        action="deleted",
        payload_snapshot=record,
    )

    raw = conn.execute(
        "SELECT payload_snapshot FROM ledger WHERE id = ?", (ledger_id,)
    ).fetchone()["payload_snapshot"]
    assert not raw.startswith("enc:fernet:v1:")
    assert json.loads(raw) == record
    assert audit.ledger_snapshot(conn, ledger_id) == record
    conn.close()
    audit._reset_ledger_cipher()


def test_ledger_snapshot_raises_invalid_token_with_wrong_key(monkeypatch, ledger_key):
    conn = audit.connect(":memory:")
    ledger_id = audit.ledger_record(
        conn,
        tenant_id="op",
        entity_type="content_page",
        action="deleted",
        payload_snapshot={"page": {"password": "first-key-secret"}},
    )

    monkeypatch.setenv("SIERRA_MCP_LEDGER_KEY", _fernet_key())
    audit._reset_ledger_cipher()

    with pytest.raises(InvalidToken):
        audit.ledger_snapshot(conn, ledger_id)
    conn.close()


def test_ledger_snapshot_supports_multifernet_rotation(monkeypatch):
    old_key = _fernet_key()
    new_key = _fernet_key()
    monkeypatch.setenv("SIERRA_MCP_LEDGER_KEY", old_key)
    audit._reset_ledger_cipher()
    conn = audit.connect(":memory:")
    record = {"widget": {"id": 7, "apiToken": "old-key-token"}}
    ledger_id = audit.ledger_record(
        conn,
        tenant_id="op",
        entity_type="widget",
        action="deleted",
        payload_snapshot=record,
    )

    monkeypatch.setenv("SIERRA_MCP_LEDGER_KEY", f"{new_key},{old_key}")
    audit._reset_ledger_cipher()

    assert audit.ledger_snapshot(conn, ledger_id) == record
    conn.close()
    audit._reset_ledger_cipher()
