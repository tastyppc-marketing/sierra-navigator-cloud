"""Guarded write + identity-locked delete tools.

Read-only contract for the *tests*: everything is driven through ``FakeTransport``
(no live Sierra), but these tools DO build ``allow_write=True`` clients against the
fake. Identity + DB + runtime are injected via ``context.use`` (a ``:memory:``
audit DB + a FakeTransport-backed runtime)."""
import json

import pytest

from sierra_core.client import SierraHttpClient
from sierra_core.transport import FakeTransport
from sierra_mcp import audit, context, tools_write
from sierra_mcp.runtime import SierraRuntime
from sierra_mcp.guards import (
    TRACKER,
    ConfirmTokenError,
    ScopeError,
    VolumeCapError,
)

SITE_ID = 5907


def env(inner: dict) -> str:
    return json.dumps({"d": json.dumps(inner)})


def ok(data) -> str:
    return env({"responseCode": 0, "data": data})


def err(code: int = 1, message: str = "boom") -> str:
    return env({"responseCode": code, "message": message})


class _FakeBroker:
    def get_session(self, force_refresh=False, *, stale=None):
        return object()

    def invalidate(self):
        pass


def make_runtime(ft: FakeTransport, site_id: int = SITE_ID) -> SierraRuntime:
    # build_client_fn honours allow_write so runtime.write/delete build write clients.
    return SierraRuntime(
        broker=_FakeBroker(),
        build_client_fn=lambda sess, *, allow_write=False: SierraHttpClient(
            ft, site_id=site_id, allow_write=allow_write
        ),
    )


@pytest.fixture
def ctx():
    """Pin a fresh :memory: audit DB + FakeTransport runtime for one test."""
    conn = audit.connect(":memory:")
    TRACKER.reset()

    class Ctx:
        def __init__(self):
            self.conn = conn
            self.ft = FakeTransport({})

        def wire(self, responses: dict) -> FakeTransport:
            self.ft = FakeTransport(responses)
            context.use(conn=conn, runtime=make_runtime(self.ft))
            return self.ft

    c = Ctx()
    context.use(conn=conn, runtime=make_runtime(c.ft))  # default pin
    yield c
    context.reset()
    TRACKER.reset()


def _audit_triples(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT tool, action, result FROM audit_log"
    ).fetchall()]


# ========================================================================== #
# write tools — dry-run
# ========================================================================== #

def test_write_dry_run_mints_token_and_sends_nothing(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    out = tools_write.create_content_label("Lakefront")

    assert out["mode"] == "dry_run"
    assert out["tool"] == "create_content_label"
    assert out["preview"] == {"name": "Lakefront", "page_id": -1}
    assert out["confirm_token"].startswith("ct_")
    assert "expires_at" in out
    assert ft.calls == []  # NOTHING reached Sierra
    assert ("create_content_label", "preview", "ok") in _audit_triples(ctx.conn)


def test_remove_tool_dry_run_carries_warning(ctx):
    ctx.wire({})
    out = tools_write.remove_content_label(55)
    assert out["mode"] == "dry_run"
    assert out["warnings"]  # remove_* surfaces a caution


# ========================================================================== #
# write tools — commit
# ========================================================================== #

def test_write_commit_calls_sierra_and_audits(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 321})})
    token = tools_write.create_content_label("Lakefront")["confirm_token"]

    out = tools_write.create_content_label("Lakefront", confirm_token=token)

    assert out["mode"] == "committed"
    assert out["result"] == 321
    assert len(ft.calls) == 1
    path, body = ft.calls[0]
    assert path == "/content-page-form.aspx/AddContentLabel"
    assert body["name"] == "Lakefront"
    assert body["siteId"] == SITE_ID
    assert ("create_content_label", "commit", "ok") in _audit_triples(ctx.conn)


def test_update_content_label_commit_path(ctx):
    ft = ctx.wire({"/content-pages.aspx/UpdateContentLabel": ok({})})
    token = tools_write.update_content_label(55, "New Name")["confirm_token"]
    out = tools_write.update_content_label(55, "New Name", confirm_token=token)

    assert out["mode"] == "committed"
    path, body = ft.calls[0]
    assert path == "/content-pages.aspx/UpdateContentLabel"
    assert body["contentLabelId"] == 55 and body["name"] == "New Name"


