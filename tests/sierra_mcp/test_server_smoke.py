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


def test_registered_tools_equal_read_write_delete_union():
    # The registered set must EQUAL read + write + delete exactly. Adding (or
    # dropping) any tool breaks this test, keeping the layer's contract locked.
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    expected = (
        set(server.READ_TOOL_NAMES)
        | set(server.WRITE_TOOL_NAMES)
        | set(server.DELETE_TOOL_NAMES)
    )
    assert names == expected
    assert len(server.READ_TOOL_NAMES) == 10
    assert len(server.WRITE_TOOL_NAMES) == 6
    assert len(server.DELETE_TOOL_NAMES) == 2
    assert len(names) == 18
    # the three sets must be disjoint (no tool double-counted)
    assert not (set(server.READ_TOOL_NAMES) & set(server.WRITE_TOOL_NAMES))
    assert not (set(server.WRITE_TOOL_NAMES) & set(server.DELETE_TOOL_NAMES))
    # spot-check representative tools
    assert {"list_saved_searches", "get_page"} <= names          # read
    assert "create_content_label" in names                       # write
    assert {"propose_deletions", "confirm_deletions"} <= names   # delete


def test_resources_registered():
    res = asyncio.run(server.mcp.list_resources())
    uris = {str(r.uri) for r in res}
    for uri in server.RESOURCE_URIS:
        assert uri in uris


def test_local_mode_has_no_auth():
    # conftest pins the hermetic local-dev state (no AUTHKIT_DOMAIN +
    # SIERRA_MCP_ALLOW_NO_AUTH=1), so the imported server runs auth-disabled.
    assert server.mcp.auth is None
