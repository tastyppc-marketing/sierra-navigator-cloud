"""SQLite-backed audit + guardrail stores for the Sierra MCP write/delete layer.

Three single-file SQLite stores (stdlib ``sqlite3`` only — no third-party deps):

* ``audit_log``      — **immutable**, INSERT-only. UPDATE/DELETE are blocked at
  the database level by ``BEFORE`` triggers, so the trail can't be rewritten
  even by a buggy caller. Every guarded tool call lands here: who, what, when,
  before/after state, and the result.
* ``ledger``         — cleanup tracking + recovery snapshots. Rows are INSERTed
  at create/delete time; only ``cleanup_status`` (+ ``deleted_at``) may change
  afterwards.
* ``confirm_tokens`` — one-time confirm tokens backing the preview->commit
  handshake in :mod:`sierra_mcp.guards`.

Design notes
------------
* **tenant_id everywhere.** Every store fn carries ``tenant_id`` so Phase-3
  multi-tenancy is a non-event; the operator MVP just passes one constant.
* **Time** is ISO-8601 UTC (:func:`now_iso`) — sortable, tz-aware, stdlib-only.
* **Redaction.** ``args_redacted`` is scrubbed of obvious secret keys (recursively)
  *before* it is persisted, so the audit trail can never become a credential
  leak. ``before_json`` / ``after_json`` are entity-state snapshots and are
  stored verbatim (they are the recovery record).

This module never imports :mod:`sierra_mcp.guards` (the dependency runs the other
way), and it makes **no** network/Sierra calls.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

# Default on-disk location when neither an explicit path nor the env override is
# given. ``*.db`` under ``data/`` is gitignored (see .gitignore).
DEFAULT_DB_PATH = "./data/sierra_mcp.db"
_DB_PATH_ENV = "SIERRA_MCP_DB_PATH"

# Keys whose values must never be persisted into the audit trail. Matched as a
# substring, case-insensitively, against each key name at any nesting depth.
_SECRET_KEY_RE = re.compile(
    r"password|secret|token|key|authorization|cookie|bearer|session|jwt|api_key|auth",
    re.IGNORECASE,
)
_REDACTED = "***"


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    """Current instant as a tz-aware ISO-8601 UTC string (sortable)."""
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# concurrency — one shared connection, serialized
# --------------------------------------------------------------------------- #

# The whole process shares ONE sqlite connection (opened check_same_thread=False);
# FastMCP dispatches sync tools on a worker-thread pool, so every statement is
# serialized through this re-entrant lock. WAL + busy_timeout (see connect) handle
# any cross-process contention.
_DB_LOCK = threading.RLock()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Run a compound critical section atomically on the shared connection.

    Holds the process-wide lock for the whole block, opens ``BEGIN IMMEDIATE``,
    and commits on success / rolls back on error. Re-entrant: if a transaction is
    already open on ``conn`` (an outer ``transaction``), this neither re-BEGINs nor
    commits — the outermost block owns the commit.
    """
    with _DB_LOCK:
        began = not conn.in_transaction
        if began:
            conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            if began:
                conn.rollback()
            raise
        else:
            if began:
                conn.commit()


# --------------------------------------------------------------------------- #
# connection + schema
# --------------------------------------------------------------------------- #

