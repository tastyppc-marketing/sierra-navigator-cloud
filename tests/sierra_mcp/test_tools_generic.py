"""Tier-2 generic caller (sierra_call): allowlist, classification, locked-destructive
refusals, and the read / guarded-write paths — all driven through FakeTransport."""
import json

import pytest

from sierra_core.client import SierraHttpClient
from sierra_core.transport import FakeTransport
from sierra_mcp import audit, context, tools_generic
from sierra_mcp.runtime import SierraRuntime
from sierra_mcp.guards import TRACKER, ConfirmTokenError, ScopeError

SITE_ID = 5907


def env(inner: dict) -> str:
    return json.dumps({"d": json.dumps(inner)})


def ok(data) -> str:
    return env({"responseCode": 0, "data": data})


class _FakeBroker:
    def get_session(self, force_refresh=False):
        return object()

    def invalidate(self):
        pass


def make_runtime(ft: FakeTransport, site_id: int = SITE_ID) -> SierraRuntime:
    return SierraRuntime(
        broker=_FakeBroker(),
        build_client_fn=lambda sess, *, allow_write=False: SierraHttpClient(
            ft, site_id=site_id, allow_write=allow_write
        ),
    )


@pytest.fixture
def ctx():
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
    context.use(conn=conn, runtime=make_runtime(c.ft))
    yield c
    context.reset()
    TRACKER.reset()


def _audit_triples(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT tool, action, result FROM audit_log"
    ).fetchall()]


# ========================================================================== #
# classification (pure; no catalogue needed)
# ========================================================================== #

@pytest.mark.parametrize("path,expected", [
    ("/content-pages.aspx/GetFilters", "read"),
    ("/x.aspx/ListThings", "read"),
    ("/x.aspx/FindLead", "read"),
    ("/x.aspx/CheckStatus", "read"),
    ("/x.aspx/LoadData", "read"),
    ("/x.aspx/ValidateForm", "read"),
    ("/x.aspx/SearchLeads", "read"),
    ("/x.aspx/CountRows", "read"),
    ("/content-page-form.aspx/AddContentLabel", "write"),
    ("/x.aspx/UpdateThing", "write"),
    ("/x.aspx/SaveThing", "write"),
    ("/x.aspx/RemoveThing", "write"),
    ("/x.aspx/SetFlag", "write"),
    ("/x.aspx/CreateWidget", "write"),
    ("/x.aspx/DuplicateThing", "write"),   # classified write; refusal is separate
    ("/x.aspx/DeleteThing", "delete"),
    ("/action-plans.aspx/DeleteActionPlan", "delete"),
])
def test_classify(path, expected):
    assert tools_generic.classify(path) == expected


# ========================================================================== #
# allowlist + locked-destructive refusals
# ========================================================================== #

def test_unknown_path_refused(ctx):
    ft = ctx.wire({})
    with pytest.raises(ValueError) as ei:
        tools_generic.sierra_call("/totally-made-up.aspx/GetNope")
    assert "catalogue" in str(ei.value)
    assert ft.calls == []  # nothing posted


@pytest.mark.parametrize("path", [
    "/content-pages.aspx/DeleteContentPage",
    "/saved-searches.aspx/DeleteSavedSearch",
    "/content-pages.aspx/DuplicateContentPage",   # Duplicate* -> About-duplicate class
])
def test_locked_destructive_refused_with_tier1_message(ctx, path):
    ft = ctx.wire({})
    with pytest.raises(ValueError) as ei:
        tools_generic.sierra_call(path, {"id": 1})
    assert "propose_deletions/confirm_deletions" in str(ei.value)
    assert ft.calls == []  # nothing destructive reached Sierra


# ========================================================================== #
# read path
# ========================================================================== #

def test_read_path_executes_and_audits(ctx):
    ft = ctx.wire({
        "/content-pages.aspx/GetFilters": ok({"sections": [{"id": 1}], "labels": []}),
    })
    out = tools_generic.sierra_call(
        "/content-pages.aspx/GetFilters", {"siteId": 4989, "agentSiteId": -1}
    )
    assert out == {
        "mode": "called",
        "path": "/content-pages.aspx/GetFilters",
        "result": {"sections": [{"id": 1}], "labels": []},
    }
    # body passed VERBATIM — no siteId coercion/mutation
    path, body = ft.calls[0]
    assert path == "/content-pages.aspx/GetFilters"
    assert body == {"siteId": 4989, "agentSiteId": -1}
    assert ("sierra_call", "call", "ok") in _audit_triples(ctx.conn)


