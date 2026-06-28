"""Guarded write + identity-locked delete tool logic (FastMCP-independent).

Every mutation is a two-step **preview -> confirm** handshake over a hash-pinned,
one-time confirm token:

* **dry-run** (no ``confirm_token``): mint a token bound to the exact payload,
  audit a ``preview`` row, and return the token. **Nothing** is sent to Sierra.
* **commit** (``confirm_token`` supplied): redeem the token (rejects reuse /
  expiry / a mutated payload / wrong tool/tenant), reserve volume, run the real
  ``sierra_core`` call via ``runtime.write``/``runtime.delete``, and audit the
  outcome.

Deletes add an identity lock: the caller must echo each record's stored title
back as ``expected_title``; ``sierra_core`` re-fetches, verifies the title, takes
a recovery snapshot to the ledger, and only then deletes. A title mismatch aborts
that row (no snapshot, no delete) without failing the rest of the batch.

``server.py`` wraps these as MCP tools; identity + DB + runtime come from
:mod:`sierra_mcp.context` (tests inject a ``:memory:`` DB + a FakeTransport
runtime via ``context.use``).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable

from sierra_core.errors import IdentityLockError
from sierra_mcp import audit, context
from sierra_mcp.guards import (
    TRACKER,
    GuardError,
    enforce_delete_call_cap,
    mint_token,
    redeem_token,
)

_DELETE_ENTITY_TYPES = ("content_page", "saved_search")


def _warnings_for(tool: str) -> list[str]:
    """Human-facing cautions surfaced in a dry-run preview."""
    warnings: list[str] = []
    if "remove" in tool:
        warnings.append(
            "Removes an existing label/association. Verify the ids before confirming."
        )
    return warnings


def _client_error(e: Exception) -> str:
    """A sanitized error string safe to RETURN to the MCP client (#17).

    The verbatim ``repr(e)`` can echo upstream Sierra business-rule text, a stored title,
    or HTTP internals, so the client-facing per-row ``error`` is just the exception class
    name; the full ``repr(e)`` is preserved only in the immutable audit DB.
    """
    return type(e).__name__


@contextmanager
def _audit_guard_rejections(
    *,
    conn,
    tenant: str,
    actor: str,
    tool: str,
    action: str,
    scope: str,
    args_redacted: Any,
    confirm_token: str | None = None,
    entity_type: str | None = None,
):
    """Audit (``result="rejected"``) and re-raise any guardrail failure in the block.

    Wraps the scope / confirm-token / volume-cap guard calls so a refusal that
    happens BEFORE any Sierra contact still lands an immutable audit row (#8) — the
    guardrails can't be probed invisibly. Only :class:`GuardError` is intercepted;
    a commit/network failure keeps its own ``result="error"`` audit downstream, and
    a per-row :class:`IdentityLockError` ABORT stays its own ``result="aborted"``
    outcome (neither is a guard rejection).
    """
    try:
        yield
    except GuardError as e:
        audit.audit_reject(
            conn,
            tenant_id=tenant,
            actor=actor,
            tool=tool,
            action=action,
            scope=scope,
            args_redacted=args_redacted,
            confirm_token=confirm_token,
            entity_type=entity_type,
            error=repr(e),
        )
        raise


# --------------------------------------------------------------------------- #
# guarded write machine
# --------------------------------------------------------------------------- #

def guarded_write(
    *,
    tool: str,
    scope: str,
    payload: dict,
    commit: Callable[[], Any],
    confirm_token: str | None = None,
    kind: str = "write",
) -> dict:
    """Run one guarded write as a dry-run (mint) or a commit (redeem + run).

    ``commit`` is a zero-arg callable that performs the real Sierra mutation
    (usually ``lambda: context.get_runtime().write(lambda c: c.<method>(...))``).
    Returns a ``{"mode": "dry_run", ...}`` or ``{"mode": "committed", ...}`` dict.
    Guard rejections (``ScopeError`` / ``ConfirmTokenError`` / ``VolumeCapError``)
    and commit failures propagate; commit failures are audited first.
    """
    conn = context.get_conn()
    tenant = context.TENANT_ID

    # Enforce identity (subject allowlist) + scope at the entry, auditing any denial
    # (PermissionError / ScopeError) before re-raising. "preview" vs "commit" labels which
    # path was refused; authorize returns the enforced actor for the success rows.
    reject_action = "commit" if confirm_token is not None else "preview"
    actor = context.authorize(
        conn, tool=tool, action=reject_action, scope=scope,
        args_redacted=payload, confirm_token=confirm_token,
    )

    if confirm_token is None:
        minted = mint_token(
            conn, tenant_id=tenant, tool=tool, scope_required=scope, payload=payload
        )
        audit.audit_event(
            conn,
            tenant_id=tenant,
            actor=actor,
            tool=tool,
            action="preview",
            result="ok",
            scope=scope,
            args_redacted=payload,
            confirm_token=minted["confirm_token"],
        )
        return {
            "mode": "dry_run",
            "tool": tool,
            "preview": payload,
            "confirm_token": minted["confirm_token"],
            "expires_at": minted["expires_at"],
            "warnings": _warnings_for(tool),
        }

    # commit: redeem (raises on mismatch/expiry/reuse) -> reserve -> run -> audit.
    # A token/cap refusal here is audited (rejected) and re-raised before any call.
    with _audit_guard_rejections(
        conn=conn, tenant=tenant, actor=actor, tool=tool, action="commit",
        scope=scope, args_redacted=payload, confirm_token=confirm_token,
    ):
        redeem_token(conn, confirm_token, tenant_id=tenant, tool=tool, payload=payload)
        TRACKER.check_and_reserve(tenant, kind)
    try:
        result = commit()
    except Exception as e:
        audit.audit_event(
            conn,
            tenant_id=tenant,
            actor=actor,
            tool=tool,
            action="commit",
            result="error",
            scope=scope,
            args_redacted=payload,
            confirm_token=confirm_token,
            error=repr(e),
        )
        raise
    audit.audit_event(
        conn,
        tenant_id=tenant,
        actor=actor,
        tool=tool,
        action="commit",
        result="ok",
        scope=scope,
        args_redacted=payload,
        confirm_token=confirm_token,
        after_json={"result": result},
    )
    return {"mode": "committed", "tool": tool, "result": result}


# --------------------------------------------------------------------------- #
# simple guarded write tools (scope = "write")
# --------------------------------------------------------------------------- #

def create_content_label(name: str, page_id: int = -1, confirm_token: str | None = None) -> dict:
    payload = {"name": name, "page_id": page_id}
    return guarded_write(
        tool="create_content_label",
        scope="write",
        payload=payload,
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(
            lambda c: c.add_content_label(name, page_id=page_id)
        ),
    )


def update_content_label(content_label_id: int, name: str, confirm_token: str | None = None) -> dict:
    payload = {"content_label_id": content_label_id, "name": name}
    return guarded_write(
        tool="update_content_label",
        scope="write",
        payload=payload,
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(
            lambda c: c.update_content_label(content_label_id, name)
        ),
    )


def remove_content_label(content_label_id: int, confirm_token: str | None = None) -> dict:
    payload = {"content_label_id": content_label_id}
    return guarded_write(
        tool="remove_content_label",
        scope="write",
        payload=payload,
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(
            lambda c: c.remove_content_label(content_label_id)
        ),
    )


def add_page_content_label_link(
    page_id: int, content_label_id: int, confirm_token: str | None = None
) -> dict:
    payload = {"page_id": page_id, "content_label_id": content_label_id}
    return guarded_write(
        tool="add_page_content_label_link",
        scope="write",
        payload=payload,
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(
            lambda c: c.add_page_content_label_link(page_id, content_label_id)
        ),
    )


def remove_page_content_label_link(
    page_id: int, content_label_id: int, confirm_token: str | None = None
) -> dict:
    payload = {"page_id": page_id, "content_label_id": content_label_id}
    return guarded_write(
        tool="remove_page_content_label_link",
        scope="write",
        payload=payload,
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(
            lambda c: c.remove_page_content_label_link(page_id, content_label_id)
        ),
    )


def update_page_component_title(
    component_link_id: int, title: str, confirm_token: str | None = None
) -> dict:
    payload = {"component_link_id": component_link_id, "title": title}
    return guarded_write(
        tool="update_page_component_title",
        scope="write",
        payload=payload,
        confirm_token=confirm_token,
        commit=lambda: context.get_runtime().write(
            lambda c: c.update_page_component_title(component_link_id, title)
        ),
    )


# --------------------------------------------------------------------------- #
# identity-locked delete flow (scope = "delete")
# --------------------------------------------------------------------------- #

def _fetch_candidate(runtime, entity_type: str, entity_id: Any) -> tuple[str | None, bool]:
    """Live-read one entity and return ``(stored_title, reversible)``.

    content_page -> ``get_page().page.name``, IRREVERSIBLE (hard delete).
    saved_search -> ``get_saved_search().searchName|name``, reversible (soft delete).
    Raises if the read fails (caller records the id as a fetch error).
    """
    if entity_type == "content_page":
        rec = runtime.read(lambda c: c.get_page(entity_id))
        page = rec.get("page") if isinstance(rec, dict) else None
        title = page.get("name") if isinstance(page, dict) else None
        return title, False
    rec = runtime.read(lambda c: c.get_saved_search(entity_id))
    title = None
    if isinstance(rec, dict):
        title = rec.get("searchName") or rec.get("name")
    return title, True


def propose_deletions(entity_type: str, ids: list, confirm_token: str | None = None) -> dict:
    """Preview a batch delete: live-fetch each record, mint a delete token.

    ``confirm_token`` is accepted for signature symmetry with the write tools but
    is unused — proposing always previews; the commit happens in
    :func:`confirm_deletions`. Sends NOTHING destructive.
    """
    if entity_type not in _DELETE_ENTITY_TYPES:
        raise ValueError(
            f"unknown entity_type {entity_type!r}; expected one of {_DELETE_ENTITY_TYPES}"
        )
    conn = context.get_conn()
    runtime = context.get_runtime()
    tenant = context.TENANT_ID

    # Enforce identity + delete scope (audited on denial), then the per-call cap (also
    # audited) — both before any live fetch; proposing must not be silently probeable.
    actor = context.authorize(
        conn, tool="propose_deletions", action="propose", scope="delete",
        args_redacted={"entity_type": entity_type, "ids": list(ids)},
        entity_type=entity_type,
    )
    with _audit_guard_rejections(
        conn=conn, tenant=tenant, actor=actor, tool="propose_deletions",
        action="propose", scope="delete",
        args_redacted={"entity_type": entity_type, "ids": list(ids)},
        entity_type=entity_type,
    ):
        enforce_delete_call_cap(ids)

    candidates: list[dict] = []
    deletable_ids: list = []
    for entity_id in ids:
        try:
            stored_title, reversible = _fetch_candidate(runtime, entity_type, entity_id)
        except Exception as e:  # fetch failed -> excluded from the deletable set
            candidates.append({"id": entity_id, "error": _client_error(e)})
            continue
        candidates.append(
            {
                "entity_type": entity_type,
                "id": entity_id,
                "stored_title": stored_title,
                "reversible": reversible,
            }
        )
        deletable_ids.append(entity_id)

    payload = {"entity_type": entity_type, "ids": sorted(deletable_ids)}
    minted = mint_token(
        conn,
        tenant_id=tenant,
        tool="confirm_deletions",
        scope_required="delete",
        payload=payload,
        prefix="dt",
    )
    audit.audit_event(
        conn,
        tenant_id=tenant,
        actor=actor,
        tool="propose_deletions",
        action="propose",
        result="ok",
        scope="delete",
        entity_type=entity_type,
        args_redacted={"entity_type": entity_type, "ids": list(ids)},
        confirm_token=minted["confirm_token"],
    )
    return {
        "mode": "dry_run",
        "candidates": candidates,
        "confirm_token": minted["confirm_token"],
        "expires_at": minted["expires_at"],
        "note": "echo each stored_title back as expected_title to confirm",
    }


def _capturing_sink(conn, tenant: str, entity_type: str) -> tuple[Callable[[dict], int], dict]:
    """A snapshot sink that also captures the ledger id it returns.

    ``sierra_core`` calls ``snapshot_sink(record)`` (pre-delete) and ignores the
    return value, so we wrap :func:`audit.make_snapshot_sink` to stash the new
    ledger row id where the tool can read it to flip ``cleanup_status`` afterwards.
    """
    base = audit.make_snapshot_sink(conn, tenant_id=tenant, entity_type=entity_type)
    captured: dict = {}

    def sink(record: dict) -> int:
        ledger_id = base(record)
        captured["ledger_id"] = ledger_id
        return ledger_id

    return sink, captured


def confirm_deletions(confirm_token: str, entity_type: str, confirmations: list) -> dict:
    """Commit a previously-proposed delete batch, identity-locked per row.

    ``confirmations`` is ``[{"id":..., "expected_title":...}, ...]``. The confirmed
    id-set must EXACTLY equal the proposed set (the token is hash-pinned to
    ``{"entity_type", "ids": sorted(...)}``), so adding/removing an id is rejected
    at redeem time. Each row is then deleted via the matching identity-locked
    ``sierra_core`` method; a title mismatch aborts only that row.
    """
    if entity_type not in _DELETE_ENTITY_TYPES:
        raise ValueError(
            f"unknown entity_type {entity_type!r}; expected one of {_DELETE_ENTITY_TYPES}"
        )
    conn = context.get_conn()
    runtime = context.get_runtime()
    tenant = context.TENANT_ID

    ids = [c["id"] for c in confirmations]
    payload = {"entity_type": entity_type, "ids": sorted(ids)}
    # identity + delete scope (authorize) -> token-set integrity (hash-pinned) -> volume
    # cap. A refusal at any of these is audited (rejected) and re-raised before any
    # identity-locked delete fires.
    actor = context.authorize(
        conn, tool="confirm_deletions", action="delete", scope="delete",
        args_redacted=payload, confirm_token=confirm_token, entity_type=entity_type,
    )
    with _audit_guard_rejections(
        conn=conn, tenant=tenant, actor=actor, tool="confirm_deletions",
        action="delete", scope="delete", args_redacted=payload,
        confirm_token=confirm_token, entity_type=entity_type,
    ):
        redeem_token(conn, confirm_token, tenant_id=tenant, tool="confirm_deletions", payload=payload)
        TRACKER.check_and_reserve(tenant, "delete", n=len(confirmations))

    results: list[dict] = []
    for conf in confirmations:
        entity_id = conf["id"]
        expected_title = conf.get("expected_title")
        sink, captured = _capturing_sink(conn, tenant, entity_type)
        try:
            if entity_type == "content_page":
                res = runtime.delete(
                    lambda c, i=entity_id, t=expected_title, s=sink: c.delete_content_page(
                        i, expected_title=t, snapshot_sink=s
                    )
                )
            else:
                res = runtime.delete(
                    lambda c, i=entity_id, t=expected_title, s=sink: c.delete_saved_search(
                        i, expected_title=t, snapshot_sink=s
                    )
                )
        except IdentityLockError as e:
            # assert_identity runs BEFORE the snapshot in sierra_core, so an abort
            # means NO snapshot was taken and NO delete fired — just record it.
            audit.audit_event(
                conn,
                tenant_id=tenant,
                actor=actor,
                tool="confirm_deletions",
                action="delete",
                result="aborted",
                scope="delete",
                entity_type=entity_type,
                entity_id=entity_id,
                title_snapshot=expected_title,
                confirm_token=confirm_token,
                error=repr(e),
            )
            results.append(
                {"id": entity_id, "deleted": False, "identity": "ABORTED", "error": _client_error(e)}
            )
            continue
        except Exception as e:
            audit.audit_event(
                conn,
                tenant_id=tenant,
                actor=actor,
                tool="confirm_deletions",
                action="delete",
                result="error",
                scope="delete",
                entity_type=entity_type,
                entity_id=entity_id,
                title_snapshot=expected_title,
                confirm_token=confirm_token,
                error=repr(e),
            )
            results.append(
                {"id": entity_id, "deleted": False, "identity": "ERROR", "error": _client_error(e)}
            )
            continue

        # success: flip the pre-delete snapshot row to "deleted".
        reversible = bool(res.get("reversible")) if isinstance(res, dict) else (entity_type == "saved_search")
        if "ledger_id" in captured:
            audit.ledger_mark_cleanup(conn, captured["ledger_id"], "deleted", deleted=True)
        audit.audit_event(
            conn,
            tenant_id=tenant,
            actor=actor,
            tool="confirm_deletions",
            action="delete",
            result="ok",
            scope="delete",
            entity_type=entity_type,
            entity_id=entity_id,
            title_snapshot=expected_title,
            confirm_token=confirm_token,
            reversible=reversible,
        )
        results.append(
            {"id": entity_id, "deleted": True, "reversible": reversible, "identity": "PASS"}
        )

    return {"mode": "committed", "results": results}
