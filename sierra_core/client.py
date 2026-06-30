# sierra_core/client.py
"""Browserless Sierra admin client.

Identical endpoint semantics to the legacy SierraXhrClient but calls a
Transport instead of page.evaluate(fetch).  site_id is injected (discovered
once by the SessionBroker), not read from a page.
"""
from __future__ import annotations
import json as _json
from typing import Any

from sierra_core.parsing import unwrap_response
from sierra_core.errors import WriteNotAllowed, EndpointError, IdentityLockError
from sierra_core.transport import Transport


def _assert_mutation_ack(result: Any, *, what: str) -> None:
    """Require a POSITIVE Sierra acknowledgment for a write/delete mutation (#9; re-audit
    #3 MEDIUM extends it to non-delete writes).

    A mutation must NOT be reported as success merely because nothing raised. At HTTP 200
    Sierra can return: a non-``d`` ASP.NET fault (``unwrap_response`` yields ``None``), a
    business-rule rejection carrying a ``Message``/``error`` (a ``responseCode:0`` whose
    non-empty message ``unwrap_response`` preserves), or an empty body (a bare
    ``responseCode:0``). Treat fault/error-marker payloads as FAILURE; only an empty or
    error-marker-free payload counts as a committed mutation. Fail loud rather than audit a
    Sierra-rejected write as ``result="ok"`` (or flip the recovery ledger for a delete).
    """
    if isinstance(result, dict):
        for k in ("Message", "message", "error", "errors", "exceptionMessage", "ExceptionType"):
            v = result.get(k)
            if v:
                raise EndpointError(f"{what} rejected by Sierra: {v}", raw=result)
        if result.get("success") is False:
            raise EndpointError(f"{what} reported success=false", raw=result)
        # An empty dict is Sierra's bare ``responseCode:0`` success (no body) — accept it.
        # A ``responseCode:0`` that still carries a business-rule *message* is surfaced by
        # the loop above, because unwrap_response PRESERVES a non-empty message rather than
        # stripping it (#9); so a soft rejection no longer masquerades as an empty ack.
        return
    if result:  # truthy non-dict (e.g. an id or True) is a positive ack
        return
    raise EndpointError(f"{what}: no response body — cannot confirm the mutation", raw=result)


def _looks_like_page_absent(err: EndpointError) -> bool:
    raw = err.raw
    if not isinstance(raw, dict):
        return False
    message = raw.get("message") or raw.get("Message")
    return isinstance(message, str) and "not found" in message.lower()


