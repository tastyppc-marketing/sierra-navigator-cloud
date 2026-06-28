"""Tier-2 generic caller — the guarded escape hatch over the 642-endpoint catalogue.

``sierra_call`` lets a client invoke a catalogued Sierra endpoint by path, so new
*read/write* back-end ops are reachable with no new code. It is FAIL-CLOSED:

1. **Allowlist** — ``path`` must be one of the 642 catalogued endpoints.
2. **Default-deny classification** — reads execute; a curated set of non-destructive
   write verbs is guarded; EVERY destructive verb AND any unrecognized verb is REFUSED.
3. **No Tier-2 destruction** — entity (content-page / saved-search) deletes route to
   the identity-locked Tier-1 flow (``propose_deletions`` / ``confirm_deletions``);
   other destructive ops need a typed tool. Refusals are audited.
4. **Guarded writes** — writes reuse the SAME dry-run -> confirm handshake, audit
   trail, and volume caps as the Tier-1 write tools.

The body is passed to Sierra **verbatim** — the per-domain string/int ``siteId``
quirk is the caller's (catalogue's) responsibility, not ours.
"""
from __future__ import annotations

from typing import Any

from sierra_mcp import audit, context
from sierra_mcp.catalogue import endpoint_paths
from sierra_mcp.guards import require_scope
from sierra_mcp.tools_write import guarded_write

# FAIL-CLOSED default-deny (plan 015, #1/#2/#6/#7). Reads execute; a curated set of
# non-destructive write verbs is guarded; EVERYTHING else — every destructive verb AND
# any unrecognized verb — is refused. Destruction is categorically impossible via the
# generic caller (the original bug was a *denylist* that missed plural/batch/alternate
# variants; a denylist would repeat that bug class).
_READ_VERBS = ("Get", "List", "Find", "Check", "Load", "Validate", "Search",
               "Count", "Is", "Can")
_WRITE_VERBS = ("Add", "Update", "Save", "Set", "Create", "Assign", "Unassign",
                "Reorder", "Apply", "Edit", "Change", "Enable", "Send", "Import",
                "Test", "Copy", "Claim", "Accept", "Complete", "Activate", "Pause",
                "Resume", "Hide")  # reviewed, non-destructive
_DESTRUCTIVE_VERBS = ("Delete", "Remove", "Merge", "Bulk", "Purge", "Clear", "Archive",
                      "Trash", "Discard", "Duplicate", "Move", "Replace", "Reset",
                      "Deactivate", "Unpublish")

# Entity deletes that DO have a Tier-1 identity-locked flow — route the caller there.
_TIER1_DELETE_HINTS = ("ContentPage", "SavedSearch")

_REFUSE_ROUTE_MSG = (
    "refused: destructive op — use propose_deletions/confirm_deletions "
    "(identity-locked) instead of raw sierra_call"
)
_REFUSE_UNTYPED_MSG = (
    "refused: destructive/unrecognized op {method!r} is not available via the generic "
    "caller; it needs a typed identity-locked + snapshotting tool"
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


def _is_destructive(method: str) -> bool:
    """True for any data-destroying verb — 'Delete' anywhere (BulkDeleteLeads,
    DeleteContentPages) or a leading destructive verb (Remove/Merge/Purge/…)."""
    return "Delete" in method or method.startswith(_DESTRUCTIVE_VERBS)


def classify(path: str) -> str:
    """Required handling: ``"read"`` / ``"write"`` / ``"refused"`` (default-deny).

    refused = any destructive verb (checked FIRST) OR any unrecognized verb (the
    default — fail closed). read and write are explicit verb allowlists.
    """
    method = _method_of(path)
    if _is_destructive(method):
        return "refused"
    if method.startswith(_READ_VERBS):
        return "read"
    if method.startswith(_WRITE_VERBS):
        return "write"
    return "refused"


def _refusal_message(path: str) -> str:
    """Route content-page / saved-search destructive ops to the Tier-1 identity-locked
    flow; everything else needs a typed tool."""
    method = _method_of(path)
    if _is_destructive(method) and any(h in path or h in method for h in _TIER1_DELETE_HINTS):
        return _REFUSE_ROUTE_MSG
    return _REFUSE_UNTYPED_MSG.format(method=method)


def _audit_reject(path: str, scope: str, error: str) -> None:
    """Record a guard refusal (no Sierra contact) in the append-only audit log."""
    audit.audit_reject(
        context.get_conn(),
        tenant_id=context.TENANT_ID,
        actor=context.actor(),
        tool="sierra_call",
        action="call",
        scope=scope,
        endpoint=path,
        error=error,
        args_redacted={"path": path},
    )


def sierra_call(path: str, body: dict | None = None, confirm_token: str | None = None) -> dict:
    """Invoke a catalogued Sierra endpoint by path (Tier-2 generic caller).

    Reads execute immediately; writes/deletes use the two-step dry-run -> confirm
    handshake (call once without ``confirm_token`` to preview + mint a token, then
    again with it to commit). Raises ``ValueError`` for an un-catalogued path or a
    locked-destructive op.
    """
    if path not in _allowlist():
        msg = f"unknown endpoint path {path!r}: not in the 642-endpoint catalogue allowlist"
        _audit_reject(path, scope="unknown", error=msg)
        raise ValueError(msg)

    kind = classify(path)
    body = body or {}

    if kind == "refused":
        msg = _refusal_message(path)
        _audit_reject(
            path,
            scope="delete" if _is_destructive(_method_of(path)) else "write",
            error=msg,
        )
        raise ValueError(msg)

    if kind == "read":
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

    # write: reuse the Tier-1 guarded-write machine (preview->confirm, audit, caps).
    # The commit goes through the allow_write gate via client.call(..., write=True).
    return guarded_write(
        tool=f"sierra_call:{path}",
        scope="write",
        payload={"path": path, "body": body},
        kind="write",
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(lambda c: c.call(path, body, write=True)),
    )
