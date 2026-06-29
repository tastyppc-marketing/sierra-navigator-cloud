"""LOCAL review dashboard — server-rendered UI over the guarded delete tools.

Drives ``dashboard_app`` with Starlette's ``TestClient`` against a ``:memory:``
audit DB + a FakeTransport-backed ``SierraRuntime`` (mirrors the fixture in
``test_tools_write.py``). No live Sierra, no network.

Threading note: ``TestClient`` runs the ASGI app in a separate portal thread, so
the shared in-memory sqlite connection is opened with ``check_same_thread=False``
(``audit.connect`` defaults to thread-affine). Production drives the same handlers
on a single asyncio thread, where the lazily-created connection stays consistent.
"""
import json
import re
import sqlite3

import pytest
from starlette.testclient import TestClient

from sierra_core.client import SierraHttpClient
from sierra_core.transport import FakeTransport
from sierra_mcp import audit, context
from sierra_mcp.dashboard import dashboard_app
from sierra_mcp.guards import TRACKER
from sierra_mcp.runtime import SierraRuntime

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
    return SierraRuntime(
        broker=_FakeBroker(),
        build_client_fn=lambda sess, *, allow_write=False: SierraHttpClient(
            ft, site_id=site_id, allow_write=allow_write
        ),
    )


def _memory_conn() -> sqlite3.Connection:
    """An in-memory audit DB usable across TestClient's portal thread."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    audit.init_schema(conn)
    return conn


@pytest.fixture
def ctx():
    """Pin a fresh :memory: audit DB + FakeTransport runtime; yield a Ctx helper."""
    conn = _memory_conn()
    TRACKER.reset()

    class Ctx:
        def __init__(self):
            self.conn = conn
            self.ft = FakeTransport({})
            self.client = TestClient(dashboard_app)

        def wire(self, responses: dict) -> FakeTransport:
            self.ft = FakeTransport(responses)
            context.use(conn=conn, runtime=make_runtime(self.ft))
            return self.ft

    c = Ctx()
    context.use(conn=conn, runtime=make_runtime(c.ft))  # default pin
    yield c
    context.reset()
    TRACKER.reset()


def _token(html_text: str) -> str:
    """Pull the minted dt_ confirm token out of a rendered preview page."""
    m = re.search(r'name="confirm_token" value="(dt_[0-9a-f]+)"', html_text)
    assert m, "no confirm_token rendered in preview page"
    return m.group(1)


# ========================================================================== #
# GET / — index
# ========================================================================== #

def test_index_renders_banner_and_rows_with_id_and_title(ctx):
    ctx.wire({
        "/content-pages.aspx/GetContentPageList":
            ok({"contentPages": [{"id": 900, "name": "Home Page", "statusName": "Published"}]}),
        "/saved-searches.aspx/GetSavedSearchList":
            ok({"items": [{"id": 77, "searchName": "Lakefront Homes", "status": "Active"}]}),
    })
    r = ctx.client.get("/")

    assert r.status_code == 200
    body = r.text
    # loud local-only banner
    assert "LOCAL REVIEW BUILD" in body
    # both sections present
    assert "Content pages" in body and "Saved searches" in body
    # content page: id AND title side by side
    assert "900" in body and "Home Page" in body
    # saved search: id AND title
    assert "77" in body and "Lakefront Homes" in body
    # per-row checkboxes carrying the id as value, plus the two preview forms
    assert 'name="ids" value="900"' in body
    assert 'name="ids" value="77"' in body
    assert 'action="/preview"' in body
    assert 'name="entity_type" value="content_page"' in body
    assert 'name="entity_type" value="saved_search"' in body
    # content pages flagged irreversible right on the list
    assert "IRREVERSIBLE" in body


def test_cleanup_alias_also_renders(ctx):
    ctx.wire({
        "/content-pages.aspx/GetContentPageList": ok({"contentPages": []}),
        "/saved-searches.aspx/GetSavedSearchList": ok({"items": []}),
    })
    r = ctx.client.get("/cleanup")
    assert r.status_code == 200
    assert "LOCAL REVIEW BUILD" in r.text


def test_index_survives_a_failing_list_endpoint(ctx):
    # content-page list errors (no fake) -> that section degrades, saved searches still render
    ctx.wire({
        "/saved-searches.aspx/GetSavedSearchList":
            ok({"items": [{"id": 77, "searchName": "Lakefront Homes"}]}),
    })
    r = ctx.client.get("/")
    assert r.status_code == 200
    assert "Lakefront Homes" in r.text  # the healthy section rendered


# ========================================================================== #
# POST /preview — propose_deletions
# ========================================================================== #

def test_preview_content_page_shows_candidate_token_and_irreversible(ctx):
    ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Old Home"}}),
    })
    r = ctx.client.post("/preview", data={"entity_type": "content_page", "ids": "900"})

    assert r.status_code == 200
    body = r.text
    # id + stored_title side by side
    assert "900" in body and "Old Home" in body
    # the identity-lock echo is staged in a hidden field
    assert 'name="expected_title" value="Old Home"' in body
    # a one-time confirm token is present
    assert "dt_" in body and 'name="confirm_token"' in body
    # IRREVERSIBLE badge for a content page
    assert "IRREVERSIBLE" in body


def test_preview_fetch_error_row_shown_but_not_deletable(ctx):
    ctx.wire({"/content-page-form.aspx/GetPage": err(1, "not found")})
    r = ctx.client.post("/preview", data={"entity_type": "content_page", "ids": "404"})

    body = r.text
    assert "404" in body
    assert "not deletable" in body
    # no expected_title hidden field for a non-deletable row
    assert 'name="expected_title"' not in body


def test_preview_with_no_selection_is_friendly(ctx):
    ctx.wire({})
    r = ctx.client.post("/preview", data={"entity_type": "content_page"})
    assert r.status_code == 200
    assert "Nothing selected" in r.text


# ========================================================================== #
# POST /confirm — confirm_deletions
# ========================================================================== #

def test_confirm_content_page_happy_pass_and_deletes(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Old Home"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    pv = ctx.client.post("/preview", data={"entity_type": "content_page", "ids": "900"})
    token = _token(pv.text)

    cf = ctx.client.post("/confirm", data={
        "entity_type": "content_page",
        "confirm_token": token,
        "ids": "900",
        "expected_title": "Old Home",
    })

    assert cf.status_code == 200
    assert "PASS" in cf.text
    assert "1 deleted" in cf.text
    # the FakeTransport actually saw the identity-locked delete call
    paths = [p for p, _ in ft.calls]
    assert "/content-pages.aspx/DeleteContentPage" in paths
    # ledger recorded the pre-delete snapshot, flipped to deleted
    row = ctx.conn.execute(
        "SELECT entity_id, title_snapshot, cleanup_status FROM ledger"
    ).fetchone()
    assert row["entity_id"] == "900" and row["title_snapshot"] == "Old Home"
    assert row["cleanup_status"] == "deleted"


def test_confirm_saved_search_happy_calls_delete_saved_search(ctx):
    ft = ctx.wire({
        "/lead-detail.aspx/GetSavedSearchRecord":
            ok({"savedSearch": {"id": 77, "searchName": "Lakefront"}}),
        "/saved-searches.aspx/DeleteSavedSearch": ok({"deleted": True}),
    })
    pv = ctx.client.post("/preview", data={"entity_type": "saved_search", "ids": "77"})
    token = _token(pv.text)

    cf = ctx.client.post("/confirm", data={
        "entity_type": "saved_search",
        "confirm_token": token,
        "ids": "77",
        "expected_title": "Lakefront",
    })

    assert "PASS" in cf.text
    assert "reversible (soft)" in cf.text
    paths = [p for p, _ in ft.calls]
    assert "/saved-searches.aspx/DeleteSavedSearch" in paths


def test_confirm_wrong_title_aborts_identity_lock_no_delete(ctx):
    ft = ctx.wire({
        "/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "Actual Title"}}),
        "/content-pages.aspx/DeleteContentPage": ok({"deleted": True}),
    })
    pv = ctx.client.post("/preview", data={"entity_type": "content_page", "ids": "900"})
    token = _token(pv.text)

    cf = ctx.client.post("/confirm", data={
        "entity_type": "content_page",
        "confirm_token": token,
        "ids": "900",
        "expected_title": "Wrong Title",  # mismatch -> identity lock aborts the row
    })

    assert cf.status_code == 200
    assert "ABORTED" in cf.text
    # the destructive call NEVER fired and nothing was snapshotted
    assert "/content-pages.aspx/DeleteContentPage" not in [p for p, _ in ft.calls]
    assert ctx.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0


def test_confirm_with_bad_token_shows_error_page(ctx):
    ctx.wire({"/content-page-form.aspx/GetPage": ok({"page": {"id": 900, "name": "X"}})})
    cf = ctx.client.post("/confirm", data={
        "entity_type": "content_page",
        "confirm_token": "dt_deadbeef",  # never minted
        "ids": "900",
        "expected_title": "X",
    })
    assert cf.status_code == 400
    assert "Confirm failed" in cf.text


# ========================================================================== #
# isolation: the dashboard is NOT wired into the MCP server
# ========================================================================== #

def test_server_module_does_not_import_dashboard():
    import sierra_mcp.server as server

    src = open(server.__file__, encoding="utf-8").read()
    assert "dashboard" not in src, "server.py must not import/reference the dashboard"


def test_dashboard_does_not_import_server():
    import sierra_mcp.dashboard as dash

    src = open(dash.__file__, encoding="utf-8").read()
    assert "import sierra_mcp.server" not in src
    assert "from sierra_mcp.server" not in src
