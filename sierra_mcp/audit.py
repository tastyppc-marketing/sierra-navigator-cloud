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
import logging
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
_LEDGER_KEY_ENV = "SIERRA_MCP_LEDGER_KEY"
_ENC_PREFIX = "enc:fernet:v1:"

_LOG = logging.getLogger(__name__)

# Keys whose values must never be persisted into the audit trail. Matched as a
# substring, case-insensitively, against each key name at any nesting depth.
_SECRET_KEY_RE = re.compile(
    r"password|passwd|passphrase|pwd|secret|credential|token|key|authorization|cookie|"
    r"bearer|session|jwt|api_key|auth",
    re.IGNORECASE,
)
_REDACTED = "***"

# Value-level secret detection (#14): scrub a string VALUE that is unambiguously a secret
# REGARDLESS of its key name — a JWT, or an HTTP auth-scheme token. Deliberately narrow
# (specific shapes, not "any long string") so the audit trail keeps ordinary ids/text.
_SECRET_VALUE_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"   # JWT header.payload.sig
    r"|(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}",               # Authorization scheme token
    re.IGNORECASE,
)

_LEDGER_CIPHER_LOCK = threading.Lock()
_LEDGER_CIPHER_CACHE_SET = False
_LEDGER_CIPHER_CACHE_RAW: str | None = None
_LEDGER_CIPHER_CACHE: Any = None


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
    _ledger_cipher()
    if (os.environ.get("AUTHKIT_DOMAIN") or "").strip() and not (
        os.environ.get(_LEDGER_KEY_ENV) or ""
    ).strip():
        _LOG.warning(
            "AUTHKIT_DOMAIN is set but %s is unset; ledger recovery snapshots "
            "will be written in plaintext.",
            _LEDGER_KEY_ENV,
        )
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
    # Value-level scrub: a secret-shaped string is starred even under an innocuous key (#14).
    if isinstance(obj, str) and _SECRET_VALUE_RE.search(obj):
        return _REDACTED
    return obj


def _as_json(value: Any) -> str | None:
    """JSON-encode dicts/lists; pass ``None`` and ``str`` through unchanged.

    ``default=str`` is a safety net so audit logging can never crash the caller
    on an oddball value (e.g. a stray ``datetime``).
    """
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _ledger_cipher() -> Any:
    """Return the configured Fernet/MultiFernet cipher, or ``None`` when unset.

    The ``cryptography`` import is lazy so the default no-key path stays stdlib
    only. The cache is keyed by the raw env-var value so tests and long-lived
    processes can pick up key flips after :func:`_reset_ledger_cipher`.
    """
    global _LEDGER_CIPHER_CACHE_SET, _LEDGER_CIPHER_CACHE_RAW, _LEDGER_CIPHER_CACHE

    raw = os.environ.get(_LEDGER_KEY_ENV, "")
    with _LEDGER_CIPHER_LOCK:
        if _LEDGER_CIPHER_CACHE_SET and raw == _LEDGER_CIPHER_CACHE_RAW:
            return _LEDGER_CIPHER_CACHE

        if not raw.strip():
            cipher = None
        else:
            from cryptography.fernet import Fernet, MultiFernet

            keys = [part.strip() for part in raw.split(",") if part.strip()]
            if not keys:
                raise ValueError(f"{_LEDGER_KEY_ENV} is set but contains no keys")
            fernets = [Fernet(key.encode("ascii")) for key in keys]
            cipher = fernets[0] if len(fernets) == 1 else MultiFernet(fernets)

        _LEDGER_CIPHER_CACHE_SET = True
        _LEDGER_CIPHER_CACHE_RAW = raw
        _LEDGER_CIPHER_CACHE = cipher
        return cipher


def _reset_ledger_cipher() -> None:
    """Clear the ledger cipher cache after tests change ``SIERRA_MCP_LEDGER_KEY``."""
    global _LEDGER_CIPHER_CACHE_SET, _LEDGER_CIPHER_CACHE_RAW, _LEDGER_CIPHER_CACHE

    with _LEDGER_CIPHER_LOCK:
        _LEDGER_CIPHER_CACHE_SET = False
        _LEDGER_CIPHER_CACHE_RAW = None
        _LEDGER_CIPHER_CACHE = None


def _encrypt_snapshot(text: str | None) -> str | None:
    """Encrypt a serialized ledger snapshot when a ledger key is configured."""
    if text is None:
        return None
    cipher = _ledger_cipher()
    if cipher is None:
        return text
    return _ENC_PREFIX + cipher.encrypt(text.encode("utf-8")).decode("ascii")


def _decrypt_snapshot(stored: str | None) -> str | None:
    """Decrypt an encrypted ledger snapshot; pass through legacy plaintext rows."""
    if stored is None or not stored.startswith(_ENC_PREFIX):
        return stored
    cipher = _ledger_cipher()
    if cipher is None:
        raise RuntimeError(
            f"{_LEDGER_KEY_ENV} is required to read encrypted ledger snapshots"
        )
    token = stored[len(_ENC_PREFIX):].encode("ascii")
    return cipher.decrypt(token).decode("utf-8")


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
    When ``SIERRA_MCP_LEDGER_KEY`` is configured, this column is encrypted-at-rest;
    otherwise it remains plaintext for backward-compatible no-key operation.
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
                _encrypt_snapshot(_as_json(payload_snapshot)),
                _bool_int(reversible),
                cleanup_status,
                authorization,
                now_iso(),
            ),
        )
        return int(cur.lastrowid)


def ledger_snapshot(conn: sqlite3.Connection, ledger_id: int) -> Any:
    """Return a ledger row's decrypted ``payload_snapshot``, or ``None`` if absent."""
    row = conn.execute(
        "SELECT payload_snapshot FROM ledger WHERE id = ?", (int(ledger_id),)
    ).fetchone()
    if row is None:
        return None
    stored = _decrypt_snapshot(row["payload_snapshot"])
    if stored is None:
        return None
    return json.loads(stored)


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
