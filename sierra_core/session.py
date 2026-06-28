# sierra_core/session.py
"""SessionBroker: pure-HTTP ASP.NET forms-auth, cookie cache, TTL, and manual
invalidation.

Cloud build: HTTP-login ONLY. The local dev tree additionally carries a headed
Chrome fallback (_headed_login) for resilience; the hosted server is browserless
by design (no Playwright/Chromium in the image), so the default login path here
is _http_login() and a failure raises SierraAuthError rather than launching a
browser. Phase-0 proved ASP.NET forms-auth works over plain httpx.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from sierra_core.errors import SierraAuthError


@dataclass
class Session:
    """Live Sierra session: auth cookies, tenant site_id, and admin base URL."""
    cookies: dict
    site_id: int
    base_url: str
    created_at: float = field(default=0.0)


# ---------------------------------------------------------------------------
# Login strategy (pure-HTTP)
# ---------------------------------------------------------------------------

LOGIN_URL = "https://client.sierrainteractivedev.com/login.aspx"


def _hidden(html: str, name: str) -> str:
    """Extract a hidden-field value from ASP.NET WebForms HTML."""
    m = (re.search(rf'id="{name}"\s+value="([^"]*)"', html)
         or re.search(rf'name="{name}"\s+value="([^"]*)"', html))
    return m.group(1) if m else ""


def _http_login() -> Session:
    """Pure-HTTP ASP.NET forms-auth login.

    Flow:
      1. GET login page → scrape __VIEWSTATE / __EVENTVALIDATION tokens.
      2. POST credentials; follow redirects to the per-tenant admin host.
      3. Derive base_url = scheme+host of the post-login URL.
      4. GET {base_url}/content-pages.aspx with auth cookies → regex siteId
         from the inline ``window.curUser`` JSON block.
    Raises SierraAuthError if login or site_id extraction fails.
    """
    import httpx
    from sierra_core.config import get_credentials
    from sierra_core.transport import USER_AGENT

    creds = get_credentials()
    headers = {"User-Agent": USER_AGENT}

    with httpx.Client(headers=headers, follow_redirects=True, timeout=30) as c:
        # Step 1: GET login page for ASP.NET hidden tokens.
        g = c.get(LOGIN_URL)
        if g.status_code != 200:
            raise SierraAuthError(f"GET login page returned {g.status_code}")

        form = {
            "__VIEWSTATE": _hidden(g.text, "__VIEWSTATE"),
            "__EVENTVALIDATION": _hidden(g.text, "__EVENTVALIDATION"),
            "__VIEWSTATEGENERATOR": _hidden(g.text, "__VIEWSTATEGENERATOR"),
            "txtSiteName": creds["site"],
            "txtUserName": creds["username"],
            "txtPassword": creds["password"],
            "btnLoginSubmit": "Login",
        }

        # Step 2: POST credentials, follow redirects.
        p = c.post(LOGIN_URL, data=form)
        final_url = str(p.url)
        if "login.aspx" in final_url.lower():
            raise SierraAuthError(
                f"Login failed: still at login page after POST (url={final_url})"
            )

        # Step 3: derive base_url from final redirect URL (HTTPS only).
        m = re.match(r"(https://[^/]+)", final_url)
        if not m:
            raise SierraAuthError(f"Cannot parse base_url from post-login URL: {final_url}")
        base_url = m.group(1)

        # Capture cookie jar as plain dict.
        cookies: dict = dict(c.cookies)

        # Step 4: GET an admin page to discover siteId from window.curUser.
        admin_page = c.get(f"{base_url}/content-pages.aspx")
        if admin_page.status_code != 200:
            raise SierraAuthError(
                f"GET content-pages.aspx returned {admin_page.status_code} "
                "(session may not have been established)"
            )

        # Anchor siteId extraction to window.curUser to avoid matching unrelated JSON.
        cur_user_match = re.search(
            r"window\.curUser\s*=\s*(\{[^;]+)", admin_page.text, re.DOTALL
        )
        if not cur_user_match:
            raise SierraAuthError(
                "Cannot find window.curUser on content-pages.aspx; "
                "login may have silently failed."
            )
        cur_user_text = cur_user_match.group(1)
        site_id_match = re.search(r'"siteId"\s*:\s*(\d+)', cur_user_text)
        if not site_id_match:
            site_id_match = re.search(r"'siteId'\s*:\s*(\d+)", cur_user_text)
        if not site_id_match:
            raise SierraAuthError(
                "Cannot find siteId in window.curUser on content-pages.aspx; "
                "login may have silently failed."
            )
        site_id = int(site_id_match.group(1))

    return Session(cookies=cookies, site_id=site_id, base_url=base_url)


# ---------------------------------------------------------------------------
# SessionBroker
# ---------------------------------------------------------------------------

class SessionBroker:
    """Owns the Sierra session: acquire (pluggable), cache, TTL, refresh.
    Isolates the fragile login from every caller.

    Default login_fn = _http_login (pure-HTTP, browserless).
    Inject a fake login_fn + fake clock in unit tests.
    """

    def __init__(
        self,
        login_fn: Optional[Callable[[], Session]] = None,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: int = 1800,
    ):
        self._login_fn = login_fn or _http_login
        self._clock = clock
        self._ttl = ttl_seconds
        self._session: Optional[Session] = None

    def get_session(self, force_refresh: bool = False) -> Session:
        now = self._clock()
        if (
            not force_refresh
            and self._session is not None
            and now - self._session.created_at < self._ttl
        ):
            return self._session
        s = self._login_fn()
        s.created_at = now
        self._session = s
        return s

    def invalidate(self) -> None:
        """Drop the cached session so the next get_session() re-authenticates."""
        self._session = None
