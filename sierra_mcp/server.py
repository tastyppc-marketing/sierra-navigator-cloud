"""Sierra Navigator — FastMCP (v3) server, Tier-1 read-only surface.

Exposes Sierra Interactive's admin backend to MCP clients behind WorkOS OAuth.
Every tool here is a **read**: it drives ``sierra_core`` through ``SierraRuntime``
(session broker + auto re-auth) with ``allow_write=False``. Two resources expose
the shipped endpoint catalogue. ``app`` is the ASGI entrypoint:

    python -m sierra_mcp.server   # the ONLY supported launcher (MCP at /mcp)

Launch ONLY via ``python -m sierra_mcp.server`` (the container CMD): it binds
``SIERRA_MCP_BIND_HOST`` — the SAME value the no-auth gate checks — so the socket can't
diverge from the gate. Do NOT run ``uvicorn sierra_mcp.server:app --host <X>`` directly:
its ``--host`` binds independently of that env var, so a no-auth dev config could bind a
public interface behind a gate that still thinks it's loopback (re-audit #4 deploy-exposure).
"""
from __future__ import annotations

import json

from fastmcp import FastMCP
from starlette.responses import JSONResponse

from sierra_mcp import context, tools_generic, tools_read, tools_write
from sierra_mcp.auth import build_auth
from sierra_mcp.catalogue import load_catalogue, verified_endpoints_markdown

# --------------------------------------------------------------------------
# Server + runtime
# --------------------------------------------------------------------------

_AUTH_PROVIDER = build_auth()
_AUTH_DISABLED = _AUTH_PROVIDER is None

mcp = FastMCP(
    name="Sierra Navigator",
    instructions=(
        "Access to a Sierra Interactive real-estate admin backend: content pages, "
        "saved searches, shared HTML widgets, blog posts, and the filter/label "
        "vocab. READS: use the list_* tools to discover ids, then the get_* tools "
        "for a full record. WRITES + DELETES are two-step: call the tool once with "
        "no confirm_token to PREVIEW (nothing is sent to Sierra; you get a "
        "confirm_token), then call again with that token to COMMIT. Deletes are "
        "identity-locked: propose_deletions returns each record's stored title, "
        "which you must echo back as expected_title in confirm_deletions. Deleting "
        "a content page is IRREVERSIBLE. The resource://sierra/endpoints* resources "
        "document the broader (not-yet-exposed) backend API surface."
    ),
    auth=_AUTH_PROVIDER,
    # Don't leak raw internal exception text (sqlite/thread/parse internals) to
    # connected MCP clients; tools return curated errors instead (#17).
    mask_error_details=True,
)

# One shared runtime (one SessionBroker / one Sierra login) for reads + writes.
runtime = context.get_runtime()


def _guarded_read(tool: str, fn):
    """Enforce per-identity access on a Tier-1 read, then run it.

    Every read goes through ``context.authorize`` (subject allowlist + ``read`` scope,
    auditing any denial) BEFORE hitting Sierra — closing New-3, where the read tools
    called ``runtime.read`` directly and so skipped the allowlist that writes/deletes and
    the ``sierra_call`` read path already enforce.
    """
    context.authorize(context.get_conn(), tool=tool, action="read", scope="read")
    return runtime.read(fn)


# --------------------------------------------------------------------------
# Tier-1 read tools (one per sierra_mcp.tools_read shaper)
# --------------------------------------------------------------------------

@mcp.tool
def get_page(page_id: int) -> dict:
    """Fetch one content page by id.

    Returns ``{"record": {...}}`` with the page's full fields (name, url, status,
    components, ...). Use ``list_content_pages`` first to discover ids.
    """
    return _guarded_read("get_page", lambda c: tools_read.get_page(c, page_id=page_id))


@mcp.tool
def list_content_pages(
    sort_by: int = 2,
    sort_direction: int = 0,
    section_id: int = -1,
    content_label_id: int = -1,
    status_id: int = -1,
    search_term: str = "",
    page_num: int = 1,
    page_size: int = 500,
) -> dict:
    """List content (CMS) pages for the site.

    Filter with ``search_term`` (name match), ``section_id``, ``content_label_id``,
    ``status_id`` (all -1 = no filter). Returns ``{"rows": [...], "count": N}`` plus
    ``total`` / ``has_more`` when Sierra reports them; each row carries the page id +
    metadata. ``page_size`` defaults to 500 (the Sierra server-side cap).
    """
    return _guarded_read(
        "list_content_pages",
        lambda c: tools_read.list_content_pages(
            c,
            sort_by=sort_by,
            sort_direction=sort_direction,
            section_id=section_id,
            content_label_id=content_label_id,
            status_id=status_id,
            search_term=search_term,
            page_num=page_num,
            page_size=page_size,
        )
    )