class SierraHttpClient:
    """Browserless Sierra admin client.  Identical endpoint semantics to the
    legacy SierraXhrClient, but calls a Transport instead of page.evaluate(fetch)."""

    def __init__(
        self,
        transport: Transport,
        site_id: int,
        *,
        agent_site_id: int = -1,
        allow_write: bool = False,
    ):
        self._t = transport
        self.site_id = int(site_id)
        self._agent_site_id = agent_site_id
        self._allow_write = allow_write

    def __enter__(self):
        return self

    def __exit__(self, *_):
        close = getattr(self._t, "close", None)
        if close is not None:
            close()

    # NOTE: session-expiry refresh/retry (re-auth + retry once) is wired in Phase 2,
    # when the client is constructed from the SessionBroker. Phase 1 is a single-shot
    # CLI with a fresh login per run.
    def _call(self, path: str, body: dict | None = None) -> Any:
        return unwrap_response(self._t.post_json(path, body or {}))

    # ---- generic catalogued-endpoint caller --------------------------

    def call(self, path: str, body: dict | None = None, *, write: bool = False) -> Any:
        """Generic catalogued-endpoint call.

        Posts ``body`` verbatim to ``path`` and unwraps the response, exactly like
        the typed methods. ``write=True`` routes the call through the
        ``allow_write`` gate first (so a read-only client refuses it). The Tier-2
        ``sierra_call`` MCP tool sits on top of this; allow-listing, classification,
        and the destructive-op refusals are enforced there, not here.
        """
        if write:
            self._ensure_write("call")
            result = self._call(path, body or {})
            # Tier-2 generic writes must assert a positive Sierra ack too (re-audit #4
            # MEDIUM): a responseCode:0 + business-rule Message is a soft-rejection, not a
            # commit — W6-T2 wired this only on the typed write methods, not here.
            _assert_mutation_ack(result, what=f"sierra_call({path})")
            return result
        return self._call(path, body or {})

    # ---- reads -------------------------------------------------------

    def get_page(self, page_id: int | str) -> dict:
        pid = int(page_id)
        return self._call("/content-page-form.aspx/GetPage", {
            "id": pid,
            "urlParams": [
                {"name": "id",   "value": str(pid)},
                {"name": "secid","value": "-1"},
                {"name": "clid", "value": "-1"},
                {"name": "sb",   "value": "2"},
                {"name": "so",   "value": "0"},
                {"name": "pn",   "value": "1"},
                {"name": "asid", "value": "-1"},
            ],
        })

    def get_filters(self) -> dict:
        return self._call(
            "/content-pages.aspx/GetFilters",
            {"siteId": self.site_id, "agentSiteId": self._agent_site_id},
        )

    def list_content_labels(
        self, sort_by: int = 1, sort_order: int = 0
    ) -> list:
        r = self._call(
            "/content-pages.aspx/GetContentLabels",
            {
                "siteId": self.site_id,
                "agentSiteId": self._agent_site_id,
                "sortBy": sort_by,
                "sortOrder": sort_order,
            },
        )
        return r if isinstance(r, list) else []

    def list_content_pages(
        self,
        sort_by: int = 2,
        sort_direction: int = 0,
        section_id: int = -1,
        content_label_id: int = -1,
        status_id: int = -1,
        search_term: str = "",
        page_num: int = 1,
        page_size: int = 1000,
    ) -> dict:
        return self._call(
            "/content-pages.aspx/GetContentPageList",
            {
                "siteId": self.site_id,
                "agentSiteId": self._agent_site_id,
                "sectionId": section_id,
                "contentLabelId": content_label_id,
                "statusId": status_id,
                "searchTerm": search_term,
                "sortBy": sort_by,
                "sortDirection": sort_direction,
                "pageNumber": page_num,
                "pageSize": page_size,
                "updatePageSize": True,
            },
        )

    def list_html_widgets(
        self,
        sort_by: int = 1,
        sort_direction: int = 1,
        widget_type: int = -1,
        search_term: str = "",
        page_num: int = 1,
        page_size: int = 100,
    ) -> dict:
        return self._call(
            "/shared-html-widgets.aspx/GetHtmlWidgetList",
            {
                "siteId": self.site_id,
                "agentSiteId": self._agent_site_id,
                "searchTerm": search_term,
                "widgetType": widget_type,
                "sortBy": sort_by,
                "sortDirection": sort_direction,
                "pageNumber": page_num,
                "pageSize": page_size,
            },
        )

    def list_saved_searches(
        self,
        sort_by: int = 4,
        sort_direction: int = 0,
        search_term: str = "",
        favorite_filter: int = 1,
        page_num: int = 1,
        page_size: int = 5000,
    ) -> dict:
        return self._call(
            "/saved-searches.aspx/GetSavedSearchList",
            {
                "siteId": str(self.site_id),
                "agentSiteId": str(self._agent_site_id),
                "searchTerm": search_term,
                "sortBy": sort_by,
                "sortDirection": sort_direction,
                "favoriteFilter": favorite_filter,
                "pageNumber": page_num,
                "pageSize": page_size,
                "user": True,
            },
        )

    def list_blog_posts(
        self,
        sort_by: int = 1,
        sort_direction: int = 1,
        category_id: int = -1,
        author_id: int = -1,
        tag_id: int = -1,
        search_term: str = "",
        page_num: int = 1,
        page_size: int = 200,
    ) -> dict:
        return self._call(
            "/blog-manager.aspx/GetBlogPostList",
            {
                "siteId": self.site_id,
                "agentSiteId": self._agent_site_id,
                "categoryId": category_id,
                "authorId": author_id,
                "tagId": tag_id,
                "searchTerm": search_term,
                "sortBy": sort_by,
                "sortDirection": sort_direction,
                "pageNumber": page_num,
                "pageSize": page_size,
            },
        )

    def get_blog_post(self, post_id: int | str) -> dict:
        pid = int(post_id)
        return self._call(
            "/blog-post-form.aspx/GetBlogPostInfo",
            {"id": pid, "urlParams": [{"name": "id", "value": str(pid)}]},
        )

    def get_widget(self, widget_id: int | str) -> dict:
        r = self._call(
            "/shared-html-widgets.aspx/GetHtmlWidget",
            {"widgetId": int(widget_id)},
        )
        if isinstance(r, dict) and "htmlWidget" in r:
            return r["htmlWidget"]
        return r

    def get_saved_search(self, search_id: int | str) -> dict:
        r = self._call(
            "/lead-detail.aspx/GetSavedSearchRecord",
            {"searchId": int(search_id), "siteId": str(self.site_id)},
        )
        if isinstance(r, dict):
            for k in ("savedSearch", "search", "savedSearchRecord"):
                if k in r:
                    return r[k]
        return r

    # ---- write guard (used by write/delete methods) -------------------

    def _ensure_write(self, op: str) -> None:
        if not self._allow_write:
            raise WriteNotAllowed(f"{op} requires allow_write=True")

    def _assert_page_absent(self, page_id: int | str) -> None:
        try:
            record = self.get_page(page_id)
        except EndpointError as err:
            if _looks_like_page_absent(err):
                return
            raise
        fetched_id = (record.get("page") or {}).get("id") if isinstance(record, dict) else None
        raise EndpointError(
            f"delete_content_page(id={page_id}): page still present after delete "
            f"(re-fetch returned id={fetched_id!r}) - Sierra acknowledged but did not remove it",
            raw=record,
        )

    # ---- writes -------------------------------------------------------

    def add_content_label(self, name: str, page_id: int = -1) -> int:
        self._ensure_write("add_content_label")
        r = self._call(
            "/content-page-form.aspx/AddContentLabel",
            {
                "siteId": self.site_id,
                "agentSiteId": self._agent_site_id,
                "pageId": int(page_id),
                "name": name,
            },
        )
        if isinstance(r, dict) and "contentLabelId" in r:
            return int(r["contentLabelId"])
        raise EndpointError(f"add_content_label: unexpected {r!r}")

    def update_content_label(self, content_label_id: int, name: str) -> None:
        self._ensure_write("update_content_label")
        _assert_mutation_ack(
            self._call(
                "/content-pages.aspx/UpdateContentLabel",
                {
                    "siteId": self.site_id,
                    "agentSiteId": self._agent_site_id,
                    "contentLabelId": int(content_label_id),
                    "name": name,
                },
            ),
            what="update_content_label",
        )

    def remove_content_label(self, content_label_id: int) -> None:
        self._ensure_write("remove_content_label")
        _assert_mutation_ack(
            self._call(
                "/content-pages.aspx/RemoveContentLabel",
                {"contentLabelId": int(content_label_id)},
            ),
            what="remove_content_label",
        )

    def save_html_widget(self, widget: dict) -> None:
        self._ensure_write("save_html_widget")
        _assert_mutation_ack(
            self._call(
                "/shared-html-widgets.aspx/SaveHtmlWidget",
                {
                    "widget": _json.dumps(widget),
                    "siteId": self.site_id,
                    "agentSiteId": self._agent_site_id,
                },
            ),
            what="save_html_widget",
        )

    def save_content_page(
        self,
        page: dict,
        components: list,
        associations: list | None = None,
    ) -> dict:
        self._ensure_write("save_content_page")
        r = self._call(
            "/content-page-form.aspx/SaveContentPage",
            {
                "page": page,
                "components": components or [],
                "siteId": self.site_id,
                "agentSiteId": self._agent_site_id,
                "associations": associations or [],
            },
        )
        _assert_mutation_ack(r, what="save_content_page")
        return r if isinstance(r, dict) else {}

    def update_page_component_title(
        self, component_link_id: int, title: str
    ) -> None:
        self._ensure_write("update_page_component_title")
        _assert_mutation_ack(
            self._call(
                "/content-page-form.aspx/UpdatePageComponentTitle",
                {"componentId": int(component_link_id), "componentTitle": title},
            ),
            what="update_page_component_title",
        )

    def add_page_component_link(
        self,
        page_id: int,
        type: int,
        title: str,
        data: str = "",
        tags: list | None = None,
        component_id: int = -1,
    ) -> int:
        self._ensure_write("add_page_component_link")
        r = self._call(
            "/content-page-form.aspx/AddPageComponentLink",
            {
                "componentId": int(component_id),
                "data": data,
                "pageId": int(page_id),
                "tags": tags or [],
                "title": title,
                "type": int(type),
            },
        )
        if isinstance(r, dict):
            if "pageComponentId" in r:
                return int(r["pageComponentId"])
            if isinstance(r.get("data"), dict) and "pageComponentId" in r["data"]:
                return int(r["data"]["pageComponentId"])
        raise EndpointError(f"add_page_component_link: unexpected {r!r}")

    def remove_page_component_link(self, component_link_id: int) -> None:
        self._ensure_write("remove_page_component_link")
        _assert_mutation_ack(
            self._call(
                "/content-pages.aspx/RemovePageComponentLink",
                {"componentId": int(component_link_id)},
            ),
            what="remove_page_component_link",
        )

    def add_page_content_label_link(
        self, page_id: int, content_label_id: int
    ) -> None:
        self._ensure_write("add_page_content_label_link")
        _assert_mutation_ack(
            self._call(
                "/content-page-form.aspx/AddPageContentLabelLink",
                {"pageId": int(page_id), "contentLabelId": int(content_label_id)},
            ),
            what="add_page_content_label_link",
        )

    def remove_page_content_label_link(
        self, page_id: int, content_label_id: int
    ) -> None:
        self._ensure_write("remove_page_content_label_link")
        _assert_mutation_ack(
            self._call(
                "/content-pages.aspx/RemovePageContentLabelLink",
                {"pageId": int(page_id), "contentLabelId": int(content_label_id)},
            ),
            what="remove_page_content_label_link",
        )

    def save_saved_search(self, record: dict) -> dict:
        self._ensure_write("save_saved_search")
        r = self._call("/lead-detail.aspx/SaveSearchRecord", record)
        _assert_mutation_ack(r, what="save_saved_search")
        return r if isinstance(r, dict) else {}

    # ---- identity-locked deletes -------------------------------------

    def delete_content_page(
        self,
        page_id: int,
        *,
        expected_title: str,
        snapshot_sink,
    ) -> dict:
        """HARD, irreversible delete.
        Re-fetch → id-echo guard → identity-lock on title → snapshot → delete."""
        from sierra_core.identity import assert_identity
        self._ensure_write("delete_content_page")
        record = self.get_page(page_id)
        # id-echo guard: refuse if the re-fetched record's id doesn't match
        fetched_id = (record.get("page") or {}).get("id") if isinstance(record, dict) else None
        if str(fetched_id) != str(page_id):
            raise IdentityLockError(
                f"id-echo: requested page_id={page_id} but record returned id={fetched_id!r}"
            )
        stored = (
            (record.get("page") or {}).get("name", "")
            if isinstance(record, dict)
            else ""
        )
        assert_identity(
            supplied_title=expected_title,
            stored_title=stored,
            entity_id=page_id,
        )
        snapshot_sink(record)  # MUST happen before the irreversible delete
        ack = self._call(
            "/content-pages.aspx/DeleteContentPage",
            {"pageId": int(page_id)},
        )
        _assert_mutation_ack(ack, what=f"delete_content_page(id={page_id})")
        # Production calibration 2026-06-29: hard-deleted pages re-fetch as Page not found.
        self._assert_page_absent(page_id)
        return {"deleted": int(page_id), "reversible": False}

    def delete_saved_search(
        self,
        search_id: int,
        *,
        expected_title: str,
        snapshot_sink,
    ) -> dict:
        """SOFT delete (recoverable by id).
        Re-fetch → id-echo guard → identity-lock on name → snapshot → delete."""
        from sierra_core.identity import assert_identity
        self._ensure_write("delete_saved_search")
        record = self.get_saved_search(search_id)
        # id-echo guard: only fires if the record exposes an 'id' field
        fetched_id = record.get("id") if isinstance(record, dict) else None
        if fetched_id is not None and str(fetched_id) != str(search_id):
            raise IdentityLockError(
                f"id-echo: requested search_id={search_id} but record returned id={fetched_id!r}"
            )
        stored = (
            record.get("searchName") or record.get("name", "")
            if isinstance(record, dict)
            else ""
        )
        assert_identity(
            supplied_title=expected_title,
            stored_title=stored,
            entity_id=search_id,
        )
        snapshot_sink(record)
        ack = self._call(
            "/saved-searches.aspx/DeleteSavedSearch",
            {"siteId": str(self.site_id), "savedSearchId": int(search_id)},
        )
        _assert_mutation_ack(ack, what=f"delete_saved_search(id={search_id})")
        # Saved-search deletes are soft/recoverable; re-fetch by id still returns the record.
        return {"deleted": int(search_id), "reversible": True}