def test_remove_page_content_label_link_commit_path(ctx):
    ft = ctx.wire({"/content-pages.aspx/RemovePageContentLabelLink": ok({})})
    token = tools_write.remove_page_content_label_link(900, 55)["confirm_token"]
    tools_write.remove_page_content_label_link(900, 55, confirm_token=token)

    path, body = ft.calls[0]
    assert path == "/content-pages.aspx/RemovePageContentLabelLink"
    assert body["pageId"] == 900 and body["contentLabelId"] == 55


def test_update_page_component_title_commit_path(ctx):
    ft = ctx.wire({"/content-page-form.aspx/UpdatePageComponentTitle": ok({})})
    token = tools_write.update_page_component_title(7, "Hero")["confirm_token"]
    tools_write.update_page_component_title(7, "Hero", confirm_token=token)

    path, body = ft.calls[0]
    assert path == "/content-page-form.aspx/UpdatePageComponentTitle"
    assert body["componentId"] == 7 and body["componentTitle"] == "Hero"


def test_commit_token_reuse_raises(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    token = tools_write.create_content_label("L")["confirm_token"]
    tools_write.create_content_label("L", confirm_token=token)  # first commit ok
    with pytest.raises(ConfirmTokenError):
        tools_write.create_content_label("L", confirm_token=token)  # reuse rejected
    assert len(ft.calls) == 1  # the second commit never reached Sierra


def test_commit_mutated_payload_raises(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    token = tools_write.create_content_label("Original")["confirm_token"]
    with pytest.raises(ConfirmTokenError):
        tools_write.create_content_label("Mutated", confirm_token=token)  # hash mismatch
    assert ft.calls == []  # nothing reached Sierra


def test_commit_without_write_scope_raises(ctx, monkeypatch):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    token = tools_write.create_content_label("L")["confirm_token"]  # minted with scope
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read"})
    with pytest.raises(ScopeError):
        tools_write.create_content_label("L", confirm_token=token)
    assert ft.calls == []


def test_write_session_cap_enforced(ctx, monkeypatch):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    monkeypatch.setattr(TRACKER, "write_cap", 1)
    t1 = tools_write.create_content_label("A")["confirm_token"]
    tools_write.create_content_label("A", confirm_token=t1)  # 1st within cap
    t2 = tools_write.create_content_label("B")["confirm_token"]
    with pytest.raises(VolumeCapError):
        tools_write.create_content_label("B", confirm_token=t2)  # 2nd over cap


# ========================================================================== #
# delete — propose
# ========================================================================== #

def test_propose_returns_candidates_sends_nothing_destructive(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Home"}}),
    })
    out = tools_write.propose_deletions("content_page", [900])

    assert out["mode"] == "dry_run"
    assert out["candidates"][0] == {
        "entity_type": "content_page", "id": 900,
        "stored_title": "Home", "reversible": False,
    }
    assert out["confirm_token"].startswith("dt_")
    # only the read GetPage was hit — nothing destructive
    assert [p for p, _ in ft.calls] == ["/content-page-form.aspx/GetPage"]
    assert ("propose_deletions", "propose", "ok") in _audit_triples(ctx.conn)


def test_propose_saved_search_marks_reversible(ctx):
    ctx.wire({
        "/lead-detail.aspx/GetSavedSearchRecord":
            ok({"savedSearch": {"id": 77, "searchName": "Lakefront"}}),
    })
    out = tools_write.propose_deletions("saved_search", [77])
    cand = out["candidates"][0]
    assert cand["stored_title"] == "Lakefront" and cand["reversible"] is True


def test_propose_over_call_cap_raises(ctx, monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_DELETE_CALL_CAP", "3")
    ft = ctx.wire({"/content-page-form.aspx/GetPage": ok({"page": {"id": 1, "name": "X"}})})
    with pytest.raises(VolumeCapError):
        tools_write.propose_deletions("content_page", [1, 2, 3, 4])
    assert ft.calls == []  # cap is checked before any fetch


def test_propose_fetch_failure_excludes_id(ctx):
    ctx.wire({"/content-page-form.aspx/GetPage": err(1, "not found")})
    out = tools_write.propose_deletions("content_page", [404])
    cand = out["candidates"][0]
    assert cand["id"] == 404 and "error" in cand
    assert "stored_title" not in cand  # excluded from the deletable set


def test_propose_unknown_entity_type_raises(ctx):
    ctx.wire({})
    with pytest.raises(ValueError):
        tools_write.propose_deletions("widget", [1])


# ========================================================================== #
# delete — confirm (happy paths)
# ========================================================================== #

def test_confirm_content_page_hard_delete_happy(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": [
            ok({"page": {"id": 900, "name": "Old Home"}}),
            ok({"page": {"id": 900, "name": "Old Home"}}),
            err(1, "Page not found"),
        ],
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])
    out = tools_write.confirm_deletions(
        prop["confirm_token"], "content_page",
        [{"id": 900, "expected_title": "Old Home"}],
    )

    assert out["results"][0] == {
        "id": 900, "deleted": True, "reversible": False, "identity": "PASS",
    }
    # the correct identity-locked delete endpoint + body
    del_body = next(b for p, b in ft.calls if p == "/content-pages.aspx/DeleteContentPage")
    assert del_body["pageId"] == 900
    # ledger: pre-delete snapshot flipped to "deleted" with a deleted_at stamp
    led = ctx.conn.execute(
        "SELECT entity_id, title_snapshot, cleanup_status, deleted_at, payload_snapshot "
        "FROM ledger"
    ).fetchall()
    assert len(led) == 1
    row = led[0]
    assert row["entity_id"] == "900" and row["title_snapshot"] == "Old Home"
    assert row["cleanup_status"] == "deleted" and row["deleted_at"] is not None
    assert json.loads(row["payload_snapshot"])["page"]["name"] == "Old Home"
    assert ("confirm_deletions", "delete", "ok") in _audit_triples(ctx.conn)


