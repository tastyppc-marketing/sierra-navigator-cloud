"""W4-T4 (re-audit New-5 HIGH): the ledger recovery snapshot must be VERBATIM.

The ``payload_snapshot`` written before an IRREVERSIBLE content-page delete is the
sole recovery record. W1-T1 (#14) redacted it, but the broad secret-key regex stars
``metaKeywords`` (matches "key") and ``password`` — destroying recovery fidelity and
dropping a page's access password on restore. The recovery snapshot is stored
verbatim; the immutable ``audit_log`` (before/after/args) stays redacted.

Carry-forward: storing recovery data verbatim re-introduces plaintext secrets in the
local ledger — the proper long-term control is encryption-at-rest for this column,
not redaction (which is lossy and breaks recovery).
"""
import json

from sierra_mcp import audit


def test_recovery_snapshot_preserves_all_fields_verbatim():
    conn = audit.connect(":memory:")
    record = {
        "page": {
            "id": 900,
            "name": "Home",
            "metaKeywords": "homes, utah, lakefront",  # matches "key" -> was redacted
            "password": "s3cret-page-pw",               # matches "password" -> was redacted
            "metaDescription": "Find homes.",
        }
    }
    sink = audit.make_snapshot_sink(conn, tenant_id="op", entity_type="content_page")
    ledger_id = sink(record)

    row = conn.execute(
        "SELECT payload_snapshot FROM ledger WHERE id = ?", (ledger_id,)
    ).fetchone()
    snap = json.loads(row["payload_snapshot"])
    page = snap["page"]
    assert page["metaKeywords"] == "homes, utah, lakefront"  # recovery needs it intact
    assert page["password"] == "s3cret-page-pw"
    assert page["metaDescription"] == "Find homes."
    conn.close()


def test_ledger_record_payload_snapshot_is_verbatim():
    conn = audit.connect(":memory:")
    lid = audit.ledger_record(
        conn, tenant_id="op", entity_type="content_page", action="deleted",
        payload_snapshot={"apiKey": "KEEPME", "nested": {"password": "ALSO-KEEP"}},
    )
    snap = json.loads(
        conn.execute("SELECT payload_snapshot FROM ledger WHERE id = ?", (lid,)).fetchone()[
            "payload_snapshot"
        ]
    )
    assert snap == {"apiKey": "KEEPME", "nested": {"password": "ALSO-KEEP"}}
    conn.close()


def test_audit_log_state_columns_stay_redacted_after_snapshot_change():
    # #14 regression: making the ledger snapshot verbatim must NOT relax audit_log
    # redaction — before/after/args secrets are still scrubbed there.
    conn = audit.connect(":memory:")
    audit.audit_event(
        conn, tenant_id="op", actor="op", tool="t", action="commit", result="ok",
        before_json={"password": "SEKRET_BEFORE"},
        after_json={"api_key": "SEKRET_AFTER"},
        args_redacted={"authorization": "SEKRET_ARGS"},
    )
    row = conn.execute(
        "SELECT before_json, after_json, args_redacted FROM audit_log"
    ).fetchone()
    assert "SEKRET_BEFORE" not in row["before_json"]
    assert "SEKRET_AFTER" not in row["after_json"]
    assert "SEKRET_ARGS" not in row["args_redacted"]
    conn.close()
