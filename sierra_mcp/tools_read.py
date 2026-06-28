"""Pure read shapers — turn a SierraHttpClient's raw payloads into clean,
LLM-friendly result dicts. No FastMCP dependency; ``server.py`` wraps these.

Conventions
-----------
* List reads return ``{"rows": [...], "count": <int>}``. The source key varies
  by endpoint (Sierra is inconsistent), so each shaper tries the documented
  candidate keys in order. Paginated lists additionally surface ``total`` and
  ``has_more`` when derivable.
* Single-record reads return ``{"record": {...}}``.
* Everything is defensive: missing/oddly-typed payloads degrade to empty.
"""
from __future__ import annotations

from typing import Any

from sierra_core.client import SierraHttpClient


def _rows(payload: Any, *keys: str) -> list:
    """First list-valued entry among ``keys`` in ``payload`` (else [])."""
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
    elif isinstance(payload, list):
        return payload
    return []


def _record(payload: Any, *keys: str) -> dict:
    """Extract a single record.

    With ``keys``: the first dict-valued nested entry (e.g. ``payload["page"]``).
    Without ``keys``: ``payload`` itself when it is already the record dict.
    """
    if keys:
        if isinstance(payload, dict):
            for k in keys:
                v = payload.get(k)
                if isinstance(v, dict):
                    return v
        return {}
    return payload if isinstance(payload, dict) else {}


def _listed(rows: list) -> dict:
    """Shape a non-paginated list (no total/has_more concept)."""
    return {"rows": rows, "count": len(rows)}


# Candidate keys Sierra uses for a result total. Record-count keys are preferred
# over ``totalPages`` (a different unit) when more than one is present.
_TOTAL_KEYS = ("totalRecords", "recordCount", "totalPages")


def _paged_list(payload: Any, rows: list, *, page_num: int, page_size: int) -> dict:
    """Shape a paginated list, surfacing Sierra's total + a derived ``has_more``.

    * ``total`` — first present int among ``_TOTAL_KEYS`` (omitted if none found).
    * ``has_more`` — derived from ``total`` when known (page- vs. record-count
      semantics handled), else a heuristic: a full page
      (``len(rows) == page_size``) implies there may be more.
    """
    out: dict = {"rows": rows, "count": len(rows)}
    total: int | None = None
    total_is_pages = False
    if isinstance(payload, dict):
        for k in _TOTAL_KEYS:
            v = payload.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                total = v
                total_is_pages = k == "totalPages"
                break
    if total is not None:
        out["total"] = total
        if total_is_pages:
            out["has_more"] = page_num < total
        elif page_size and page_size > 0:
            out["has_more"] = total > page_num * page_size
    elif page_size and page_size > 0:
        out["has_more"] = len(rows) == page_size
    return out


# ---- content pages -------------------------------------------------------

def get_page(client: SierraHttpClient, *, page_id: int | str) -> dict:
    """A single content page record (nested under ``page``)."""
    return {"record": _record(client.get_page(page_id), "page")}


def list_content_pages(
    client: SierraHttpClient,
    *,
    sort_by: int = 2,
    sort_direction: int = 0,
    section_id: int = -1,
    content_label_id: int = -1,
    status_id: int = -1,
    search_term: str = "",
    page_num: int = 1,
    page_size: int = 500,
) -> dict:
    """List content pages (rows from ``contentPages`` / ``pages``)."""
    r = client.list_content_pages(
        sort_by=sort_by,
        sort_direction=sort_direction,
        section_id=section_id,
        content_label_id=content_label_id,
        status_id=status_id,
        search_term=search_term,
        page_num=page_num,
        page_size=page_size,
    )
    rows = _rows(r, "contentPages", "pages")
    return _paged_list(r, rows, page_num=page_num, page_size=page_size)


# ---- saved searches ------------------------------------------------------

def list_saved_searches(
    client: SierraHttpClient,
    *,
    sort_by: int = 4,
    sort_direction: int = 0,
    search_term: str = "",
    favorite_filter: int = 1,
    page_num: int = 1,
    page_size: int = 5000,
) -> dict:
    """List saved searches (rows from ``items``)."""
    r = client.list_saved_searches(
        sort_by=sort_by,
        sort_direction=sort_direction,
        search_term=search_term,
        favorite_filter=favorite_filter,
        page_num=page_num,
        page_size=page_size,
    )
    rows = _rows(r, "items")
    return _paged_list(r, rows, page_num=page_num, page_size=page_size)


def get_saved_search(client: SierraHttpClient, *, search_id: int | str) -> dict:
    """A single saved-search record (already unwrapped by the client)."""
    return {"record": _record(client.get_saved_search(search_id))}


# ---- HTML widgets --------------------------------------------------------

def list_html_widgets(
    client: SierraHttpClient,
    *,
    sort_by: int = 1,
    sort_direction: int = 1,
    widget_type: int = -1,
    search_term: str = "",
    page_num: int = 1,
    page_size: int = 100,
) -> dict:
    """List shared HTML widgets (rows from ``widgets`` / ``htmlWidgets`` / ``items``)."""
    r = client.list_html_widgets(
        sort_by=sort_by,
        sort_direction=sort_direction,
        widget_type=widget_type,
        search_term=search_term,
        page_num=page_num,
        page_size=page_size,
    )
    rows = _rows(r, "widgets", "htmlWidgets", "items")
    return _paged_list(r, rows, page_num=page_num, page_size=page_size)


def get_widget(client: SierraHttpClient, *, widget_id: int | str) -> dict:
    """A single HTML widget record (already unwrapped by the client)."""
    return {"record": _record(client.get_widget(widget_id))}


# ---- blog ----------------------------------------------------------------

def list_blog_posts(
    client: SierraHttpClient,
    *,
    sort_by: int = 1,
    sort_direction: int = 1,
    category_id: int = -1,
    author_id: int = -1,
    tag_id: int = -1,
    search_term: str = "",
    page_num: int = 1,
    page_size: int = 50,
) -> dict:
    """List blog posts (rows from ``posts`` / ``blogPosts``)."""
    r = client.list_blog_posts(
        sort_by=sort_by,
        sort_direction=sort_direction,
        category_id=category_id,
        author_id=author_id,
        tag_id=tag_id,
        search_term=search_term,
        page_num=page_num,
        page_size=page_size,
    )
    rows = _rows(r, "posts", "blogPosts")
    return _paged_list(r, rows, page_num=page_num, page_size=page_size)


def get_blog_post(client: SierraHttpClient, *, post_id: int | str) -> dict:
    """A single blog post record.

    Live ``GetBlogPostInfo`` nests the record under ``blogPost``; ``post`` is kept
    as a fallback for older/alternate payload shapes.
    """
    return {"record": _record(client.get_blog_post(post_id), "blogPost", "post")}


# ---- filters & labels ----------------------------------------------------

def get_filters(client: SierraHttpClient) -> dict:
    """Content-page filter vocab: ``sections`` and ``labels`` lists."""
    r = client.get_filters()
    sections = r.get("sections") if isinstance(r, dict) else None
    labels = r.get("labels") if isinstance(r, dict) else None
    return {"sections": sections or [], "labels": labels or []}


def list_content_labels(
    client: SierraHttpClient, *, sort_by: int = 1, sort_order: int = 0
) -> dict:
    """List content labels (the client returns a bare list)."""
    r = client.list_content_labels(sort_by=sort_by, sort_order=sort_order)
    return _listed(r if isinstance(r, list) else [])