def test_confirm_saved_search_soft_delete_happy(ctx):
    ft = ctx.wire({
        "/lead-detail.aspx/GetSavedSearchRecord":
            ok({"savedSearch": {"id": 77, "searchName": "Lakefront"}}),
        "/saved-searches.aspx/DeleteSavedSearch": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("saved_search", [77])
    out = tools_write.confirm_deletions(
        prop["confirm_token"], "saved_search",
        [{"id": 77, "expected_title": "Lakefront"}],
    )

    assert out["results"][0] == {
        "id": 77, "deleted": True, "reversible": True, "identity": "PASS",
    }
    del_body = next(b for p, b in ft.calls if p == "/saved-searches.aspx/DeleteSavedSearch")
    assert del_body["savedSearchId"] == 77
    assert del_body["siteId"] == str(SITE_ID)  # this delete wants a STRING siteId
    led = ctx.conn.execute("SELECT cleanup_status, deleted_at FROM ledger").fetchall()
    assert led[0]["cleanup_status"] == "deleted" and led[0]["deleted_at"] is not None


# ========================================================================== #
# delete — confirm (identity lock + token set integrity)
# ========================================================================== #

def test_confirm_identity_mismatch_aborts_row_no_snapshot_no_delete(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Actual Title"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])
    out = tools_write.confirm_deletions(
        prop["confirm_token"], "content_page",
        [{"id": 900, "expected_title": "Wrong Title"}],  # mismatch
    )

    row = out["results"][0]
    assert row["identity"] == "ABORTED" and row["deleted"] is False and "error" in row
    # sierra_core takes the snapshot AFTER assert_identity, so an abort means
    # NO destructive call and NO snapshot row.
    assert "/content-pages.aspx/DeleteContentPage" not in [p for p, _ in ft.calls]
    assert ctx.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    assert ("confirm_deletions", "delete", "aborted") in _audit_triples(ctx.conn)


def test_confirm_batch_one_pass_one_abort_does_not_fail_batch(ctx):
    # FakeTransport is path-keyed, so both GetPage calls return id=900; the second
    # confirmed id (901) trips sierra_core's id-echo guard -> that row ABORTs while
    # 900 still deletes. Proves the batch continues past an aborted row.
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": [
            ok({"page": {"id": 900, "name": "Home"}}),
            ok({"page": {"id": 900, "name": "Home"}}),
            ok({"page": {"id": 900, "name": "Home"}}),
            err(1, "Page not found"),
            ok({"page": {"id": 900, "name": "Home"}}),
        ],
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900, 901])
    out = tools_write.confirm_deletions(
        prop["confirm_token"], "content_page",
        [{"id": 900, "expected_title": "Home"}, {"id": 901, "expected_title": "Home"}],
    )

    by_id = {r["id"]: r for r in out["results"]}
    assert by_id[900]["identity"] == "PASS" and by_id[900]["deleted"] is True
    assert by_id[901]["identity"] == "ABORTED" and by_id[901]["deleted"] is False
    # exactly one snapshot (for the row that actually deleted)
    assert ctx.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 1


