"""W5-T3 (re-audit #2 #14 MEDIUM): audit redaction must catch secrets by VALUE, not only
by key name.

The append-only audit_log is immutable (triggers block UPDATE/DELETE), so a secret written
verbatim can never be scrubbed. Key-name-only redaction missed secrets under arbitrary keys
in the client-controlled generic-caller body. We add (a) a few more secret key names and
(b) value-pattern scrubbing for unambiguous secret shapes (JWT, Bearer/Basic tokens), which
fires regardless of the key. The verbatim ledger recovery snapshot (New-5) is NOT affected —
ledger_record never calls _redact.
"""
import json

import pytest

from sierra_mcp import audit


def _args_blob(conn):
    return conn.execute("SELECT args_redacted FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[
        "args_redacted"
    ]


def test_secret_values_scrubbed_under_innocuous_keys():
    conn = audit.connect(":memory:")
    body = {
        "note": "Bearer abcdefghijklmnop123456",  # innocuous key, auth-token value
        "blob": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_part_here_xyz",  # JWT
        "keep": "ordinary text value",
    }
    audit.audit_event(
        conn, tenant_id="op", actor="op", tool="sierra_call", action="call", result="ok",
        args_redacted=body,
    )
    blob = _args_blob(conn)
    assert "Bearer abcdefghijklmnop123456" not in blob
    assert "eyJhbGciOiJIUzI1NiJ9" not in blob
    assert "ordinary text value" in blob  # non-secret preserved (no over-redaction)
    conn.close()


def test_secret_value_scrubbed_even_nested_in_list():
    conn = audit.connect(":memory:")
    audit.audit_event(
        conn, tenant_id="op", actor="op", tool="t", action="commit", result="ok",
        args_redacted={"items": [{"x": "Bearer zzzzzzzzzzzzzzzzzz"}, {"y": "plain"}]},
    )
    blob = _args_blob(conn)
    assert "Bearer zzzzzzzzzzzzzzzzzz" not in blob
    assert "plain" in blob
    conn.close()


@pytest.mark.parametrize("key", ["pwd", "passwd", "passphrase", "credentials"])
def test_broadened_key_names_redacted(key):
    conn = audit.connect(":memory:")
    audit.audit_event(
        conn, tenant_id="op", actor="op", tool="t", action="commit", result="ok",
        args_redacted={key: "SEKRET", "ok": "fine"},
    )
    blob = _args_blob(conn)
    assert "SEKRET" not in blob and "fine" in blob
    conn.close()


def test_ordinary_long_ids_are_not_over_redacted():
    # Conservative: a plain GUID/numeric id or ordinary text must survive — value scrubbing
    # only targets JWT / Bearer|Basic shapes, not arbitrary long strings.
    conn = audit.connect(":memory:")
    audit.audit_event(
        conn, tenant_id="op", actor="op", tool="t", action="commit", result="ok",
        args_redacted={"id": "5f3c9a12-7b44-4e2a-9c01-aabbccddeeff", "name": "Lakefront Homes"},
    )
    blob = _args_blob(conn)
    assert "5f3c9a12-7b44-4e2a-9c01-aabbccddeeff" in blob
    assert "Lakefront Homes" in blob
    conn.close()
