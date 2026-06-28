# tests/sierra_core/test_client_writes.py
import json
import pytest
from sierra_core.client import SierraHttpClient
from sierra_core.transport import FakeTransport
from sierra_core.errors import WriteNotAllowed, IdentityLockError


def env(inner): return json.dumps({"d": json.dumps(inner)})


# ---- Task 7: write methods + write gate --------------------------------

def test_write_blocked_when_readonly():
    c = SierraHttpClient(FakeTransport({}), site_id=4989, allow_write=False)
    with pytest.raises(WriteNotAllowed):
        c.add_content_label("X")


def test_add_content_label_returns_new_id():
    ft = FakeTransport({"/content-page-form.aspx/AddContentLabel":
                        env({"responseCode": 0, "data": {"contentLabelId": 19778, "pageContentLabelId": -1}})})
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    assert c.add_content_label("Test") == 19778
    _, body = ft.calls[0]
    assert body["name"] == "Test" and body["siteId"] == 4989


def test_save_html_widget_stringifies_widget_field():
    ft = FakeTransport({"/shared-html-widgets.aspx/SaveHtmlWidget":
                        env({"ResponseCode": 0, "Data": None, "Message": ""})})
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    c.save_html_widget({"id": -1, "title": "W", "content": "x"})
    _, body = ft.calls[0]
    assert isinstance(body["widget"], str) and json.loads(body["widget"])["title"] == "W"


def test_add_page_component_link_returns_link_id():
    ft = FakeTransport({"/content-page-form.aspx/AddPageComponentLink":
                        env({"responseCode": 0, "data": {"pageComponentId": 1155651}})})
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    assert c.add_page_component_link(218725, type=1, title="HTML", data="<p>x</p>") == 1155651


# ---- Task 8: identity-locked, snapshotting deletes --------------------

def _page_env(title):
    return json.dumps({"d": json.dumps(
        {"responseCode": 0, "data": {"page": {"id": 124195, "name": title}}}
    )})


def test_delete_content_page_identity_lock_blocks_mismatch():
    ft = FakeTransport({"/content-page-form.aspx/GetPage": _page_env("Real Title")})
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    snaps = []
    with pytest.raises(IdentityLockError):
        c.delete_content_page(124195, expected_title="WRONG", snapshot_sink=snaps.append)
    assert snaps == []  # nothing snapshotted, nothing deleted
    assert all("DeleteContentPage" not in p for p, _ in ft.calls)


def test_delete_content_page_snapshots_then_deletes_on_match():
    ft = FakeTransport({
        "/content-page-form.aspx/GetPage": _page_env("Real Title"),
        "/content-pages.aspx/DeleteContentPage": env({"responseCode": 0}),
    })
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    snaps = []
    out = c.delete_content_page(124195, expected_title="real title", snapshot_sink=snaps.append)
    assert out == {"deleted": 124195, "reversible": False}
    assert snaps and snaps[0]["page"]["id"] == 124195  # snapshot captured first
    assert any(p.endswith("/DeleteContentPage") for p, _ in ft.calls)


# ---- I3: delete_saved_search tests ------------------------------------

def _ss_env(title):
    """FakeTransport response for GetSavedSearchRecord returning a savedSearch dict."""
    return json.dumps({"d": json.dumps(
        {"responseCode": 0, "data": {"savedSearch": {"searchName": title, "id": 77}}}
    )})


def test_delete_saved_search_identity_lock_blocks_mismatch():
    ft = FakeTransport({"/lead-detail.aspx/GetSavedSearchRecord": _ss_env("My Search")})
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    snaps = []
    with pytest.raises(IdentityLockError):
        c.delete_saved_search(77, expected_title="WRONG", snapshot_sink=snaps.append)
    assert snaps == []  # nothing snapshotted, nothing deleted
    assert all("DeleteSavedSearch" not in p for p, _ in ft.calls)


def test_delete_saved_search_snapshots_then_deletes_on_match():
    ft = FakeTransport({
        "/lead-detail.aspx/GetSavedSearchRecord": _ss_env("My Search"),
        "/saved-searches.aspx/DeleteSavedSearch": env({"responseCode": 0}),
    })
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    snaps = []
    out = c.delete_saved_search(77, expected_title="my search", snapshot_sink=snaps.append)
    assert out == {"deleted": 77, "reversible": True}
    assert snaps and snaps[0]["searchName"] == "My Search"  # snapshot captured first
    delete_calls = [(p, b) for p, b in ft.calls if "DeleteSavedSearch" in p]
    assert len(delete_calls) == 1
    _, del_body = delete_calls[0]
    assert del_body["siteId"] == "4989"   # must be a STRING after fix I1
    assert del_body["savedSearchId"] == 77


# ---- Change 5: parametrized write-gate (read-only client blocks all mutators) ----

@pytest.mark.parametrize("call", [
    lambda c: c.add_content_label("x"),
    lambda c: c.update_content_label(1, "x"),
    lambda c: c.remove_content_label(1),
    lambda c: c.save_html_widget({}),
    lambda c: c.save_content_page({}, []),
    lambda c: c.update_page_component_title(1, "x"),
    lambda c: c.add_page_component_link(1, type=1, title="x"),
    lambda c: c.remove_page_component_link(1),
    lambda c: c.save_saved_search({}),
    lambda c: c.delete_content_page(1, expected_title="x", snapshot_sink=lambda r: None),
    lambda c: c.delete_saved_search(1, expected_title="x", snapshot_sink=lambda r: None),
], ids=[
    "add_content_label", "update_content_label", "remove_content_label",
    "save_html_widget", "save_content_page", "update_page_component_title",
    "add_page_component_link", "remove_page_component_link", "save_saved_search",
    "delete_content_page", "delete_saved_search",
])
def test_write_gate_readonly_client(call):
    """Each mutator must raise WriteNotAllowed before touching the transport."""
    ft = FakeTransport({})
    c = SierraHttpClient(ft, site_id=4989, allow_write=False)
    with pytest.raises(WriteNotAllowed):
        call(c)
    assert ft.calls == [], "write gate must fire before any transport call"
