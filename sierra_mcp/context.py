"""Per-process operator context: one audit DB, one runtime, one identity.

**Operator MVP.** This layer exposes a single constant tenant (``"operator"``)
with all scopes granted. Phase 3 will scope identity + DB connection + granted
scopes per authenticated tenant; everything here is written tenant-first
(``TENANT_ID`` threaded through every guard/audit call) so that swap is a
non-event.

The audit DB connection and the :class:`~sierra_mcp.runtime.SierraRuntime` are
lazy process singletons — nothing opens a DB file or a session at import time;
they are created on first use by a write/delete tool. Tests call :func:`use` to
pin a ``":memory:"`` connection + a FakeTransport-backed runtime, and
:func:`reset` to drop the singletons afterwards.
"""
from __future__ import annotations

import os
import sqlite3

from fastmcp.server.dependencies import get_access_token

from sierra_mcp import audit
from sierra_mcp.guards import GuardError, require_scope
from sierra_mcp.runtime import SierraRuntime

# Single constant tenant + actor for the operator MVP.
TENANT_ID = "operator"
ACTOR = "operator"

# All scopes granted to the operator. Phase 3 derives this per-tenant from the
# auth subject's grants.
_GRANTED_SCOPES = frozenset({"read", "write", "delete"})

_conn: sqlite3.Connection | None = None
_runtime: SierraRuntime | None = None


def get_conn() -> sqlite3.Connection:
    """Return the process-wide audit/ledger/token DB connection (lazy singleton)."""
    global _conn
    if _conn is None:
        _conn = audit.connect()
    return _conn


def get_runtime() -> SierraRuntime:
    """Return the process-wide :class:`SierraRuntime` (lazy singleton).

    Shared by the read tools AND the write/delete tools so there is exactly one
    SessionBroker (one Sierra login) per process.
    """
    global _runtime
    if _runtime is None:
        _runtime = SierraRuntime()
    return _runtime


# --------------------------------------------------------------------------- #
# authorization — derived from the validated WorkOS token (plan 015, #5)
# --------------------------------------------------------------------------- #

def _subject_allowlist() -> set[str]:
    """Allowed token subjects (email or sub) from ``SIERRA_MCP_SUBJECT_ALLOWLIST``
    (comma-separated). Empty/unset ⇒ allow any *authenticated* subject."""
    raw = os.environ.get("SIERRA_MCP_SUBJECT_ALLOWLIST", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def _require_allowed(claims: dict) -> str:
    """Return the subject (email→sub→'authenticated'); raise ``PermissionError`` if
    an allowlist is configured and neither the email nor sub is on it. Fail-closed."""
    email, sub = claims.get("email"), claims.get("sub")
    subject = email or sub or "authenticated"
    allow = _subject_allowlist()
    if allow and not (allow & ({email, sub, subject} - {None})):
        raise PermissionError(
            f"subject {subject!r} is not in SIERRA_MCP_SUBJECT_ALLOWLIST"
        )
    return subject


def _scopes_from_claims(claims: dict) -> set[str]:
    """Map token claims → granted scopes. WorkOS does not emit custom scopes yet,
    so a valid + allowlisted subject gets the full operator grant. SEAM: when
    ``sierra:read/write/delete`` are issued, map the ``scope`` claim here (one change)."""
    return set(_GRANTED_SCOPES)


def granted_scopes() -> set[str]:
    """Scopes for the current request. Authenticated → allowlist-gated grant;
    no token (auth-disabled loopback dev) → operator full grant (preserves tests)."""
    tok = get_access_token()
    if tok is None:
        return set(_GRANTED_SCOPES)
    claims = tok.claims or {}
    _require_allowed(claims)
    return _scopes_from_claims(claims)


def actor() -> str:
    """Identity recorded in the audit trail (non-repudiation). Authenticated →
    token ``email``/``sub`` (allowlist-gated); no token → the constant operator (dev)."""
    tok = get_access_token()
    if tok is None:
        return ACTOR
    return _require_allowed(tok.claims or {})


def token_subject() -> str:
    """Best-effort caller subject for AUDIT LABELLING ONLY — never raises, never gates.

    Unlike :func:`actor`, this does NOT enforce the allowlist, so a denial can still be
    recorded against the subject that was rejected (non-repudiation). Returns the token
    email/sub, ``"authenticated"`` if a token carries neither, or the operator constant
    when there is no token (dev)."""
    tok = get_access_token()
    if tok is None:
        return ACTOR
    claims = tok.claims or {}
    return claims.get("email") or claims.get("sub") or "authenticated"


def authorize(
    conn,
    *,
    tool: str,
    action: str,
    scope: str,
    endpoint: str | None = None,
    entity_type: str | None = None,
    entity_id=None,
    args_redacted=None,
    confirm_token: str | None = None,
) -> str:
    """Enforce per-identity access at a tool entry and AUDIT any denial before re-raising.

    The single gate every tool (read / write / delete / generic) goes through:
    :func:`granted_scopes` runs the subject allowlist (``PermissionError`` if the subject
    is not allowlisted) and :func:`require_scope` checks the capability (``ScopeError`` if
    missing). On denial we write a ``result="rejected"`` row labelled with
    :func:`token_subject` (so an authenticated-but-unauthorized caller can't probe the
    surface invisibly — #8/New-4) and re-raise. On success we return the enforced
    :func:`actor`. Closes New-3 (reads now consult the allowlist, like writes/deletes)."""
    try:
        require_scope(granted_scopes(), scope)
    except (PermissionError, GuardError) as e:
        audit.audit_reject(
            conn,
            tenant_id=TENANT_ID,
            actor=token_subject(),
            tool=tool,
            action=action,
            scope=scope,
            error=repr(e),
            endpoint=endpoint,
            entity_type=entity_type,
            entity_id=entity_id,
            args_redacted=args_redacted,
            confirm_token=confirm_token,
        )
        raise
    return actor()


# --------------------------------------------------------------------------- #
# test hooks
# --------------------------------------------------------------------------- #

def use(*, conn: sqlite3.Connection | None = None, runtime: SierraRuntime | None = None) -> None:
    """Pin the process singletons (tests inject a ``:memory:`` DB + fake runtime)."""
    global _conn, _runtime
    if conn is not None:
        _conn = conn
    if runtime is not None:
        _runtime = runtime


def reset() -> None:
    """Drop the singletons so the next access re-initialises (test teardown)."""
    global _conn, _runtime
    _conn = None
    _runtime = None
