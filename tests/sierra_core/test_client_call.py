# tests/sierra_core/test_client_call.py
"""Generic catalogued-endpoint caller: read-style works on a read-only client,
write=True is gated, and the body is posted verbatim."""
import json

import pytest

from sierra_core.client import SierraHttpClient
from sierra_core.transport import FakeTransport
from sierra_core.errors import WriteNotAllowed


def env(inner):
    return json.dumps({"d": json.dumps(inner)})


def test_call_read_style_works_on_readonly_client():
    ft = FakeTransport({"/content-pages.aspx/GetFilters":
                        env({"responseCode": 0, "data": {"sections": [], "labels": []}})})
    c = SierraHttpClient(ft, site_id=4989)  # allow_write defaults False
    out = c.call("/content-pages.aspx/GetFilters", {"siteId": 4989})
    assert out == {"sections": [], "labels": []}
    path, body = ft.calls[0]
    assert path == "/content-pages.aspx/GetFilters"
    assert body == {"siteId": 4989}  # passed through verbatim


def test_call_write_true_on_readonly_client_raises_and_posts_nothing():
    ft = FakeTransport({"/content-page-form.aspx/AddContentLabel":
                        env({"responseCode": 0, "data": {"contentLabelId": 1}})})
    c = SierraHttpClient(ft, site_id=4989)  # read-only
    with pytest.raises(WriteNotAllowed):
        c.call("/content-page-form.aspx/AddContentLabel", {"name": "X"}, write=True)
    assert ft.calls == []  # gate fired before any network post


def test_call_write_true_on_write_client_posts_body_verbatim():
    ft = FakeTransport({"/content-page-form.aspx/AddContentLabel":
                        env({"responseCode": 0, "data": {"contentLabelId": 5}})})
    c = SierraHttpClient(ft, site_id=4989, allow_write=True)
    body = {"name": "Lakefront", "pageId": -1, "extra": ["a", 1]}
    out = c.call("/content-page-form.aspx/AddContentLabel", body, write=True)
    assert out == {"contentLabelId": 5}
    path, sent = ft.calls[0]
    assert path == "/content-page-form.aspx/AddContentLabel"
    assert sent == body  # verbatim — no siteId coercion or mutation


def test_call_defaults_to_empty_body():
    ft = FakeTransport({"/x.aspx/GetThing": env({"responseCode": 0, "data": {"ok": True}})})
    c = SierraHttpClient(ft, site_id=4989)
    c.call("/x.aspx/GetThing")
    _, body = ft.calls[0]
    assert body == {}
