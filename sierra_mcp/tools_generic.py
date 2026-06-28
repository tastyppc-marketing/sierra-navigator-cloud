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

import re
from typing import Any

from sierra_mcp import audit, context
from sierra_mcp.catalogue import endpoint_paths
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
                      "Deactivate", "Unpublish",
                      # re-audit New-1: state-destroying verbs the read/write allowlists
                      # must never out-rank — esp. "Cancel", which the read verb "Can"
                      # used to swallow via a non-boundary prefix match.
                      "Cancel", "Disable", "Release", "Revoke", "Void", "Expire",
                      "Terminate", "Clean")  # re-audit #3: +Clean
_DESTRUCTIVE_SET = frozenset(_DESTRUCTIVE_VERBS)

# Destructive name-fragments that appear MID-method (not as a leading verb) and are not
# caught by a leading-verb match: "Delete" anywhere (BulkDeleteLeads) and "Deletion"
# (SetClientDeletionStatusForSavedSearches — a soft-delete starting with write-verb
# "Set"; note "Delete" is NOT a substring of "Deletion"). re-audit New-2.
_DESTRUCTIVE_FRAGMENTS = ("Delete", "Deletion")

# Split a CamelCase method into its word tokens, e.g. TestVoiceAndTextReleaseExpiredNumbers
# -> [Test, Voice, And, Text, Release, Expired, Numbers]. Used to catch a destructive verb
# sitting MID-name behind a benign leading verb (re-audit #3 HIGH).
_CAMEL_TOKEN_RE = re.compile(r"[A-Z][a-z0-9]*")

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


def _starts_with_verb(method: str, verbs: tuple[str, ...]) -> bool:
    """True iff ``method`` begins with one of ``verbs`` at a CamelCase TOKEN BOUNDARY —
    the verb is the entire method, or the char right after it is uppercase (a new word).

    Load-bearing fix for re-audit New-1: a plain ``startswith`` let the read verb 'Can'
    swallow the mutating verb 'Cancel' (and 'Set' swallow 'Setting'). Requiring an
    uppercase boundary means 'Can' matches 'CanEditPage' but NOT 'CancelMessage'.
    """
    for v in verbs:
        if method == v:
            return True
        if method.startswith(v) and len(method) > len(v) and method[len(v)].isupper():
            return True
    return False


def _is_destructive(method: str) -> bool:
    """True for any data-destroying method — a destructive name-fragment anywhere
    ('Delete'/'Deletion', catching BulkDeleteLeads and SetClientDeletionStatus*) OR a
    destructive verb appearing as a CamelCase TOKEN ANYWHERE in the method, leading or
    mid-name.

    The token-anywhere check (re-audit #3 HIGH) closes the gap where a destructive verb
    behind a benign leading verb — TestVoiceAndText**Release**ExpiredNumbers,
    ...Manual**Disable**, ...**Clean**NumberReferences — used to classify as a guarded
    'write' and commit through the generic caller with no snapshot / identity lock. Exact
    token equality (not substring) avoids false positives like 'Archived' != 'Archive'.
    """
    # Delete/Deletion fragment anywhere, or a LEADING destructive verb -> destructive.
    if any(frag in method for frag in _DESTRUCTIVE_FRAGMENTS) or _starts_with_verb(
        method, _DESTRUCTIVE_VERBS
    ):
        return True
    # A would-be WRITE (anything not led by a read verb) that carries a destructive verb
    # token MID-name is actually destructive — TestVoiceAndText**Release**ExpiredNumbers.
    # A READ (led by Get/Check/Validate/…) is a query, so a destructive token there is
    # benign (GetLeadsForMerge, CheckAbilityToRemoveAdminUser) and must stay read (#7).
    if not _starts_with_verb(method, _READ_VERBS) and any(
        tok in _DESTRUCTIVE_SET for tok in _CAMEL_TOKEN_RE.findall(method)
    ):
        return True
    return False


def classify(path: str) -> str:
    """Required handling: ``"read"`` / ``"write"`` / ``"refused"`` (default-deny).

    refused = destructive (checked FIRST) OR any unrecognized verb (the default — fail
    closed). read/write are explicit verb allowlists matched at a CamelCase token
    boundary (:func:`_starts_with_verb`), so a short read/write verb can never swallow a
    longer destructive one.
    """
    method = _method_of(path)
    if _is_destructive(method):
        return "refused"
    if _starts_with_verb(method, _READ_VERBS):
        return "read"
    if _starts_with_verb(method, _WRITE_VERBS):
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
    """Record a guard refusal (no Sierra contact) in the append-only audit log.

    Labelled with ``token_subject`` (not ``actor``) so auditing a refusal never itself
    raises ``PermissionError`` when the caller is authenticated-but-not-allowlisted."""
    audit.audit_reject(
        context.get_conn(),
        tenant_id=context.TENANT_ID,
        actor=context.token_subject(),
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
        conn = context.get_conn()
        # Enforce identity (allowlist) + read scope, auditing any denial — the Tier-2 read
        # path must consult the same gate as the Tier-1 reads (New-3) and writes (#8).
        actor = context.authorize(
            conn, tool="sierra_call", action="call", scope="read",
            endpoint=path, args_redacted={"path": path, "body": body},
        )
        try:
            result = context.get_runtime().read(lambda c: c.call(path, body))
        except Exception as e:
            audit.audit_event(
                conn,
                tenant_id=context.TENANT_ID,
                actor=actor,
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
            actor=actor,
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
