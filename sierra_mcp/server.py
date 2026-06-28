"""Sierra Navigator — FastMCP (v3) server, Tier-1 read-only surface.

Exposes Sierra Interactive's admin backend to MCP clients behind WorkOS OAuth.
Every tool here is a **read**: it drives ``sierra_core`` through ``SierraRuntime``
(session broker + auto re-auth) with ``allow_write=False``. Two resources expose
the shipped endpoint catalogue. ``app`` is the ASGI entrypoint:

    uvicorn sierra_mcp.server:app --host 127.0.0.1 --port 8080   # MCP at /mcp/
"""
from __future__ import annotations

import json

from fastmcp import FastMCP
from starlette.responses import JSONResponse

from sierra_mcp import tools_read
from sierra_mcp.auth import build_auth
from sierra_mcp.catalogue import load_catalogue, verified_endpoints_markdown
from sierra_mcp.runtime import SierraRuntime

# --------------------------------------------------------------------------
# Server + runtime
# --------------------------------------------------------------------------

mcp = FastMCP(
    name="Sierra Navigator",
    instructions=(
        "Read-only access to a Sierra Interactive real-estate admin backend: "
        "content pages, saved searches, shared HTML widgets, blog posts, and "
        "the filter/label vocab. Use the list_* tools to discover ids, then the "
        "get_* tools to fetch a full record. The resource://sierra/endpoints* "
        "resources document the broader (not-yet-exposed) backend API surface."
    ),
    auth=build_auth(),
)

runtime = SierraRuntime()


# --------------------------------------------------------------------------
# Tier-1 read tools (one per sierra_mcp.tools_read shaper)
# --------------------------------------------------------------------------

@mcp.tool
def get_page(page_id: int) -> dict:
    """Fetch one content page by id.

    Returns ``{"record": {...}}`` with the page's full fields (name, url, status,
    components, ...). Use ``list_content_pages`` first to discover ids.
    """
    return runtime.read(lambda c: tools_read.get_page(c, page_id=page_id))


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
    return runtime.read(
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
    return runtime.read(
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
    return runtime.read(lambda c: tools_read.get_saved_search(c, search_id=search_id))


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
    return runtime.read(
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
    return runtime.read(lambda c: tools_read.get_widget(c, widget_id=widget_id))


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
    return runtime.read(
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
    return runtime.read(lambda c: tools_read.get_blog_post(c, post_id=post_id))


@mcp.tool
def get_filters() -> dict:
    """Get the content-page filter vocabulary.

    Returns ``{"sections": [...], "labels": [...]}`` — the section and content-label
    options used to filter ``list_content_pages``.
    """
    return runtime.read(lambda c: tools_read.get_filters(c))


@mcp.tool
def list_content_labels(sort_by: int = 1, sort_order: int = 0) -> dict:
    """List content labels (page taxonomy tags).

    Returns ``{"rows": [...], "count": N}`` with each label's id + name.
    """
    return runtime.read(
        lambda c: tools_read.list_content_labels(
            c, sort_by=sort_by, sort_order=sort_order
        )
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
# Resources — the shipped endpoint catalogue
# --------------------------------------------------------------------------

@mcp.resource("resource://sierra/endpoints", mime_type="application/json")
def sierra_endpoints() -> str:
    """The 642-endpoint Sierra admin XHR map, keyed by URL path (JSON)."""
    return json.dumps(load_catalogue().get("by_url", {}))


@mcp.resource("resource://sierra/endpoints/verified", mime_type="text/markdown")
def sierra_endpoints_verified() -> str:
    """Human-written reference of verified Sierra endpoints (Markdown)."""
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


# ASGI entrypoint. The MCP protocol is mounted at /mcp/ by default; /health is
# the custom route above. Must be built AFTER all tools/resources/routes register.
app = mcp.http_app()