@mcp.tool
def list_saved_searches(
    sort_by: int = 4,
    sort_direction: int = 0,
    search_term: str = "",
    favorite_filter: int = 1,
    page_num: int = 1,
    page_size: int = 5000,
) -> dict:
    """List saved property searches.

    ``search_term`` matches the saved-search name. Returns
    ``{"rows": [...], "count": N}`` (rows include the saved-search id + name).
    """
    return _guarded_read(
        "list_saved_searches",
        lambda c: tools_read.list_saved_searches(
            c,
            sort_by=sort_by,
            sort_direction=sort_direction,
            search_term=search_term,
            favorite_filter=favorite_filter,
            page_num=page_num,
            page_size=page_size,
        )
    )


@mcp.tool
def get_saved_search(search_id: int) -> dict:
    """Fetch one saved search by id.

    Returns ``{"record": {...}}`` with the search's name and full filter criteria.
    Discover ids via ``list_saved_searches``.
    """
    return _guarded_read("get_saved_search", lambda c: tools_read.get_saved_search(c, search_id=search_id))


@mcp.tool
def list_html_widgets(
    sort_by: int = 1,
    sort_direction: int = 1,
    widget_type: int = -1,
    search_term: str = "",
    page_num: int = 1,
    page_size: int = 100,
) -> dict:
    """List shared HTML widgets (reusable content/code snippets).

    ``widget_type`` -1 = all (1 = component, 4 = code). ``search_term`` matches the
    widget title. Returns ``{"rows": [...], "count": N}``.
    """
    return _guarded_read(
        "list_html_widgets",
        lambda c: tools_read.list_html_widgets(
            c,
            sort_by=sort_by,
            sort_direction=sort_direction,
            widget_type=widget_type,
            search_term=search_term,
            page_num=page_num,
            page_size=page_size,
        )
    )


@mcp.tool
def get_widget(widget_id: int) -> dict:
    """Fetch one shared HTML widget by id.

    Returns ``{"record": {...}}`` including the widget's title, type, and HTML/JS
    body. Discover ids via ``list_html_widgets``.
    """
    return _guarded_read("get_widget", lambda c: tools_read.get_widget(c, widget_id=widget_id))


@mcp.tool
def list_blog_posts(
    sort_by: int = 1,
    sort_direction: int = 1,
    category_id: int = -1,
    author_id: int = -1,
    tag_id: int = -1,
    search_term: str = "",
    page_num: int = 1,
    page_size: int = 50,
) -> dict:
    """List blog posts.

    Filter by ``category_id`` / ``author_id`` / ``tag_id`` (-1 = no filter) and
    ``search_term`` (title match). Returns ``{"rows": [...], "count": N}`` plus
    ``total`` / ``has_more`` when Sierra reports them. ``page_size`` defaults to 50
    (the Sierra blog-manager page cap).
    """
    return _guarded_read(
        "list_blog_posts",
        lambda c: tools_read.list_blog_posts(
            c,
            sort_by=sort_by,
            sort_direction=sort_direction,
            category_id=category_id,
            author_id=author_id,
            tag_id=tag_id,
            search_term=search_term,
            page_num=page_num,
            page_size=page_size,
        )
    )


@mcp.tool
def get_blog_post(post_id: int) -> dict:
    """Fetch one blog post by id.

    Returns ``{"record": {...}}`` with the post title, body, and metadata.
    Discover ids via ``list_blog_posts``.
    """
    return _guarded_read("get_blog_post", lambda c: tools_read.get_blog_post(c, post_id=post_id))


@mcp.tool
def get_filters() -> dict:
    """Get the content-page filter vocabulary.

    Returns ``{"sections": [...], "labels": [...]}`` — the section and content-label
    options used to filter ``list_content_pages``.
    """
    return _guarded_read("get_filters", lambda c: tools_read.get_filters(c))


@mcp.tool
def list_content_labels(sort_by: int = 1, sort_order: int = 0) -> dict:
    """List content labels (page taxonomy tags).

    Returns ``{"rows": [...], "count": N}`` with each label's id + name.
    """
    return _guarded_read(
        "list_content_labels",
        lambda c: tools_read.list_content_labels(
            c, sort_by=sort_by, sort_order=sort_order
        ),
    )


# Authoritative list of the read tool names registered above (used by tests
# and any caller that wants the Tier-1 surface without async introspection).
READ_TOOL_NAMES: tuple[str, ...] = (
    "get_page",
    "list_content_pages",
    "list_saved_searches",
    "get_saved_search",
    "list_html_widgets",
    "get_widget",
    "list_blog_posts",
    "get_blog_post",
    "get_filters",
    "list_content_labels",
)