def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open (and initialise) the audit/ledger/token database.

    ``db_path`` precedence: explicit arg -> ``SIERRA_MCP_DB_PATH`` env ->
    :data:`DEFAULT_DB_PATH`. ``":memory:"`` is honoured for tests. The parent
    directory of an on-disk path is created if missing. Sets
    ``row_factory = sqlite3.Row`` and runs :func:`init_schema`.
    """
    if db_path is None:
        db_path = (os.environ.get(_DB_PATH_ENV) or "").strip() or DEFAULT_DB_PATH
    if db_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL + a busy timeout so the single shared connection tolerates concurrent
    # access from FastMCP's worker threads (serialized by _DB_LOCK) without
    # SQLITE_BUSY. (No-op/harmless on :memory:.)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the three tables, their indexes, and the append-only triggers.

    Everything is ``IF NOT EXISTS`` so this is safe to call on every connect.
    """
    conn.executescript(
        """
        -- Immutable audit trail. INSERT only; UPDATE/DELETE blocked by triggers.
        CREATE TABLE IF NOT EXISTS audit_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT NOT NULL,
            tenant_id      TEXT NOT NULL,
            actor          TEXT NOT NULL,
            tool           TEXT NOT NULL,
            endpoint       TEXT,
            entity_type    TEXT,
            entity_id      TEXT,
            title_snapshot TEXT,
            action         TEXT NOT NULL,
            scope          TEXT,
            confirm_token  TEXT,
            args_redacted  TEXT,
            before_json    TEXT,
            after_json     TEXT,
            reversible     INTEGER,
            result         TEXT NOT NULL,
            error          TEXT,
            request_id     TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_audit_tenant_ts
            ON audit_log (tenant_id, ts);
        CREATE INDEX IF NOT EXISTS ix_audit_entity
            ON audit_log (entity_type, entity_id);

        -- Append-only enforcement: any UPDATE/DELETE aborts the statement.
        CREATE TRIGGER IF NOT EXISTS audit_no_update
            BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete
            BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;

        -- Cleanup tracking + recovery snapshots. UPDATE allowed for cleanup_status.
        CREATE TABLE IF NOT EXISTS ledger (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id        TEXT NOT NULL,
            entity_type      TEXT NOT NULL,
            entity_id        TEXT,
            title_snapshot   TEXT,
            action           TEXT NOT NULL,
            payload_snapshot TEXT,
            reversible       INTEGER,
            cleanup_status   TEXT,
            authorization    TEXT,
            created_at       TEXT NOT NULL,
            deleted_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_ledger_tenant_type
            ON ledger (tenant_id, entity_type);

        -- One-time confirm tokens (preview -> commit handshake).
        CREATE TABLE IF NOT EXISTS confirm_tokens (
            token          TEXT PRIMARY KEY,
            tenant_id      TEXT NOT NULL,
            tool           TEXT NOT NULL,
            scope_required TEXT NOT NULL,
            payload_hash   TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            expires_at     TEXT NOT NULL,
            used_at        TEXT
        );
        """
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# internal helpers
# --------------------------------------------------------------------------- #

def _redact(obj: Any) -> Any:
    """Recursively star the value of any secret-ish key (by name) in ``obj``.

    Dicts and lists are walked; the key *name* is matched against
    :data:`_SECRET_KEY_RE`. Matching values become ``"***"`` (we star rather than
    drop so the audit record still shows the field was present). Scalars pass
    through untouched. Non-dict/list inputs (e.g. a pre-serialised string) are
    returned as-is.
    """
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = _REDACTED
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _as_json(value: Any) -> str | None:
    """JSON-encode dicts/lists; pass ``None`` and ``str`` through unchanged.

    ``default=str`` is a safety net so audit logging can never crash the caller
    on an oddball value (e.g. a stray ``datetime``).
    """
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _bool_int(value: Any) -> int | None:
    """Coerce a tri-state reversible flag to ``None`` / ``0`` / ``1``."""
    return None if value is None else int(bool(value))


def _str_or_none(value: Any) -> str | None:
    """Stringify a non-``None`` id (``entity_id`` is TEXT in every table)."""
    return None if value is None else str(value)


def _extract_identity(record: Any) -> tuple[Any, Any]:
    """Best-effort ``(entity_id, title)`` from a Sierra record of unknown shape.

    Order: nested ``page.id`` / ``page.name`` (content-page ``GetPage`` shape),
    then top-level ``id`` and ``name`` -> ``searchName`` (saved-search / widget
    shapes). Anything absent degrades to ``None``.
    """
    if not isinstance(record, dict):
        return None, None
    page = record.get("page")
    if isinstance(page, dict):
        return page.get("id"), page.get("name")
    return record.get("id"), (record.get("name") or record.get("searchName"))


# --------------------------------------------------------------------------- #
# audit_log
# --------------------------------------------------------------------------- #

def audit_event(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    actor: str,
    tool: str,
    action: str,
    result: str,
    scope: str | None = None,
    endpoint: str | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    title_snapshot: str | None = None,
    confirm_token: str | None = None,
    args_redacted: Any = None,
    before_json: Any = None,
    after_json: Any = None,
    reversible: Any = None,
    error: str | None = None,
    request_id: str | None = None,
) -> int:
    """Append one immutable row to ``audit_log``; return its ``id``.

    ``args_redacted`` / ``before_json`` / ``after_json`` may be dicts/lists (they
    are JSON-encoded) or pre-serialised strings (stored as-is). ``args_redacted``
    is additionally scrubbed of secret-ish keys before storage.
    """
    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO audit_log (
                ts, tenant_id, actor, tool, endpoint, entity_type, entity_id,
                title_snapshot, action, scope, confirm_token, args_redacted,
                before_json, after_json, reversible, result, error, request_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                now_iso(),
                tenant_id,
                actor,
                tool,
                endpoint,
                entity_type,
                _str_or_none(entity_id),
                title_snapshot,
                action,
                scope,
                confirm_token,
                _as_json(_redact(args_redacted)) if args_redacted is not None else None,
                _as_json(_redact(before_json)),
                _as_json(_redact(after_json)),
                _bool_int(reversible),
                result,
                error,
                request_id,
            ),
        )
        return int(cur.lastrowid)


