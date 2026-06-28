"""Server smoke — import builds the ASGI app and registers the expected
tools + resources. No network: import does not log in (SierraRuntime is lazy)."""
import asyncio
import inspect

import sierra_mcp.server as server


def test_app_is_asgi_callable():
    app = server.app
    assert callable(app)
    # ASGI app exposes a 3-arg __call__(scope, receive, send).
    params = inspect.signature(app.__call__).parameters
    assert list(params) == ["scope", "receive", "send"]


def test_exactly_the_read_tools_registered():
    # M4: the registered set must EQUAL the 10 read tools. Adding any write/delete
    # tool to this layer would break this test and protect the read-only contract.
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == set(server.READ_TOOL_NAMES)
    assert len(server.READ_TOOL_NAMES) == 10
    # spot-check the documented gotcha tool + a get_*
    assert "list_saved_searches" in names
    assert "get_page" in names


def test_resources_registered():
    res = asyncio.run(server.mcp.list_resources())
    uris = {str(r.uri) for r in res}
    for uri in server.RESOURCE_URIS:
        assert uri in uris


def test_local_mode_has_no_auth():
    # conftest pins the hermetic local-dev state (no AUTHKIT_DOMAIN +
    # SIERRA_MCP_ALLOW_NO_AUTH=1), so the imported server runs auth-disabled.
    assert server.mcp.auth is None