# --------------------------------------------------------------------------
# Guarded write tools (two-step preview -> commit; scope "write")
#
# Each call with confirm_token=None PREVIEWS (mints a token, sends nothing to
# Sierra). Call again with the returned token to COMMIT.
# --------------------------------------------------------------------------

@mcp.tool
def create_content_label(name: str, page_id: int = -1, confirm_token: str | None = None) -> dict:
    """Create a content label (optionally pre-linked to ``page_id``).

    Two-step: omit ``confirm_token`` to preview + get a token, then call again
    with it to commit. Returns the new label id on commit.
    """
    return tools_write.create_content_label(name, page_id=page_id, confirm_token=confirm_token)


@mcp.tool
def update_content_label(content_label_id: int, name: str, confirm_token: str | None = None) -> dict:
    """Rename an existing content label. Two-step preview -> commit."""
    return tools_write.update_content_label(content_label_id, name, confirm_token=confirm_token)


@mcp.tool
def remove_content_label(content_label_id: int, confirm_token: str | None = None) -> dict:
    """Delete a content label by id. Two-step preview -> commit."""
    return tools_write.remove_content_label(content_label_id, confirm_token=confirm_token)


@mcp.tool
def add_page_content_label_link(
    page_id: int, content_label_id: int, confirm_token: str | None = None
) -> dict:
    """Link an existing content label to a content page. Two-step preview -> commit."""
    return tools_write.add_page_content_label_link(
        page_id, content_label_id, confirm_token=confirm_token
    )


@mcp.tool
def remove_page_content_label_link(
    page_id: int, content_label_id: int, confirm_token: str | None = None
) -> dict:
    """Unlink a content label from a content page. Two-step preview -> commit."""
    return tools_write.remove_page_content_label_link(
        page_id, content_label_id, confirm_token=confirm_token
    )


@mcp.tool
def update_page_component_title(
    component_link_id: int, title: str, confirm_token: str | None = None
) -> dict:
    """Rename a page component (by its component-link id). Two-step preview -> commit."""
    return tools_write.update_page_component_title(
        component_link_id, title, confirm_token=confirm_token
    )


WRITE_TOOL_NAMES: tuple[str, ...] = (
    "create_content_label",
    "update_content_label",
    "remove_content_label",
    "add_page_content_label_link",
    "remove_page_content_label_link",
    "update_page_component_title",
)


# --------------------------------------------------------------------------
# Identity-locked delete tools (two-step propose -> confirm; scope "delete")
# --------------------------------------------------------------------------

@mcp.tool
def propose_deletions(entity_type: str, ids: list[int], confirm_token: str | None = None) -> dict:
    """PREVIEW a batch delete (step 1 of 2). Sends nothing destructive.

    ``entity_type`` is ``"content_page"`` (HARD, IRREVERSIBLE delete) or
    ``"saved_search"`` (soft, recoverable). Live-fetches each id and returns
    ``candidates`` with each record's ``stored_title`` + ``reversible`` flag, plus
    a ``confirm_token``. To delete, call ``confirm_deletions`` echoing each
    ``stored_title`` back as ``expected_title``.
    """
    return tools_write.propose_deletions(entity_type, ids, confirm_token=confirm_token)


@mcp.tool
def confirm_deletions(confirm_token: str, entity_type: str, confirmations: list[dict]) -> dict:
    """COMMIT a proposed batch delete (step 2 of 2), identity-locked.

    ``confirmations`` is ``[{"id": <int>, "expected_title": <str>}, ...]`` — the
    exact id-set from ``propose_deletions`` (a different set is rejected). Each
    row is deleted only if ``expected_title`` matches the live stored title; a
    mismatch aborts THAT row (marked ``ABORTED``) without failing the batch.
    Content-page deletes are IRREVERSIBLE. A recovery snapshot is written to the
    ledger before each delete.
    """
    return tools_write.confirm_deletions(confirm_token, entity_type, confirmations)


DELETE_TOOL_NAMES: tuple[str, ...] = (
    "propose_deletions",
    "confirm_deletions",
)


# --------------------------------------------------------------------------
# Tier-2 generic caller (the guarded escape hatch over the whole catalogue)
# --------------------------------------------------------------------------