def audit_reject(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    actor: str,
    tool: str,
    action: str,
    scope: str | None = None,
    error: str | None = None,
    args_redacted: Any = None,
    endpoint: str | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    confirm_token: str | None = None,
) -> int:
    """Append one ``result="rejected"`` audit row for a guard REJECTION.

    Records refusals that happen BEFORE any Sierra contact — bad scope, replayed/
    expired/tampered confirm token, volume-cap trip, locked-destructive refusal —
    so the guardrails can't be probed invisibly. Callers audit, then re-raise.
    """
    return audit_event(
        conn,
        tenant_id=tenant_id,
        actor=actor,
        tool=tool,
        action=action,
        result="rejected",
        scope=scope,
        endpoint=endpoint,
        entity_type=entity_type,
        entity_id=entity_id,
        confirm_token=confirm_token,
        args_redacted=args_redacted,
        error=error,
    )


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #

def ledger_record(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    entity_type: str,
    action: str,
    entity_id: Any = None,
    title_snapshot: str | None = None,
    payload_snapshot: Any = None,
    reversible: Any = None,
    cleanup_status: str | None = None,
    authorization: str | None = None,
) -> int:
    """Append one ``ledger`` row (``created_at=now``); return its ``id``.

    ``payload_snapshot`` is stored **VERBATIM** — it is the sole recovery record taken
    before an irreversible delete, so redacting it (W1-T1's broad key regex stars
    ``metaKeywords``/``password``) would make the entity non-reconstructable (New-5).
    The immutable ``audit_log`` (args/before/after) is still scrubbed; only this
    recovery column is verbatim. CARRY-FORWARD: the long-term control for secrets in
    the snapshot is encryption-at-rest on this column, not lossy redaction.
    """
    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO ledger (
                tenant_id, entity_type, entity_id, title_snapshot, action,
                payload_snapshot, reversible, cleanup_status, authorization, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id,
                entity_type,
                _str_or_none(entity_id),
                title_snapshot,
                action,
                _as_json(payload_snapshot),  # VERBATIM recovery record (see docstring)
                _bool_int(reversible),
                cleanup_status,
                authorization,
                now_iso(),
            ),
        )
        return int(cur.lastrowid)


def ledger_mark_cleanup(
    conn: sqlite3.Connection,
    ledger_id: int,
    cleanup_status: str,
    *,
    deleted: bool = False,
) -> None:
    """Update a ledger row's ``cleanup_status`` (the one mutable column).

    When ``deleted=True`` also stamp ``deleted_at=now`` to record that the
    underlying entity has actually been removed.
    """
    with transaction(conn):
        if deleted:
            conn.execute(
                "UPDATE ledger SET cleanup_status = ?, deleted_at = ? WHERE id = ?",
                (cleanup_status, now_iso(), int(ledger_id)),
            )
        else:
            conn.execute(
                "UPDATE ledger SET cleanup_status = ? WHERE id = ?",
                (cleanup_status, int(ledger_id)),
            )


def make_snapshot_sink(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    entity_type: str,
    authorization: str | None = None,
) -> Callable[[dict], int]:
    """Build the ``snapshot_sink`` that ``sierra_core``'s deletes call pre-delete.

    The returned callable writes the **full** record to ``ledger`` as
    ``action="deleted"`` with ``payload_snapshot`` = the JSON-encoded record and
    ``cleanup_status="pending-delete"``. The snapshot is taken *before* the
    irreversible delete fires, so the row is honestly marked pending; the tool
    layer flips it to ``"deleted"`` (stamping ``deleted_at``) via
    :func:`ledger_mark_cleanup` only after the delete returns successfully. The
    returned ledger id is what the caller passes to that flip. ``entity_id`` /
    ``title`` are pulled defensively via :func:`_extract_identity`.

    The sink **propagates any INSERT failure** — it does not swallow exceptions —
    because a failed snapshot must abort an irreversible delete upstream.
    """

    def sink(record: dict) -> int:
        entity_id, title = _extract_identity(record)
        return ledger_record(
            conn,
            tenant_id=tenant_id,
            entity_type=entity_type,
            action="deleted",
            entity_id=entity_id,
            title_snapshot=title,
            payload_snapshot=record,
            cleanup_status="pending-delete",
            authorization=authorization,
        )

    return sink