def test_confirm_with_extra_id_rejected_by_token(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Home"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])  # proposed set = {900}
    with pytest.raises(ConfirmTokenError):
        tools_write.confirm_deletions(
            prop["confirm_token"], "content_page",
            [{"id": 900, "expected_title": "Home"},
             {"id": 901, "expected_title": "Other"}],  # set {900,901} != {900}
        )
    assert "/content-pages.aspx/DeleteContentPage" not in [p for p, _ in ft.calls]


def test_confirm_without_delete_scope_raises(ctx, monkeypatch):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Home"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read", "write"})
    with pytest.raises(ScopeError):
        tools_write.confirm_deletions(
            prop["confirm_token"], "content_page",
            [{"id": 900, "expected_title": "Home"}],
        )
    assert "/content-pages.aspx/DeleteContentPage" not in [p for p, _ in ft.calls]


# ========================================================================== #
# W2-T10 (#8): guard REJECTIONS are audited (result="rejected") AND still raise.
# A refusal that happens BEFORE any Sierra contact (bad scope / replayed-expired-
# tampered token / volume cap) must leave an immutable trail — the guardrails
# can't be probed invisibly. The per-row identity-lock ABORT is a separate,
# already-audited outcome and must stay distinct from a guard rejection.
# ========================================================================== #

def _rejected_triples(conn):
    return [
        (r["tool"], r["action"], r["scope"])
        for r in conn.execute(
            "SELECT tool, action, scope FROM audit_log WHERE result = 'rejected'"
        ).fetchall()
    ]


def _has_rejection(conn, tool, action):
    return any(t == tool and a == action for t, a, _ in _rejected_triples(conn))


# --- guarded_write: commit-path rejections -------------------------------- #

def test_write_commit_missing_scope_audits_rejection(ctx, monkeypatch):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    token = tools_write.create_content_label("L")["confirm_token"]  # minted with scope
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read"})
    with pytest.raises(ScopeError):
        tools_write.create_content_label("L", confirm_token=token)
    assert _has_rejection(ctx.conn, "create_content_label", "commit")
    assert ft.calls == []  # nothing reached Sierra


def test_write_commit_token_reuse_audits_rejection(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    token = tools_write.create_content_label("L")["confirm_token"]
    tools_write.create_content_label("L", confirm_token=token)  # first commit ok
    with pytest.raises(ConfirmTokenError):
        tools_write.create_content_label("L", confirm_token=token)  # reuse rejected
    assert _has_rejection(ctx.conn, "create_content_label", "commit")
    assert len(ft.calls) == 1  # the rejected reuse never reached Sierra


def test_write_commit_mutated_payload_audits_rejection(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    token = tools_write.create_content_label("Original")["confirm_token"]
    with pytest.raises(ConfirmTokenError):
        tools_write.create_content_label("Mutated", confirm_token=token)  # hash mismatch
    assert _has_rejection(ctx.conn, "create_content_label", "commit")
    assert ft.calls == []


def test_write_commit_volume_cap_audits_rejection(ctx, monkeypatch):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    monkeypatch.setattr(TRACKER, "write_cap", 1)
    t1 = tools_write.create_content_label("A")["confirm_token"]
    tools_write.create_content_label("A", confirm_token=t1)  # 1st within cap
    t2 = tools_write.create_content_label("B")["confirm_token"]
    with pytest.raises(VolumeCapError):
        tools_write.create_content_label("B", confirm_token=t2)  # 2nd over cap
    assert _has_rejection(ctx.conn, "create_content_label", "commit")


# --- guarded_write: dry-run-path rejection (scope checked before mint) ----- #

def test_write_dry_run_missing_scope_audits_rejection_as_preview(ctx, monkeypatch):
    ft = ctx.wire({})
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read"})
    with pytest.raises(ScopeError):
        tools_write.create_content_label("L")  # dry-run, no token
    # scope is checked BEFORE the token is minted -> the rejection is a "preview"
    assert _has_rejection(ctx.conn, "create_content_label", "preview")
    assert ft.calls == []


# --- propose_deletions rejections ----------------------------------------- #

def test_propose_over_call_cap_audits_rejection(ctx, monkeypatch):
    monkeypatch.setenv("SIERRA_MCP_DELETE_CALL_CAP", "2")
    ft = ctx.wire({"/content-page-form.aspx/GetPage": ok({"page": {"id": 1, "name": "X"}})})
    with pytest.raises(VolumeCapError):
        tools_write.propose_deletions("content_page", [1, 2, 3])
    assert _has_rejection(ctx.conn, "propose_deletions", "propose")
    assert ft.calls == []  # cap is checked before any fetch


def test_propose_missing_scope_audits_rejection(ctx, monkeypatch):
    ft = ctx.wire({"/content-page-form.aspx/GetPage": ok({"page": {"id": 1, "name": "X"}})})
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read", "write"})
    with pytest.raises(ScopeError):
        tools_write.propose_deletions("content_page", [1])
    assert _has_rejection(ctx.conn, "propose_deletions", "propose")
    assert ft.calls == []


# --- confirm_deletions rejections ----------------------------------------- #

def test_confirm_missing_scope_audits_rejection(ctx, monkeypatch):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Home"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read", "write"})
    with pytest.raises(ScopeError):
        tools_write.confirm_deletions(
            prop["confirm_token"], "content_page",
            [{"id": 900, "expected_title": "Home"}],
        )
    assert _has_rejection(ctx.conn, "confirm_deletions", "delete")
    assert "/content-pages.aspx/DeleteContentPage" not in [p for p, _ in ft.calls]


