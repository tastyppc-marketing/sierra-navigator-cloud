"""Server-enforced write/delete guardrails: confirm tokens, scopes, volume caps.

This is the safety machine the (next-task) write/delete MCP tools sit behind.
None of it touches Sierra or the network — it is pure local policy over the
SQLite stores in :mod:`sierra_mcp.audit`.

Three guardrails:

* **Confirm tokens** — a two-step preview->commit handshake. A write/delete tool
  first *mints* a token bound to ``(tenant, tool, payload-hash)`` with a short
  TTL; the caller must then *redeem* that exact token, with the exact same
  payload, to commit. One-time use, hash-pinned (so the model can't swap the
  payload between preview and commit), tenant- and tool-scoped, and redeemed
  atomically (no double-spend).
* **Scopes** — a coarse capability gate (``read`` / ``write`` / ``delete``).
* **Volume caps** — a per-call cap on bulk deletes plus per-``(tenant, kind)``
  in-process session counters bounding how much one process can write/delete.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from sierra_mcp.audit import now_iso  # noqa: F401  (re-exported for callers' convenience)

# Recognised scopes (coarse capability gate).
SCOPES = ("read", "write", "delete")


# --------------------------------------------------------------------------- #
# exceptions
# --------------------------------------------------------------------------- #

class GuardError(Exception):
    """Base for every guardrail failure (callers can catch one type)."""


class ConfirmTokenError(GuardError):
    """A confirm token was missing, reused, expired, or did not match."""


class ScopeError(GuardError):
    """The caller's granted scopes do not include the required scope."""


class VolumeCapError(GuardError):
    """A per-call or per-session volume cap would be exceeded."""


# --------------------------------------------------------------------------- #
# env helpers
# --------------------------------------------------------------------------- #

def _env_int(name: str, default: int) -> int:
    """Read a non-negative int from env, falling back to ``default``.

    Empty/unset/non-numeric values fall back rather than raise, so a malformed
    operator env never crashes the server at import time.
    """
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# confirm-token machine
# --------------------------------------------------------------------------- #

def canonical_hash(payload: dict) -> str:
    """Stable SHA-256 of ``payload`` (key-order independent, compact separators).

    Used to pin a confirm token to the exact payload it previewed, so a commit
    with a mutated payload is rejected.
    """
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def mint_token(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    tool: str,
    scope_required: str,
    payload: dict,
    ttl_seconds: int = 120,
    prefix: str = "ct",
) -> dict:
    """Mint a one-time confirm token bound to ``(tenant, tool, payload-hash)``.

    Returns ``{"confirm_token", "expires_at", "ttl_seconds"}``. ``now`` is read
    once so ``created_at`` and the expiry base can't drift.
    """
    token = f"{prefix}_{uuid4().hex}"
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    conn.execute(
        """
        INSERT INTO confirm_tokens (
            token, tenant_id, tool, scope_required, payload_hash,
            created_at, expires_at, used_at
        ) VALUES (?,?,?,?,?,?,?,NULL)
        """,
        (
            token,
            tenant_id,
            tool,
            scope_required,
            canonical_hash(payload),
            now.isoformat(),
            expires.isoformat(),
        ),
    )
    conn.commit()
    return {
        "confirm_token": token,
        "expires_at": expires.isoformat(),
        "ttl_seconds": ttl_seconds,
    }


