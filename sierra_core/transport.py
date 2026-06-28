from __future__ import annotations
import json
from typing import Protocol
import httpx

from sierra_core.errors import EndpointError

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SIERRA_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": USER_AGENT,
}

class Transport(Protocol):
    def post_json(self, path: str, body: dict) -> str: ...

class HttpxTransport:
    """Real transport: POSTs JSON to Sierra with the auth cookie jar + browser UA."""
    def __init__(self, base_url: str, cookies: dict, *, extra_headers: dict | None = None):
        headers = dict(SIERRA_HEADERS)
        if extra_headers:
            headers.update(extra_headers)
        self._client = httpx.Client(base_url=base_url, headers=headers,
                                    cookies=cookies, timeout=30.0)

    def post_json(self, path: str, body: dict) -> str:
        r = self._client.post(path, content=json.dumps(body))
        # Surface HTTP-level failures instead of swallowing them as "data" (#9).
        # 3xx is NOT is_error, so a 302 session-expiry redirect still flows through
        # as a body for the logout detector; only 4xx/5xx raise here.
        if r.is_error:
            raise EndpointError(f"HTTP {r.status_code}", raw=(r.text or "")[:300])
        return r.text

    def close(self) -> None:
        self._client.close()

class FakeTransport:
    """Test double: returns a pre-mapped response per path, records calls."""
    def __init__(self, responses: dict[str, str]):
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, path: str, body: dict) -> str:
        self.calls.append((path, body))
        if path in self._responses:
            return self._responses[path]
        return json.dumps({"d": json.dumps({"responseCode": 1, "message": f"no fake for {path}"})})

    def close(self) -> None:
        pass  # no-op; nothing to release
