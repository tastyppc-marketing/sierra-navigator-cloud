"""call_with_refresh / SierraRuntime — session-expiry re-auth + retry-once.

No real Sierra, no httpx: a fake broker + injected build_client_fn return fake
clients whose op raises a login-page EndpointError on the first client and
succeeds on the second."""
import pytest

from sierra_core.errors import EndpointError
from sierra_mcp.runtime import call_with_refresh, _looks_logged_out, SierraRuntime

# A realistic Sierra logout signal: unwrap_response got login-page HTML and
# raised EndpointError("json parse outer failed", raw=<html...>).
LOGIN_HTML = (
    "<!DOCTYPE html><html><head><title>Sign In</title></head><body>"
    "<form action='login.aspx'>"
    "<input type='hidden' name='__VIEWSTATE' value='abc'/>"
    "<input id='txtUserName' name='txtUserName'/>"
    "</form></body></html>"
)


class _TransportStub:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeClient:
    """Stands in for SierraHttpClient: exposes ._t.close() and a do() op target."""
    def __init__(self, behavior):
        self._behavior = behavior
        self._t = _TransportStub()

    def do(self):
        return self._behavior()


class FakeBroker:
    def __init__(self):
        self.sessions = [object(), object(), object()]
        self._next = 0
        self.get_calls = []   # records force_refresh + stale per get_session()
        self.invalidated = 0

    def get_session(self, force_refresh=False, *, stale=None):
        self.get_calls.append((force_refresh, stale))
        session = self.sessions[self._next]
        self._next += 1
        return session        # build_client_fn ignores the session here

    def invalidate(self):
        self.invalidated += 1


def make_build_fn(clients):
    seq = iter(clients)

    def build(session, *, allow_write=False):
        build.calls.append(allow_write)
        return next(seq)

    build.calls = []
    return build


def _raise_logout():
    raise EndpointError("json parse outer failed", raw=LOGIN_HTML)


def _raise_other():
    raise EndpointError("responseCode 5", raw={"responseCode": 5})


# ---- _looks_logged_out unit ----------------------------------------------

@pytest.mark.parametrize("raw", [
    LOGIN_HTML,
    "redirect to LOGIN.ASPX",
    "<input name='__VIEWSTATE'>",
    "txtUserName field present",
])
def test_looks_logged_out_true(raw):
    assert _looks_logged_out(EndpointError("x", raw=raw)) is True


@pytest.mark.parametrize("raw", [None, "", {"responseCode": 5}, "totally unrelated body"])
def test_looks_logged_out_false(raw):
    assert _looks_logged_out(EndpointError("x", raw=raw)) is False


# ---- call_with_refresh ----------------------------------------------------

def test_retries_exactly_once_on_logout_and_returns_good_result():
    broker = FakeBroker()
    c1 = FakeClient(_raise_logout)
    c2 = FakeClient(lambda: {"ok": True})
    build = make_build_fn([c1, c2])

    result = call_with_refresh(broker, lambda c: c.do(), build)

    assert result == {"ok": True}
    assert broker.invalidated == 0                  # stale session is threaded through
    assert broker.get_calls == [(False, None), (True, broker.sessions[0])]
    assert c1._t.closed is True                     # stale transport closed
    assert c2._t.closed is True                     # retry transport also closed
    assert build.calls == [False, False]            # both clients read-only


def test_non_logout_error_propagates_without_retry():
    broker = FakeBroker()
    c1 = FakeClient(_raise_other)
    build = make_build_fn([c1])

    with pytest.raises(EndpointError):
        call_with_refresh(broker, lambda c: c.do(), build)

    assert broker.invalidated == 0
    assert broker.get_calls == [(False, None)]      # never refreshed
    assert build.calls == [False]
    assert c1._t.closed is True                     # closed even on error (no leak)


def test_second_logout_failure_propagates():
    broker = FakeBroker()
    c1 = FakeClient(_raise_logout)
    c2 = FakeClient(_raise_logout)                  # retry also logged out
    build = make_build_fn([c1, c2])

    with pytest.raises(EndpointError):
        call_with_refresh(broker, lambda c: c.do(), build)

    assert broker.invalidated == 0
    assert broker.get_calls == [(False, None), (True, broker.sessions[0])]
    assert c1._t.closed is True and c2._t.closed is True  # both transports closed


def test_happy_path_closes_transport_no_refresh():
    broker = FakeBroker()
    c1 = FakeClient(lambda: {"rows": []})
    build = make_build_fn([c1])

    assert call_with_refresh(broker, lambda c: c.do(), build) == {"rows": []}
    assert broker.invalidated == 0
    assert broker.get_calls == [(False, None)]
    assert c1._t.closed is True                     # I1: closed on the happy path


# ---- I3: detection survives the real parsing path + raw truncation --------

def test_object_moved_redirect_detected_through_unwrap_response():
    """A realistic ASP.NET forms-auth expiry (302 'Object moved' body) fed through
    sierra_core.parsing.unwrap_response must raise EndpointError, and the
    login.aspx marker must survive parsing's 300-char raw truncation."""
    from sierra_core.parsing import unwrap_response

    body = (
        "<html><head><title>Object moved</title></head><body>"
        '<h2>Object moved to <a href="/login.aspx?ReturnUrl=%2fcontent-pages.aspx">'
        "here</a>.</h2></body></html>"
    )
    with pytest.raises(EndpointError) as ei:
        unwrap_response(body)

    err = ei.value
    # raw is truncated to 300 chars by parsing.py; the marker is well within it.
    assert len(str(err.raw)) <= 300
    assert _looks_logged_out(err) is True


# ---- SierraRuntime delegates with allow_write=False -----------------------

def test_runtime_read_uses_injected_broker_and_is_read_only():
    broker = FakeBroker()
    c1 = FakeClient(lambda: {"ok": 1})
    build = make_build_fn([c1])
    rt = SierraRuntime(broker=broker, build_client_fn=build)

    assert rt.read(lambda c: c.do()) == {"ok": 1}
    assert build.calls == [False]                   # never enables writes


def test_runtime_read_refreshes_on_logout():
    broker = FakeBroker()
    c1 = FakeClient(_raise_logout)
    c2 = FakeClient(lambda: {"ok": 2})
    build = make_build_fn([c1, c2])
    rt = SierraRuntime(broker=broker, build_client_fn=build)

    assert rt.read(lambda c: c.do()) == {"ok": 2}
    assert broker.invalidated == 0
    assert broker.get_calls == [(False, None), (True, broker.sessions[0])]
    assert build.calls == [False, False]
