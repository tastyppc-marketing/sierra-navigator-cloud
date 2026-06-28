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
    ("/x.aspx/SetFlag", "write"),
    ("/x.aspx/CreateWidget", "write"),
    # default-deny: every destructive verb AND any unrecognized verb -> "refused"
    ("/x.aspx/RemoveThing", "refused"),
    ("/x.aspx/DuplicateThing", "refused"),
    ("/x.aspx/DeleteThing", "refused"),
    ("/action-plans.aspx/DeleteActionPlan", "refused"),
    ("/x.aspx/MergeLeads", "refused"),
    ("/leads.aspx/BulkDeleteLeads", "refused"),
    ("/x.aspx/FrobnicateThing", "refused"),   # unrecognized verb -> default-deny
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
# default-deny: NO destruction via the generic caller (#1/#2/#6/#7)
# ========================================================================== #

def test_non_locked_delete_is_refused_and_audited(ctx):
    # A Delete* on an entity WITHOUT a Tier-1 flow is refused outright (no raw post),
    # and the refusal is recorded in the append-only audit log.
    ft = ctx.wire({})
    with pytest.raises(ValueError) as ei:
        tools_generic.sierra_call("/action-plans.aspx/DeleteActionPlan", {"actionPlanId": 42})
    assert "not available via the generic caller" in str(ei.value)
    assert ft.calls == []
    assert ("sierra_call", "call", "rejected") in _audit_triples(ctx.conn)


def test_remove_merge_bulk_refused(ctx):
    ft = ctx.wire({})
    for path in ("/widgets.aspx/RemoveHtmlWidget", "/leads.aspx/MergeLeads",
                 "/saved-searches.aspx/MergeSavedSearches", "/leads.aspx/BulkDeleteLeads"):
        with pytest.raises(ValueError):
            tools_generic.sierra_call(path, {"id": 1})
    assert ft.calls == []


def test_every_catalogued_destructive_path_is_refused(ctx):
    # Catalogue-driven regression: EVERY catalogued endpoint whose method is destructive
    # must be refused by sierra_call — proving the whole 642-surface is covered, not a
    # hand-picked few. NOT ONE may reach the FakeTransport.
    ft = ctx.wire({})
    destructive = [p for p in tools_generic._allowlist()
                   if tools_generic._is_destructive(tools_generic._method_of(p))]
    assert len(destructive) >= 40, f"expected many destructive paths, found {len(destructive)}"
    for p in destructive:
        with pytest.raises(ValueError):
            tools_generic.sierra_call(p, {})
    assert ft.calls == []  # not one destructive call reached Sierra


# ========================================================================== #
# W4-T1 (re-audit New-1 CRIT, New-2 HIGH, residual #1/#2/#7): classifier soundness.
# classify() must match verbs on CamelCase TOKEN BOUNDARIES (verb is the whole method
# or is followed by an uppercase letter), not raw startswith — pre-fix the read prefix
# "Can" swallowed the mutating verb "Cancel*". Plus the Cancel-family and name-fragment
# soft-deletes ("Deletion", e.g. SetClientDeletionStatus*) are destructive.
# ========================================================================== #

@pytest.mark.parametrize("path,expected", [
    # New-1: "Can" must NOT swallow the mutating "Cancel*" verb
    ("/lead-detail.aspx/CancelScheduledMessage", "refused"),
    ("/facebook-relogin.aspx/CancelNotification", "refused"),
    ("/x.aspx/CancelAnything", "refused"),
    # a genuine boolean "Can*" query still reads (Can + uppercase = a token boundary)
    ("/x.aspx/CanEditPage", "read"),
    # New-2: name-fragment soft-delete ("Deletion" is not substring "Delete"; starts "Set")
    ("/saved-searches.aspx/SetClientDeletionStatusForSavedSearches", "refused"),
    # newly-recognised destructive verbs
    ("/x.aspx/DisableUser", "refused"),
    ("/x.aspx/RevokeAccess", "refused"),
    ("/x.aspx/VoidInvoice", "refused"),
    ("/x.aspx/TerminateSession", "refused"),
    ("/x.aspx/ReleaseNumber", "refused"),
    ("/x.aspx/ExpireToken", "refused"),
    # token boundary: a verb that merely prefixes a longer lowercase word is NOT that verb
    ("/x.aspx/Setting", "refused"),    # "Set"+"ting" (lowercase) is not write-verb "Set"
    ("/x.aspx/Isengard", "refused"),   # "Is"+"engard" (lowercase) is not read-verb "Is"
    # sanity: real read/write verbs still classify (uppercase-next boundary)
    ("/x.aspx/GetThing", "read"),
    ("/x.aspx/SetThing", "write"),
])
def test_classify_token_boundary_and_new_destructive_verbs(path, expected):
    assert tools_generic.classify(path) == expected


@pytest.mark.parametrize("path,expected", [
    # re-audit #3 HIGH: a destructive verb MID-name (after a benign leading verb like "Test")
    # is destructive — these 3 real catalogued voice-and-text ops are IRREVERSIBLE.
    ("/voice-and-text-settings.aspx/TestVoiceAndTextReleaseExpiredNumbers", "refused"),
    ("/voice-and-text-settings.aspx/TestVoiceAndTextManualDisable", "refused"),
    ("/voice-and-text-settings.aspx/TestVoiceAndTextCleanNumberReferences", "refused"),
    ("/x.aspx/SaveAndPurgeOldCache", "refused"),   # Purge mid-name after write-verb Save
    ("/x.aspx/UpdateThenRevokeKeys", "refused"),   # Revoke mid-name
    # leading destructive still refused (regression)
    ("/x.aspx/ReleaseNumbers", "refused"),
    ("/x.aspx/CleanReferences", "refused"),        # Clean newly added
    # benign read/write with NO destructive token are unaffected (no over-refusal)
    ("/content-pages.aspx/GetFilters", "read"),
    ("/content-pages.aspx/UpdateContentLabel", "write"),
    ("/x.aspx/CanEditPage", "read"),
    ("/x.aspx/GetArchivedList", "read"),           # "Archived" token != destructive verb "Archive"
])
def test_classify_destructive_verb_anywhere_in_method(path, expected):
    assert tools_generic.classify(path) == expected


def test_real_cancel_and_softdelete_endpoints_refused_e2e(ctx):
    # The three real catalogued mutation endpoints the re-audit flagged are refused +
    # audited, NOT executed on the live read/write path.
    ft = ctx.wire({})
    for path in ("/lead-detail.aspx/CancelScheduledMessage",
                 "/facebook-relogin.aspx/CancelNotification",
                 "/saved-searches.aspx/SetClientDeletionStatusForSavedSearches"):
        with pytest.raises(ValueError):
            tools_generic.sierra_call(path, {"id": 1})
    assert ft.calls == []  # not one reached Sierra
    assert ("sierra_call", "call", "rejected") in _audit_triples(ctx.conn)
