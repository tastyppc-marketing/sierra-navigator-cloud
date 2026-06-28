"""Runtime glue between the FastMCP server and ``sierra_core``.

Owns a :class:`~sierra_core.session.SessionBroker`, builds a fresh
:class:`~sierra_core.client.SierraHttpClient` per operation, and transparently
re-authenticates + retries **once** when Sierra's HTTP path hands back the
login page (the session-expiry signal). Tier-1 is read-only: ``read()`` always
constructs the client with ``allow_write=False``.
"""
from __future__ import annotations

from typing import Callable

from sierra_core.session import SessionBroker, Session, _http_login
from sierra_core.transport import HttpxTransport
from sierra_core.client import SierraHttpClient
from sierra_core.errors import EndpointError

# Markers that betray a Sierra login page coming back in place of JSON — i.e. the
# session has expired and we were silently redirected to /login.aspx. Matched
# case-insensitively against EndpointError.raw.
_LOGOUT_MARKERS = ("login.aspx", "__viewstate", "txtusername")


def build_client(session: Session, *, allow_write: bool = False) -> SierraHttpClient:
    """Construct a SierraHttpClient bound to ``session``'s cookies + site_id."""
    transport = HttpxTransport(session.base_url, session.cookies)
    return SierraHttpClient(transport, site_id=session.site_id, allow_write=allow_write)


def _safe_close(client: SierraHttpClient) -> None:
    """Best-effort transport close that never raises (so it can't mask an error)."""
    try:
        client._t.close()
    except Exception:
        pass


def _looks_logged_out(err: EndpointError) -> bool:
    """True when an EndpointError's raw payload looks like the Sierra login page.

    On HTTP, an expired session yields login-page HTML; ``unwrap_response`` then
    raises ``EndpointError("json parse outer failed", raw=<html>)``. We detect
    that by sniffing the raw text for login-form markers.
    """
    raw = str(getattr(err, "raw", "") or "").lower()
    return any(marker in raw for marker in _LOGOUT_MARKERS)


def call_with_refresh(
    broker: SessionBroker,
    op: Callable[[SierraHttpClient], object],
    build_client_fn: Callable[..., SierraHttpClient] = build_client,
    *,
    allow_write: bool = False,
) -> object:
    """Run ``op(client)`` against a live session, re-authenticating once on expiry.

    ``op`` is a callable ``(client) -> result``. If it raises an ``EndpointError``
    that looks like a logged-out redirect, the broker is invalidated +
    force-refreshed and ``op`` is retried exactly once on a fresh client. A second
    failure (or any non-logout EndpointError) propagates unchanged.

    The per-request transport is ALWAYS closed after use (success, retry, or
    error) so the long-running server never leaks httpx connection pools / FDs.
    """
    sess = broker.get_session()
    client = build_client_fn(sess, allow_write=allow_write)
    try:
        return op(client)
    except EndpointError as e:
        if not _looks_logged_out(e):
            raise
        # Logged out — fall through to the one-shot refresh + retry below.
    finally:
        _safe_close(client)  # close on success, on re-raise, and before retry

    # Session expired: invalidate, re-auth, retry exactly once on a fresh client.
    broker.invalidate()
    sess = broker.get_session(force_refresh=True)
    client2 = build_client_fn(sess, allow_write=allow_write)
    try:
        return op(client2)  # a second failure here propagates
    finally:
        _safe_close(client2)


class SierraRuntime:
    """Process-wide handle: one SessionBroker, read-only client construction.

    Tests inject a fake ``broker`` and ``build_client_fn``; production uses the
    default browserless ``_http_login`` broker.
    """

    def __init__(
        self,
        broker: SessionBroker | None = None,
        build_client_fn: Callable[..., SierraHttpClient] = build_client,
    ):
        self.broker = broker or SessionBroker(login_fn=_http_login)
        self._build_client = build_client_fn

    def read(self, op: Callable[[SierraHttpClient], object]) -> object:
        """Run a read operation with session-refresh, never enabling writes."""
        return call_with_refresh(
            self.broker, op, self._build_client, allow_write=False
        )
