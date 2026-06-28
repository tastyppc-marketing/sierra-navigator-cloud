"""Tier-1 read shapers — drive SierraHttpClient through FakeTransport (no network)
and assert the shaped result extracts rows/records from the right keys."""
import json

import pytest

from sierra_core.client import SierraHttpClient
from sierra_core.transport import FakeTransport
from sierra_mcp import tools_read

SITE_ID = 5907


def env(inner: dict) -> str:
    """Sierra's {"d": "<stringified-json>"} envelope."""
    return json.dumps({"d": json.dumps(inner)})


def ok(data) -> str:
    return env({"responseCode": 0, "data": data})


def make_client(path: str, data) -> SierraHttpClient:
    return SierraHttpClient(FakeTransport({path: ok(data)}), site_id=SITE_ID)


# ---- single-record reads -------------------------------------------------

def test_get_page_record_from_page_key():
    c = make_client("/content-page-form.aspx/GetPage",
                    {"page": {"id": 218585, "name": "Home"}})
    out = tools_read.get_page(c, page_id=218585)
    assert out == {"record": {"id": 218585, "name": "Home"}}


def test_get_saved_search_record_unwrapped():
    # client unwraps the savedSearch key; shaper wraps the bare record.
    c = make_client("/lead-detail.aspx/GetSavedSearchRecord",
                    {"savedSearch": {"id": 77, "searchName": "Lakefront"}})
    out = tools_read.get_saved_search(c, search_id=77)
    assert out == {"record": {"id": 77, "searchName": "Lakefront"}}


def test_get_widget_record_unwrapped():
    c = make_client("/shared-html-widgets.aspx/GetHtmlWidget",
                    {"htmlWidget": {"id": 1252, "title": "CTA"}})
    out = tools_read.get_widget(c, widget_id=1252)
    assert out == {"record": {"id": 1252, "title": "CTA"}}


def test_get_blog_post_record_from_blogPost_key():
    # LIVE shape: GetBlogPostInfo nests the record under `blogPost`.
    c = make_client("/blog-post-form.aspx/GetBlogPostInfo",
                    {"blogPost": {"id": 42, "title": "Hello"}})
    out = tools_read.get_blog_post(c, post_id=42)
    assert out == {"record": {"id": 42, "title": "Hello"}}


def test_get_blog_post_falls_back_to_post_key():
    # Fallback for older/alternate payloads that use `post`.
    c = make_client("/blog-post-form.aspx/GetBlogPostInfo",
                    {"post": {"id": 7, "title": "Legacy"}})
    out = tools_read.get_blog_post(c, post_id=7)
    assert out == {"record": {"id": 7, "title": "Legacy"}}


# ---- list reads (key extraction incl. variance) --------------------------

def test_list_content_pages_from_contentPages_key():
    c = make_client("/content-pages.aspx/GetContentPageList",
                    {"contentPages": [{"id": 1}, {"id": 2}]})
    out = tools_read.list_content_pages(c)
    # no total reported + a partial page -> has_more False
    assert out == {"rows": [{"id": 1}, {"id": 2}], "count": 2, "has_more": False}


def test_list_content_pages_from_pages_key_variant():
    c = make_client("/content-pages.aspx/GetContentPageList",
                    {"pages": [{"id": 9}]})
    out = tools_read.list_content_pages(c, page_size=500)
    assert out == {"rows": [{"id": 9}], "count": 1, "has_more": False}


def test_list_saved_searches_from_items_key():
    # The documented gotcha: saved-search rows live under `items`, NOT savedSearches.
    c = make_client("/saved-searches.aspx/GetSavedSearchList",
                    {"items": [{"id": 5}, {"id": 6}, {"id": 7}],
                     "savedSearches": [{"id": 999}]})  # decoy must be ignored
    out = tools_read.list_saved_searches(c)
    assert out["count"] == 3
    assert [r["id"] for r in out["rows"]] == [5, 6, 7]


def test_list_html_widgets_from_widgets_key():
    c = make_client("/shared-html-widgets.aspx/GetHtmlWidgetList",
                    {"widgets": [{"id": 1}]})
    assert tools_read.list_html_widgets(c) == {
        "rows": [{"id": 1}], "count": 1, "has_more": False}


