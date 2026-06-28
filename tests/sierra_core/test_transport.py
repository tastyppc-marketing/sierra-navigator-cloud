import json
from sierra_core.transport import FakeTransport, SIERRA_HEADERS, USER_AGENT

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
