import asyncio

from sierra_mcp import server


async def _ok_app(scope, receive, send):  # noqa: ANN001
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _run_asgi(app, *, client=("127.0.0.1", 4567)):
    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/probe",
        "raw_path": b"/probe",
        "query_string": b"",
        "headers": [],
        "client": client,
        "server": ("testserver", 80),
        "root_path": "",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    return messages


def _status(messages):
    for message in messages:
        if message["type"] == "http.response.start":
            return message["status"]
    raise AssertionError(f"no response start in {messages!r}")


def test_auth_disabled_blocks_non_loopback_client():
    assert server._AUTH_DISABLED is True
    app = server._LoopbackGuardMiddleware(_ok_app, auth_disabled=True)

    messages = _run_asgi(app, client=("203.0.113.10", 4567))

    assert _status(messages) == 403


def test_auth_disabled_allows_loopback_client():
    app = server._LoopbackGuardMiddleware(_ok_app, auth_disabled=True)

    assert _status(_run_asgi(app, client=("127.0.0.1", 4567))) == 204
    assert _status(_run_asgi(app, client=("::1", 4567))) == 204


def test_auth_enabled_allows_non_loopback_client():
    app = server._LoopbackGuardMiddleware(_ok_app, auth_disabled=False)

    messages = _run_asgi(app, client=("203.0.113.10", 4567))

    assert _status(messages) == 204
