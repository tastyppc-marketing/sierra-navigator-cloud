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

import sqlite3

from sierra_mcp import audit
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


def granted_scopes() -> set[str]:
    """Scopes granted to the current operator (MVP: read+write+delete)."""
    return set(_GRANTED_SCOPES)


def actor() -> str:
    """Identity recorded in the audit trail.

    MVP returns the constant operator; Phase 3 will derive it from the request's
    WorkOS auth subject.
    """
    return ACTOR


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