def test_confirm_token_set_mismatch_audits_rejection(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Home"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])  # proposed set = {900}
    with pytest.raises(ConfirmTokenError):
        tools_write.confirm_deletions(
            prop["confirm_token"], "content_page",
            [{"id": 900, "expected_title": "Home"},
             {"id": 901, "expected_title": "Other"}],  # set {900,901} != {900}
        )
    assert _has_rejection(ctx.conn, "confirm_deletions", "delete")
    assert "/content-pages.aspx/DeleteContentPage" not in [p for p, _ in ft.calls]


def test_confirm_volume_cap_audits_rejection(ctx, monkeypatch):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Home"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])
    monkeypatch.setattr(TRACKER, "delete_cap", 0)  # any delete reservation trips the cap
    with pytest.raises(VolumeCapError):
        tools_write.confirm_deletions(
            prop["confirm_token"], "content_page",
            [{"id": 900, "expected_title": "Home"}],
        )
    assert _has_rejection(ctx.conn, "confirm_deletions", "delete")
    assert "/content-pages.aspx/DeleteContentPage" not in [p for p, _ in ft.calls]


# --- the identity-lock ABORT stays a distinct, already-audited outcome ----- #

def test_returned_error_is_sanitized_while_audit_keeps_full_detail(ctx):
    # #17: the per-row `error` RETURNED to the client is a generic class name (no verbatim
    # upstream Sierra Message / stored title / HTTP internals); the full repr(e) is kept
    # only in the immutable audit DB.
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Confidential Title"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])
    out = tools_write.confirm_deletions(
        prop["confirm_token"], "content_page",
        [{"id": 900, "expected_title": "Wrong Title"}],  # identity mismatch -> ABORTED
    )
    row = out["results"][0]
    assert row["identity"] == "ABORTED"
    assert row["error"] == "IdentityLockError"          # sanitized class name only
    assert "Confidential Title" not in row["error"]     # no upstream detail leaked to client
    # the audit row keeps the full repr(e)
    audit_err = ctx.conn.execute(
        "SELECT error FROM audit_log WHERE tool='confirm_deletions' AND result='aborted'"
    ).fetchone()["error"]
    assert "IdentityLockError(" in audit_err            # full repr retained server-side


def test_identity_abort_audited_aborted_not_rejected(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Actual Title"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    prop = tools_write.propose_deletions("content_page", [900])
    out = tools_write.confirm_deletions(
        prop["confirm_token"], "content_page",
        [{"id": 900, "expected_title": "Wrong Title"}],  # identity mismatch -> ABORT
    )
    assert out["results"][0]["identity"] == "ABORTED"
    triples = _audit_triples(ctx.conn)
    assert ("confirm_deletions", "delete", "aborted") in triples
    # an abort is NOT a guard rejection — it must not land a "rejected" row
    assert ("confirm_deletions", "delete", "rejected") not in triples


def test_capturing_sink_is_idempotent_across_retry(ctx):
    # re-audit #3 LOW: runtime.call_with_refresh re-runs the whole delete op on a session-
    # expiry signal, calling the SAME snapshot_sink again. Without idempotency that inserts a
    # SECOND ledger row and orphans the first at "pending-delete" for an entity that WAS
    # deleted. The sink must snapshot exactly once.
    from sierra_mcp.tools_write import _capturing_sink
    sink, captured = _capturing_sink(ctx.conn, "op", "content_page")
    record = {"page": {"id": 900, "name": "Home"}}
    first = sink(record)
    second = sink(record)  # the retry re-invokes the sink
    assert first == second
    assert ctx.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 1  # one row only
