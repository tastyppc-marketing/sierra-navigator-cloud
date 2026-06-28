import json
import httpx
import pytest
from sierra_core.transport import FakeTransport, HttpxTransport, SIERRA_HEADERS, USER_AGENT
from sierra_core.errors import EndpointError

def test_fake_records_calls_and_returns_mapped_text():
    ft = FakeTransport({"/x/Get": json.dumps({"d": json.dumps({"responseCode": 0, "data": 1})})})
    out = ft.post_json("/x/Get", {"a": 1})
    assert json.loads(out)["d"]
    assert ft.calls == [("/x/Get", {"a": 1})]

def test_fake_unmapped_path_returns_error_envelope():
    ft = FakeTransport({})
    out = ft.post_json("/nope", {})
    inner = json.loads(json.loads(out)["d"])
    assert inner.get("responseCode", 0) != 0  # structured non-zero, not a substring match

def test_headers_constant_has_browser_ua_and_xhr():
    assert "Chrome/" in USER_AGENT
    assert SIERRA_HEADERS["X-Requested-With"] == "XMLHttpRequest"
    assert SIERRA_HEADERS["User-Agent"] == USER_AGENT


# --------------------------------------------------------------------------- #
# W1-T3 (#9): HTTP errors surface instead of being swallowed as "data"
# --------------------------------------------------------------------------- #

def _with_mock(t: HttpxTransport, handler) -> HttpxTransport:
    t._client.close()
    t._client = httpx.Client(base_url="https://x.test",
                             transport=httpx.MockTransport(handler))
    return t


def test_httpx_transport_raises_on_http_error():
    t = _with_mock(HttpxTransport("https://x.test", {}),
                   lambda req: httpx.Response(500, text="Internal Server Error"))
    with pytest.raises(EndpointError) as ei:
        t.post_json("/content-pages.aspx/DeleteContentPage", {})
    assert "HTTP 500" in str(ei.value)
    t.close()


def test_httpx_transport_returns_body_on_2xx():
    t = _with_mock(HttpxTransport("https://x.test", {}),
                   lambda req: httpx.Response(200, text='{"d":"{}"}'))
    assert t.post_json("/x.aspx/Foo", {}) == '{"d":"{}"}'
    t.close()