def test_read_without_read_scope_raises(ctx, monkeypatch):
    ft = ctx.wire({"/content-pages.aspx/GetFilters": ok({"sections": []})})
    monkeypatch.setattr(context, "granted_scopes", lambda: set())
    with pytest.raises(ScopeError):
        tools_generic.sierra_call("/content-pages.aspx/GetFilters", {})
    assert ft.calls == []


# ========================================================================== #
# write path — dry-run -> commit (guarded)
# ========================================================================== #

def test_write_path_dry_run_sends_nothing_then_commit_verbatim(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 9})})
    body = {"name": "X", "siteId": 4989, "agentSiteId": -1, "pageId": -1}

    dry = tools_generic.sierra_call("/content-page-form.aspx/AddContentLabel", body)
    assert dry["mode"] == "dry_run"
    assert dry["tool"] == "sierra_call:/content-page-form.aspx/AddContentLabel"
    assert dry["preview"] == {"path": "/content-page-form.aspx/AddContentLabel", "body": body}
    assert dry["confirm_token"].startswith("ct_")
    assert ft.calls == []  # dry-run sends NOTHING

    out = tools_generic.sierra_call(
        "/content-page-form.aspx/AddContentLabel", body, confirm_token=dry["confirm_token"]
    )
    assert out["mode"] == "committed"
    # generic caller returns the RAW unwrapped payload (not a shaped value like the
    # typed add_content_label, which would extract the int id).
    assert out["result"] == {"contentLabelId": 9}
    path, sent = ft.calls[0]
    assert path == "/content-page-form.aspx/AddContentLabel"
    assert sent == body  # VERBATIM — no siteId added/changed by the generic caller
    assert (
        "sierra_call:/content-page-form.aspx/AddContentLabel", "commit", "ok"
    ) in _audit_triples(ctx.conn)


def test_write_commit_token_reuse_rejected(ctx):
    ft = ctx.wire({"/content-page-form.aspx/AddContentLabel": ok({"contentLabelId": 1})})
    body = {"name": "Y"}
    token = tools_generic.sierra_call("/content-page-form.aspx/AddContentLabel", body)["confirm_token"]
    tools_generic.sierra_call("/content-page-form.aspx/AddContentLabel", body, confirm_token=token)
    with pytest.raises(ConfirmTokenError):
        tools_generic.sierra_call("/content-page-form.aspx/AddContentLabel", body, confirm_token=token)


# ========================================================================== #
# delete-classified (non-locked) path — also guarded
# ========================================================================== #

def test_delete_classified_path_is_guarded(ctx):
    # A Delete* path NOT in the locked set classifies "delete" and flows through the
    # same dry-run -> confirm machine (scope delete, delete volume counter).
    ft = ctx.wire({"/action-plans.aspx/DeleteActionPlan": ok({"deleted": True})})
    body = {"actionPlanId": 42}

    dry = tools_generic.sierra_call("/action-plans.aspx/DeleteActionPlan", body)
    assert dry["mode"] == "dry_run"
    assert dry["tool"] == "sierra_call:/action-plans.aspx/DeleteActionPlan"
    assert ft.calls == []

    out = tools_generic.sierra_call(
        "/action-plans.aspx/DeleteActionPlan", body, confirm_token=dry["confirm_token"]
    )
    assert out["mode"] == "committed"
    path, sent = ft.calls[0]
    assert path == "/action-plans.aspx/DeleteActionPlan"
    assert sent == body  # verbatim


def test_delete_classified_requires_delete_scope(ctx, monkeypatch):
    ctx.wire({"/action-plans.aspx/DeleteActionPlan": ok({"deleted": True})})
    monkeypatch.setattr(context, "granted_scopes", lambda: {"read", "write"})
    with pytest.raises(ScopeError):
        tools_generic.sierra_call("/action-plans.aspx/DeleteActionPlan", {"actionPlanId": 42})