@mcp.tool
def sierra_call(path: str, body: dict | None = None, confirm_token: str | None = None) -> dict:
    """Generic caller for ANY catalogued Sierra endpoint (Tier-2 escape hatch).

    Use this only when no dedicated tool exists for what you need. ``path`` MUST be
    one of the catalogued endpoints — read the ``resource://sierra/endpoints``
    resource for valid paths, and ``resource://sierra/endpoints/verified`` for the
    documented request bodies. The ``body`` is sent to Sierra VERBATIM (you are
    responsible for the exact field shapes, including the per-endpoint string-vs-int
    ``siteId`` quirk).

    Scope + behaviour are inferred from the endpoint's method name:
    - READ (Get/List/Find/Check/Load/Validate/Search/Count*) executes immediately
      and returns ``{"mode": "called", "path", "result"}``.
    - WRITE / DELETE (Add/Update/Save/Remove/Set/… or Delete*) is a TWO-STEP
      guarded mutation: call once WITHOUT ``confirm_token`` to PREVIEW (mints a
      token, sends nothing), then call again WITH that token to COMMIT. Same audit
      trail + volume caps as the dedicated write tools.

    REFUSED here: entity deletes (``DeleteContentPage`` / ``DeleteSavedSearch``) and
    any ``Duplicate*`` op — those bypass the identity lock, so use
    ``propose_deletions`` / ``confirm_deletions`` instead. An un-catalogued path is
    rejected.
    """
    return tools_generic.sierra_call(path, body, confirm_token=confirm_token)


GENERIC_TOOL_NAMES: tuple[str, ...] = (
    "sierra_call",
)


# --------------------------------------------------------------------------
# Resources — the shipped endpoint catalogue
# --------------------------------------------------------------------------

@mcp.resource("resource://sierra/endpoints", mime_type="application/json")
def sierra_endpoints() -> str:
    """The 642-endpoint Sierra admin XHR map, keyed by URL path (JSON)."""
    # Gate the catalogue behind the same subject allowlist + read scope as the read tools,
    # auditing denials — resources skipped context.authorize and so were readable by any
    # authenticated-but-unauthorized caller (re-audit #5 MEDIUM).
    context.authorize(context.get_conn(), tool="resource:endpoints", action="read", scope="read")
    return json.dumps(load_catalogue().get("by_url", {}))


@mcp.resource("resource://sierra/endpoints/verified", mime_type="text/markdown")
def sierra_endpoints_verified() -> str:
    """Human-written reference of verified Sierra endpoints (Markdown)."""
    context.authorize(
        context.get_conn(), tool="resource:endpoints/verified", action="read", scope="read"
    )
    return verified_endpoints_markdown()


RESOURCE_URIS: tuple[str, ...] = (
    "resource://sierra/endpoints",
    "resource://sierra/endpoints/verified",
)


# --------------------------------------------------------------------------
# Health route + ASGI app
# --------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(request):  # noqa: ANN001 - Starlette Request
    """Liveness probe for the VPS Caddy / container orchestrator."""
    return JSONResponse({"status": "ok"})


_LOOPBACK_CLIENT_HOSTS = {"127.0.0.1", "::1"}
# Starlette's TestClient uses this in-process sentinel instead of a socket host.
_IN_PROCESS_CLIENT_HOSTS = {"testclient"}


class _LoopbackGuardMiddleware:
    def __init__(self, app, *, auth_disabled: bool):  # noqa: ANN001
        self.app = app
        self.auth_disabled = auth_disabled

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if self.auth_disabled and scope["type"] == "http":
            client = scope.get("client")
            if (
                client is not None
                and client[0] not in _LOOPBACK_CLIENT_HOSTS
                and client[0] not in _IN_PROCESS_CLIENT_HOSTS
            ):
                response = JSONResponse(
                    {"error": "auth_disabled_loopback_only"},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ASGI entrypoint. The MCP protocol is mounted at /mcp by default; /health is
# the custom route above. Must be built AFTER all tools/resources/routes register.
app = _LoopbackGuardMiddleware(mcp.http_app(), auth_disabled=_AUTH_DISABLED)


# --------------------------------------------------------------------------
# Container / console entrypoint
# --------------------------------------------------------------------------

def main() -> None:
    """Run the server under uvicorn, binding the host from ``SIERRA_MCP_BIND_HOST``.

    Critically, the bind host comes from :func:`sierra_mcp.auth.resolved_bind_host` —
    the SAME value ``build_auth()``'s loopback gate checks — so a no-auth dev config
    (``SIERRA_MCP_ALLOW_NO_AUTH=1 + SIERRA_MCP_BIND_HOST=127.0.0.1``) can never bind a
    network-reachable socket behind the gate's back (#4/#13). Port from
    ``SIERRA_MCP_PORT`` (default 8080). The container CMD is ``python -m
    sierra_mcp.server`` so this is the single, authoritative bind site.
    """
    import os

    import uvicorn

    from sierra_mcp.auth import resolved_bind_host

    host = resolved_bind_host()
    port = int((os.environ.get("SIERRA_MCP_PORT") or "8080").strip())
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
