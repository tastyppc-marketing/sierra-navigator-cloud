# tests/sierra_core/test_client_reads.py
import json
from sierra_core.client import SierraHttpClient
from sierra_core.transport import FakeTransport


def env(inner): return json.dumps({"d": json.dumps(inner)})


def client(responses):
    return SierraHttpClient(FakeTransport(responses), site_id=4989, allow_write=False)


def test_get_page_builds_body_and_unwraps():
    ft = FakeTransport({"/content-page-form.aspx/GetPage":
                        env({"responseCode": 0, "data": {"page": {"id": 218585}}})})
    c = SierraHttpClient(ft, site_id=4989)
    out = c.get_page(218585)
    assert out["page"]["id"] == 218585
    path, body = ft.calls[0]
    assert body["id"] == 218585 and any(p["name"] == "id" for p in body["urlParams"])


def test_list_content_pages_sends_site_id_and_paging():
    ft = FakeTransport({"/content-pages.aspx/GetContentPageList":
                        env({"responseCode": 0, "data": {"pages": []}})})
    c = SierraHttpClient(ft, site_id=4989)
    c.list_content_pages(page_size=500)
    _, body = ft.calls[0]
    assert body["siteId"] == 4989 and body["pageSize"] == 500


def test_saved_searches_sends_string_site_id():
    ft = FakeTransport({"/saved-searches.aspx/GetSavedSearchList":
                        env({"responseCode": 0, "data": {"savedSearches": []}})})
    c = SierraHttpClient(ft, site_id=4989)
    c.list_saved_searches()
    _, body = ft.calls[0]
    assert body["siteId"] == "4989"  # this endpoint wants a STRING siteId


def test_get_widget_unwraps_htmlwidget_key():
    ft = FakeTransport({"/shared-html-widgets.aspx/GetHtmlWidget":
                        env({"responseCode": 0, "data": {"htmlWidget": {"id": 1252, "title": "CTA"}}})})
    c = SierraHttpClient(ft, site_id=4989)
    assert c.get_widget(1252)["title"] == "CTA"


# ---- M2: focused body-shape / unwrap tests for previously-untested reads -----

def test_get_filters_hits_endpoint_and_returns_data():
    ft = FakeTransport({"/content-pages.aspx/GetFilters":
                        env({"responseCode": 0, "data": {"sections": [{"id": 1}], "labels": []}})})
    c = SierraHttpClient(ft, site_id=4989)
    out = c.get_filters()
    path, body = ft.calls[0]
    assert path == "/content-pages.aspx/GetFilters"
    assert body["siteId"] == 4989
    assert "sections" in out


def test_list_content_labels_returns_list():
    ft = FakeTransport({"/content-pages.aspx/GetContentLabels":
                        env({"responseCode": 0, "data": [{"id": 5, "name": "L1"}]})})
    c = SierraHttpClient(ft, site_id=4989)
    result = c.list_content_labels()
    path, _ = ft.calls[0]
    assert path == "/content-pages.aspx/GetContentLabels"
    assert isinstance(result, list) and result[0]["name"] == "L1"


def test_list_content_labels_non_list_yields_empty():
    ft = FakeTransport({"/content-pages.aspx/GetContentLabels":
                        env({"responseCode": 0, "data": {"error": "no labels"}})})
    c = SierraHttpClient(ft, site_id=4989)
    assert c.list_content_labels() == []


def test_list_blog_posts_sends_site_id_and_returns_data():
    ft = FakeTransport({"/blog-manager.aspx/GetBlogPostList":
                        env({"responseCode": 0, "data": {"posts": [{"id": 10}]}})})
    c = SierraHttpClient(ft, site_id=4989)
    out = c.list_blog_posts()
    path, body = ft.calls[0]
    assert path == "/blog-manager.aspx/GetBlogPostList"
    assert body["siteId"] == 4989
    assert out["posts"][0]["id"] == 10


def test_get_blog_post_sends_id_and_returns_data():
    ft = FakeTransport({"/blog-post-form.aspx/GetBlogPostInfo":
                        env({"responseCode": 0, "data": {"post": {"id": 42, "title": "Hello"}}})})
    c = SierraHttpClient(ft, site_id=4989)
    out = c.get_blog_post(42)
    path, body = ft.calls[0]
    assert path == "/blog-post-form.aspx/GetBlogPostInfo"
    assert body["id"] == 42
    assert out["post"]["title"] == "Hello"


def test_get_saved_search_sends_string_site_id_and_unwraps_saved_search():
    ft = FakeTransport({"/lead-detail.aspx/GetSavedSearchRecord":
                        env({"responseCode": 0,
                             "data": {"savedSearch": {"searchName": "My Search", "id": 77}}})})
    c = SierraHttpClient(ft, site_id=4989)
    out = c.get_saved_search(77)
    path, body = ft.calls[0]
    assert path == "/lead-detail.aspx/GetSavedSearchRecord"
    assert body["siteId"] == "4989"   # STRING — this endpoint requires string siteId
    assert body["searchId"] == 77
    assert out["searchName"] == "My Search"  # savedSearch key unwrapped
