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


def test_registered_tools_equal_read_write_delete_generic_union():
    # The registered set must EQUAL read + write + delete + generic exactly. Adding
    # (or dropping) any tool breaks this test, keeping the layer's contract locked.
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    expected = (
        set(server.READ_TOOL_NAMES)
        | set(server.WRITE_TOOL_NAMES)
        | set(server.DELETE_TOOL_NAMES)
        | set(server.GENERIC_TOOL_NAMES)
    )
    assert names == expected
    assert len(server.READ_TOOL_NAMES) == 10
    assert len(server.WRITE_TOOL_NAMES) == 6
    assert len(server.DELETE_TOOL_NAMES) == 2
    assert len(server.GENERIC_TOOL_NAMES) == 1
    assert len(names) == 19
    # the four name groups must be pairwise disjoint (no tool double-counted)
    flat = [
        n
        for grp in (
            server.READ_TOOL_NAMES,
            server.WRITE_TOOL_NAMES,
            server.DELETE_TOOL_NAMES,
            server.GENERIC_TOOL_NAMES,
        )
        for n in grp
    ]
    assert len(flat) == len(set(flat)) == 19
    # spot-check representative tools
    assert {"list_saved_searches", "get_page"} <= names          # read
    assert "create_content_label" in names                       # write
    assert {"propose_deletions", "confirm_deletions"} <= names   # delete
    assert "sierra_call" in names                                 # generic


def test_registered_tools_have_chatgpt_action_annotations():
    """ChatGPT treats missing/null tool-impact hints as invalid app actions."""
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}

    for name in server.READ_TOOL_NAMES:
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.openWorldHint is False
        assert tools[name].annotations.destructiveHint is False

    non_destructive_writes = {
        "create_content_label",
        "update_content_label",
        "add_page_content_label_link",
        "update_page_component_title",
    }
    for name in non_destructive_writes:
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.openWorldHint is False
        assert tools[name].annotations.destructiveHint is False

    destructive_tools = {
        "remove_content_label",
        "remove_page_content_label_link",
        *server.DELETE_TOOL_NAMES,
    }
    for name in destructive_tools:
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.openWorldHint is False
        assert tools[name].annotations.destructiveHint is True

    assert tools["sierra_call"].annotations is not None
    assert tools["sierra_call"].annotations.readOnlyHint is False
    assert tools["sierra_call"].annotations.openWorldHint is True
    assert tools["sierra_call"].annotations.destructiveHint is True


def test_resources_registered():
    res = asyncio.run(server.mcp.list_resources())
    uris = {str(r.uri) for r in res}
    for uri in server.RESOURCE_URIS:
        assert uri in uris


def test_local_mode_has_no_auth():
    # conftest pins the hermetic local-dev state (no AUTHKIT_DOMAIN +
    # SIERRA_MCP_ALLOW_NO_AUTH=1), so the imported server runs auth-disabled.
    assert server.mcp.auth is None


def test_health_and_canonical_mcp_path():
    """W1-T6 (#16): /health is reachable; the canonical MCP endpoint is /mcp (no
    trailing slash, handled directly), and the /mcp/ form redirects to it — so docs
    should advertise /mcp (no extra hop)."""
    from starlette.testclient import TestClient

    with TestClient(server.app) as c:
        assert c.get("/health").status_code == 200
        # /mcp is handled directly: a bare GET yields 406 (not a 3xx redirect).
        assert c.get("/mcp", follow_redirects=False).status_code not in (301, 302, 307, 308)
        # the trailing-slash form redirects to the canonical /mcp.
        r = c.get("/mcp/", follow_redirects=False)
        assert r.status_code in (307, 308)
        assert r.headers["location"].rstrip("/").endswith("/mcp")