def redeem_token(
    conn: sqlite3.Connection,
    token: str,
    *,
    tenant_id: str,
    tool: str,
    payload: dict,
) -> None:
    """Validate and atomically spend a confirm token, or raise ``ConfirmTokenError``.

    Checks run in this order (each with a specific message): token exists ->
    not already used -> not expired -> tenant matches -> tool matches -> payload
    hash matches. The whole validate-and-spend runs inside a single
    ``BEGIN IMMEDIATE`` transaction; the spend itself is a conditional
    ``UPDATE ... WHERE used_at IS NULL`` whose ``rowcount`` is asserted, so even
    a concurrent redeem of the same token can spend it only once.
    """
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT tenant_id, tool, payload_hash, expires_at, used_at "
            "FROM confirm_tokens WHERE token = ?",
            (token,),
        ).fetchone()

        if row is None:
            raise ConfirmTokenError(f"unknown confirm token: {token!r}")
        if row["used_at"] is not None:
            raise ConfirmTokenError("confirm token already used (one-time use)")
        if datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"]):
            raise ConfirmTokenError("confirm token expired")
        if row["tenant_id"] != tenant_id:
            raise ConfirmTokenError("confirm token tenant mismatch")
        if row["tool"] != tool:
            raise ConfirmTokenError("confirm token tool mismatch")
        if canonical_hash(payload) != row["payload_hash"]:
            raise ConfirmTokenError("confirm token payload mismatch")

        spent = conn.execute(
            "UPDATE confirm_tokens SET used_at = ? "
            "WHERE token = ? AND used_at IS NULL",
            (now_iso(), token),
        )
        if spent.rowcount != 1:
            # Lost a race: another redeemer spent it between our check and here.
            raise ConfirmTokenError("confirm token already used (one-time use)")
    except BaseException:
        if began:
            conn.rollback()
        raise
    if began:
        conn.commit()


# --------------------------------------------------------------------------- #
# scopes
# --------------------------------------------------------------------------- #

def require_scope(granted: "set[str] | list[str]", required: str) -> None:
    """Raise :class:`ScopeError` unless ``required`` is in ``granted``."""
    grantset = set(granted or ())
    if required not in grantset:
        raise ScopeError(
            f"missing required scope {required!r} "
            f"(granted: {sorted(grantset)})"
        )


# --------------------------------------------------------------------------- #
# volume caps
# --------------------------------------------------------------------------- #

def enforce_delete_call_cap(ids: list, *, cap: int | None = None) -> None:
    """Raise :class:`VolumeCapError` if ``len(ids)`` exceeds the per-call cap.

    ``cap`` falls back to env ``SIERRA_MCP_DELETE_CALL_CAP`` (default 10).
    """
    if cap is None:
        cap = _env_int("SIERRA_MCP_DELETE_CALL_CAP", 10)
    n = len(ids)
    if n > cap:
        raise VolumeCapError(f"delete call cap exceeded: {n} ids > cap {cap}")


class VolumeTracker:
    """Per-``(tenant, kind)`` write/delete counters for the process lifetime.

    In-memory only (no persistence): a bound on how much a single running
    server process can mutate before a human restarts/re-confirms. ``kind`` is
    ``"write"`` or ``"delete"``; tenants are counted independently.
    """

    def __init__(self, write_cap: int | None = None, delete_cap: int | None = None):
        self.write_cap = (
            _env_int("SIERRA_MCP_WRITE_SESSION_CAP", 50)
            if write_cap is None
            else write_cap
        )
        self.delete_cap = (
            _env_int("SIERRA_MCP_DELETE_SESSION_CAP", 20)
            if delete_cap is None
            else delete_cap
        )
        self._counts: dict[tuple[str, str], int] = {}

    def _cap_for(self, kind: str) -> int:
        if kind == "write":
            return self.write_cap
        if kind == "delete":
            return self.delete_cap
        raise ValueError(f"unknown volume kind {kind!r} (expected 'write' or 'delete')")

    def check_and_reserve(self, tenant_id: str, kind: str, n: int = 1) -> None:
        """Reserve ``n`` of ``kind`` for ``tenant_id`` or raise ``VolumeCapError``.

        On success the counter is incremented; on failure nothing changes.
        """
        cap = self._cap_for(kind)
        key = (tenant_id, kind)
        current = self._counts.get(key, 0)
        if current + n > cap:
            raise VolumeCapError(
                f"{kind} session cap exceeded for tenant {tenant_id!r}: "
                f"{current}+{n} > cap {cap}"
            )
        self._counts[key] = current + n

    def current(self, tenant_id: str, kind: str) -> int:
        """Current reserved count for ``(tenant_id, kind)`` (0 if none)."""
        return self._counts.get((tenant_id, kind), 0)

    def reset(self) -> None:
        """Clear all counters (test hook / manual session reset)."""
        self._counts.clear()


# Process-wide singleton the tools use; tests call TRACKER.reset() between cases.
TRACKER = VolumeTracker()