def test_list_html_widgets_from_items_key_variant():
    c = make_client("/shared-html-widgets.aspx/GetHtmlWidgetList",
                    {"items": [{"id": 3}, {"id": 4}]})
    assert tools_read.list_html_widgets(c)["count"] == 2


def test_list_blog_posts_from_posts_key():
    c = make_client("/blog-manager.aspx/GetBlogPostList",
                    {"posts": [{"id": 10}, {"id": 11}]})
    out = tools_read.list_blog_posts(c)
    assert out == {"rows": [{"id": 10}, {"id": 11}], "count": 2, "has_more": False}


def test_list_blog_posts_from_blogPosts_key_variant():
    c = make_client("/blog-manager.aspx/GetBlogPostList",
                    {"blogPosts": [{"id": 12}]})
    assert tools_read.list_blog_posts(c) == {
        "rows": [{"id": 12}], "count": 1, "has_more": False}


def test_list_content_labels_from_bare_list():
    c = make_client("/content-pages.aspx/GetContentLabels",
                    [{"id": 5, "name": "L1"}, {"id": 6, "name": "L2"}])
    out = tools_read.list_content_labels(c)
    assert out == {"rows": [{"id": 5, "name": "L1"}, {"id": 6, "name": "L2"}],
                   "count": 2}


def test_get_filters_extracts_sections_and_labels():
    c = make_client("/content-pages.aspx/GetFilters",
                    {"sections": [{"id": 1}], "labels": [{"id": 2}, {"id": 3}]})
    out = tools_read.get_filters(c)
    assert out == {"sections": [{"id": 1}], "labels": [{"id": 2}, {"id": 3}]}


# ---- defensive: missing keys degrade to empty ----------------------------

def test_list_missing_key_yields_empty_rows():
    c = make_client("/content-pages.aspx/GetContentPageList",
                    {"unexpected": "shape"})
    assert tools_read.list_content_pages(c) == {
        "rows": [], "count": 0, "has_more": False}


# ---- M1: pagination signal (total + has_more) ----------------------------

def test_total_and_has_more_from_totalRecords():
    c = make_client("/content-pages.aspx/GetContentPageList",
                    {"contentPages": [{"id": 1}], "totalRecords": 25})
    out = tools_read.list_content_pages(c, page_num=1, page_size=10)
    assert out["total"] == 25
    assert out["has_more"] is True   # 25 > 1*10


def test_has_more_false_on_last_record_page():
    c = make_client("/content-pages.aspx/GetContentPageList",
                    {"contentPages": [{"id": 1}], "totalRecords": 25})
    out = tools_read.list_content_pages(c, page_num=3, page_size=10)
    assert out["total"] == 25
    assert out["has_more"] is False  # 25 > 3*10 is False


def test_total_from_recordCount_key():
    c = make_client("/saved-searches.aspx/GetSavedSearchList",
                    {"items": [{"id": 1}, {"id": 2}], "recordCount": 2})
    out = tools_read.list_saved_searches(c, page_num=1, page_size=5000)
    assert out["total"] == 2
    assert out["has_more"] is False


def test_total_from_totalPages_uses_page_semantics():
    c = make_client("/blog-manager.aspx/GetBlogPostList",
                    {"posts": [{"id": 1}], "totalPages": 3})
    out = tools_read.list_blog_posts(c, page_num=1, page_size=50)
    assert out["total"] == 3
    assert out["has_more"] is True   # page 1 < 3 total pages


def test_has_more_heuristic_on_full_page_without_total():
    # No total key, but a full page (rows == page_size) implies more.
    c = make_client("/blog-manager.aspx/GetBlogPostList",
                    {"posts": [{"id": 1}, {"id": 2}]})
    out = tools_read.list_blog_posts(c, page_size=2)
    assert "total" not in out
    assert out["has_more"] is True


def test_get_filters_missing_keys_yield_empty_lists():
    c = make_client("/content-pages.aspx/GetFilters", {"other": 1})
    assert tools_read.get_filters(c) == {"sections": [], "labels": []}


@pytest.mark.parametrize("data", [{}, {"page": "not-a-dict"}])
def test_get_page_missing_record_yields_empty(data):
    c = make_client("/content-page-form.aspx/GetPage", data)
    assert tools_read.get_page(c, page_id=1) == {"record": {}}
