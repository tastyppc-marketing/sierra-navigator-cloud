"""Tier-2 generic caller — the guarded escape hatch over the 642-endpoint catalogue.

``sierra_call`` lets a client invoke ANY catalogued Sierra endpoint by path, so
new back-end ops are reachable with no new code. It is fenced on four sides:

1. **Allowlist** — ``path`` must be one of the 642 catalogued endpoints.
2. **Classification** — the method segment decides the scope: read / write / delete.
3. **Locked-destructive refusal** — entity deletes and ``Duplicate*`` ops are
   refused here; they must go through the identity-locked Tier-1 flow
   (``propose_deletions`` / ``confirm_deletions``).
4. **Guarded mutation** — write/delete calls reuse the SAME dry-run -> confirm
   handshake, audit trail, and volume caps as the Tier-1 write tools.

The body is passed to Sierra **verbatim** — the per-domain string/int ``siteId``
quirk is the caller's (catalogue's) responsibility, not ours.
"""
from __future__ import annotations

from typing import Any

from sierra_mcp import audit, context
from sierra_mcp.catalogue import endpoint_paths
from sierra_mcp.guards import require_scope
from sierra_mcp.tools_write import guarded_write

# Method-segment prefixes that mark a READ (everything else non-delete is a write).
_READ_PREFIXES = ("Get", "List", "Find", "Check", "Load", "Validate", "Search", "Count")

# Entity deletes that bypass the identity lock if raw-posted — refuse them here.
_LOCKED_DESTRUCTIVE_PATHS = frozenset({
    "/content-pages.aspx/DeleteContentPage",
    "/saved-searches.aspx/DeleteSavedSearch",
})

_LOCKED_REFUSAL_MSG = (
    "refused: use propose_deletions/confirm_deletions (identity-locked) "
    "instead of raw sierra_call"
)

_allowlist_cache: set[str] | None = None


def _allowlist() -> set[str]:
    """The 642 catalogued endpoint paths (cached after first load)."""
    global _allowlist_cache
    if _allowlist_cache is None:
        _allowlist_cache = set(endpoint_paths())
    return _allowlist_cache


def _method_of(path: str) -> str:
    """The trailing method segment, e.g. ``/content-pages.aspx/GetFilters`` -> ``GetFilters``."""
    return path.rsplit("/", 1)[-1]


def classify(path: str) -> str:
    """Classify a path's required scope: ``"read"`` / ``"write"`` / ``"delete"``.

    read   -> method starts with Get/List/Find/Check/Load/Validate/Search/Count
    delete -> method starts with Delete
    write  -> everything else (Add/Update/Save/Remove/Set/Create/Duplicate/…)
    """
    method = _method_of(path)
    if method.startswith(_READ_PREFIXES):
        return "read"
    if method.startswith("Delete"):
        return "delete"
    return "write"


def _is_locked_destructive(path: str) -> bool:
    """True for entity deletes / ``Duplicate*`` ops that must use the Tier-1 flow."""
    return path in _LOCKED_DESTRUCTIVE_PATHS or _method_of(path).startswith("Duplicate")


def sierra_call(path: str, body: dict | None = None, confirm_token: str | None = None) -> dict:
    """Invoke a catalogued Sierra endpoint by path (Tier-2 generic caller).

    Reads execute immediately; writes/deletes use the two-step dry-run -> confirm
    handshake (call once without ``confirm_token`` to preview + mint a token, then
    again with it to commit). Raises ``ValueError`` for an un-catalogued path or a
    locked-destructive op.
    """
    if path not in _allowlist():
        raise ValueError(
            f"unknown endpoint path {path!r}: not in the 642-endpoint catalogue allowlist"
        )
    if _is_locked_destructive(path):
        raise ValueError(_LOCKED_REFUSAL_MSG)

    scope = classify(path)
    body = body or {}

    if scope == "read":
        require_scope(context.granted_scopes(), "read")
        conn = context.get_conn()
        try:
            result = context.get_runtime().read(lambda c: c.call(path, body))
        except Exception as e:
            audit.audit_event(
                conn,
                tenant_id=context.TENANT_ID,
                actor=context.actor(),
                tool="sierra_call",
                action="call",
                endpoint=path,
                scope="read",
                result="error",
                args_redacted={"path": path, "body": body},
                error=repr(e),
            )
            raise
        audit.audit_event(
            conn,
            tenant_id=context.TENANT_ID,
            actor=context.actor(),
            tool="sierra_call",
            action="call",
            endpoint=path,
            scope="read",
            result="ok",
            args_redacted={"path": path, "body": body},
        )
        return {"mode": "called", "path": path, "result": result}

    # write or delete: reuse the Tier-1 guarded-write machine (preview->confirm,
    # audit, volume caps). The commit goes through the allow_write gate via
    # client.call(..., write=True).
    return guarded_write(
        tool=f"sierra_call:{path}",
        scope=scope,
        payload={"path": path, "body": body},
        kind=scope,  # "write" or "delete" -> the matching VolumeTracker counter
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(lambda c: c.call(path, body, write=True)),
    )
