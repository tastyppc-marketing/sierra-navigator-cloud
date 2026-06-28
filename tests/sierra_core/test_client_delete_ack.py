"""W4-T3 (re-audit #9): a delete must require a POSITIVE Sierra acknowledgment.

delete_content_page / delete_saved_search previously discarded the response and
returned {"deleted": id} unconditionally — so at HTTP 200 a server fault (unwraps to
None), a business-rule rejection (a Message), or an empty body all reported a false
PASS and flipped the recovery ledger to "deleted". These tests pin the fix: only a
non-empty, error-marker-free payload counts as a delete.
"""
import json

import pytest

from sierra_core.client import SierraHttpClient
from sierra_core.errors import EndpointError
from sierra_core.transport import FakeTransport

SITE_ID = 5907
GETPAGE = "/content-page-form.aspx/GetPage"
DELPAGE = "/content-pages.aspx/DeleteContentPage"
GETSS = "/lead-detail.aspx/GetSavedSearchRecord"
DELSS = "/saved-searches.aspx/DeleteSavedSearch"


def env(inner: dict) -> str:
    return json.dumps({"d": json.dumps(inner)})


def ok(data) -> str:
    return env({"responseCode": 0, "data": data})


def _client(responses: dict) -> SierraHttpClient:
    return SierraHttpClient(FakeTransport(responses), site_id=SITE_ID, allow_write=True)


def _sink(_record):
    return 0


# ---- content page (IRREVERSIBLE) ---------------------------------------- #

def test_delete_content_page_accepts_positive_ack():
    c = _client({
        GETPAGE: ok({"page": {"id": 900, "name": "Home"}}),
        DELPAGE: ok({"deleted": True}),
    })
    assert c.delete_content_page(900, expected_title="Home", snapshot_sink=_sink) == {
        "deleted": 900, "reversible": False,
    }


@pytest.mark.parametrize("del_response,label", [
    ('{"Message":"boom","StackTrace":"at X.Y()"}', "non-d ASP.NET fault -> unwraps to None"),
    (env({"Message": "Cannot delete: page is referenced by a menu"}), "Message (no responseCode)"),
    (env({"responseCode": 0, "message": "Cannot delete: referenced by a published menu"}),
     "responseCode:0 business-rule message (preserved, not stripped)"),
])
def test_delete_content_page_raises_on_non_positive_ack(del_response, label):
    c = _client({
        GETPAGE: ok({"page": {"id": 900, "name": "Home"}}),
        DELPAGE: del_response,
    })
    with pytest.raises(EndpointError):
        c.delete_content_page(900, expected_title="Home", snapshot_sink=_sink)


def test_delete_accepts_bare_responsecode_zero():
    # Sierra's modeled delete success is a bare responseCode:0 (empty body after strip) —
    # accepted (matches the team's client fixtures). Only None / error-markers / a
    # business-rule message are failures.
    c = _client({
        GETPAGE: ok({"page": {"id": 900, "name": "Home"}}),
        DELPAGE: env({"responseCode": 0}),
        GETSS: ok({"savedSearch": {"id": 77, "searchName": "LF"}}),
        DELSS: env({"responseCode": 0}),
    })
    assert c.delete_content_page(900, expected_title="Home", snapshot_sink=_sink)["deleted"] == 900
    assert c.delete_saved_search(77, expected_title="LF", snapshot_sink=_sink)["deleted"] == 77


# ---- saved search (soft / reversible) ----------------------------------- #

def test_delete_saved_search_accepts_positive_ack():
    c = _client({
        GETSS: ok({"savedSearch": {"id": 77, "searchName": "Lakefront"}}),
        DELSS: ok({"deleted": True}),
    })
    assert c.delete_saved_search(77, expected_title="Lakefront", snapshot_sink=_sink) == {
        "deleted": 77, "reversible": True,
    }


def test_delete_saved_search_raises_on_business_rule_message():
    # A responseCode:0 that still carries a (now-preserved) business-rule message is a
    # soft rejection, not a delete — the SavedSearch+AgentsTeam coexistence rule class.
    c = _client({
        GETSS: ok({"savedSearch": {"id": 77, "searchName": "Lakefront"}}),
        DELSS: env({"responseCode": 0, "message": "Saved search is in use by an agent team"}),
    })
    with pytest.raises(EndpointError):
        c.delete_saved_search(77, expected_title="Lakefront", snapshot_sink=_sink)
