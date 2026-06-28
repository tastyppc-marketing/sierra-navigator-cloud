# Sierra Interactive — Backend XHR Endpoint Catalogue

**Purpose:** Map every JSON XHR endpoint Sierra's admin uses, so we can hit them directly instead of DOM-scraping. These endpoints are the foundation for the natural-language CMS automation goal (see `GOAL.md`).

**Auth model:** All endpoints require an authenticated session cookie from `/login.aspx`. Once logged in, calls work via `fetch(...)` inside the page context (cookies + referer flow naturally). Outside the browser, you'd need the session cookie in the request header.

**Read vs write:** All endpoints catalogued so far are READS. Write endpoints (SavePage, etc.) require a user-initiated write action in the browser to capture — gated until the user authorizes. See task D4-precursor.

---

## Calling pattern (verified 2026-06-27)

**New — pure httpx (no browser in the hot path):**
Use `sierra_core.SessionBroker` to acquire a `Session(cookies, site_id, base_url)` via ASP.NET
forms-auth over plain httpx, then pass the result to `HttpxTransport` and `SierraHttpClient`.
The per-tenant admin base URL is discovered at login time (this account = `https://client7.sierrainteractivedev.com`).

```python
from sierra_core.session import SessionBroker
from sierra_core.transport import HttpxTransport
from sierra_core.client import SierraHttpClient

sess = SessionBroker().get_session()          # HTTP login; headed-browser fallback on failure
client = SierraHttpClient(
    HttpxTransport(sess.base_url, sess.cookies),
    site_id=sess.site_id,
)
pages = client.list_content_pages(page_size=100)
```

Required headers on every request (set by `HttpxTransport` automatically):
```
Content-Type: application/json; charset=UTF-8
X-Requested-With: XMLHttpRequest
Accept: application/json, text/javascript, */*; q=0.01
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
```
Auth cookie: `.SIERRAXAUTH` (captured in the full jar; name-agnostic in the broker).

**Legacy — page.evaluate (browser in the hot path):**
Inside a Playwright page context, call `fetch(url, {method:"POST", headers:{...}, body:JSON.stringify(body)})`.
Cookies flow automatically via the browser session. Used by `scrapers/sierra_xhr.py`; superseded
for new work by `sierra_core/client.py`.

---

## READ endpoints (verified)

### GetPage — `POST /content-page-form.aspx/GetPage`
Returns the full state of a single content page.

**Headers:** `Content-Type: application/json; charset=UTF-8`, `X-Requested-With: XMLHttpRequest`, `Accept: application/json`

**Request body:**
```json
{
  "id": 218585,
  "urlParams": [
    {"name": "id", "value": "218585"},
    {"name": "secid", "value": "-1"},
    {"name": "clid", "value": "-1"},
    {"name": "sb", "value": "2"},
    {"name": "so", "value": "0"},
    {"name": "pn", "value": "1"},
    {"name": "asid", "value": "-1"}
  ]
}
```

**Response:** `{"d": "<stringified JSON>"}`. The stringified inner has shape `{"responseCode":0, "data": {...}}`. The `data` object contains:
- `siteId`, `agentSiteId`
- `page`: id, name, fileName (slug), sectionId, type, status, metaTitle, metaDescription, metaKeywords, robots, url (public), dateCreated, dateUpdated, labels[], password, hideHeaderNav, hideFooterNav, hideBreadcrumbs, fluidWidth, showSidebar, sidebarId, openIn, externalLink, photoId, priority, summary, updatedByName
- `pageComponents`: array of `{id, title, data, type (1-20), componentId, content, numParagraph, hasReachedListingsPagesLimit}`
- `sections`: full sections list with `{id, name, fileName, type, totalPages, ...}`

**Sample:** `sierra-map/forms/network_recon_218585.json`

---

### GetFilters — `POST /content-pages.aspx/GetFilters`
Returns the filter dropdown options for the content-pages list page.

**Request body:** *(empty body or no body required — captured during page load)*

**Response:** `{"d":"..."}` → `data: {sections: [...], contentLabels: [...]}`
- `sections`: 31 entries for Gardner (placeholder + 30 real sections) with `{id, name, fileName, type, totalPages, excludeGlobalNav, allowDelete, ...}`
- `contentLabels`: 0 entries here (labels are fetched via `GetContentLabels` separately)

**Sample:** `sierra-map/forms/GetFilters_response.json`

---

### GetContentLabels — `POST /content-pages.aspx/GetContentLabels`
Returns up to **100** content labels (server-side cap, no pagination params honored).

**Request body:**
```json
{"siteId": 4989, "agentSiteId": -1, "sortBy": 1, "sortOrder": 0}
```

- `sortBy`: 1=name, 2=pages-with-label, 3=pages-with-widget, 4=name (alias)
- `sortOrder`: 0=ascending, 1=descending

**Response:** `data: [{id, name, pagesWithLabel, pagesWithWidget}, ...]`

**Cap behaviour:** Tried `page`, `pageNumber`, `skip`, `offset`, `pageSize`, `limit`, `take`, `includeAll`, `loadAll` — all ignored. Server returns max 100. For Gardner the real total is 100, verified by merging 8 sort permutations. Larger sites may need a different endpoint.

**Sample:** `sierra-map/forms/GetContentLabels_response.json`, `labels_full.json`

---

## READ endpoints (newly verified — captured 2026-05-17)

### GetContentPageList — `POST /content-pages.aspx/GetContentPageList`
Returns the content pages list (replaces DOM-scraping `list_content_pages`).

**Request body fields (observed):** `siteId`, `agentSiteId`, plus filter/sort params.
Use this instead of paging through the DOM — likely supports `sortBy`/`sortOrder`/page params similar to other endpoints.

### GetHtmlWidgetList — `POST /shared-html-widgets.aspx/GetHtmlWidgetList`
Returns shared HTML widgets (replaces `list_shared_html_widgets`).

### GetSavedSearchList — `POST /saved-searches.aspx/GetSavedSearchList`
Returns saved searches (replaces `list_saved_searches`).

### GetBlogPostList — `POST /blog-manager.aspx/GetBlogPostList`
Returns blog posts (replaces `list_blog_posts`).

**Request body (captured):**
```json
{"siteId":4989, "agentSiteId":-1, "categoryId":-1, "authorId":-1, "tagId":-1, "searchTerm":"", "sortBy":1, "sortOrder":0, ...}
```

### GetBlogPostInfo — `POST /blog-post-form.aspx/GetBlogPostInfo`
Returns a single blog post (replaces `read_blog_post`).

**Request body:** `{"id": <post_id>, "urlParams":[{"name":"id","value":"<post_id>"}]}`

### Supporting endpoints
- `POST /content-pages.aspx/GetAgentSites` — agent-site list for the filter dropdown
- `POST /content-pages.aspx/GetFilters` — sections+labels for filter dropdowns
- `POST /shared-html-widgets.aspx/GetSiteHtmlWidgetPermissions` — per-site widget perms
- `POST /saved-searches.aspx/GetSites` — site list for saved-search scope
- `POST /blog-manager.aspx/GetFilters` + `GetAgentSites`
- `POST /sierra-alerts.aspx/GetUiAlertsToShow` — admin banner notifications (request: `{"uiAlertTypes":[2]}`)
- `POST /header-statistics.ashx` — header counters (tasks, inbox)
- `POST /cdn-cgi/rum?...` — Cloudflare RUM telemetry (skip)

---

## Single-entity reads (verified 2026-05-17)

### GetHtmlWidget — `POST /shared-html-widgets.aspx/GetHtmlWidget`
Returns one shared HTML widget (content, type, payload).

**Request body:** `{"widgetId": 1252}`

**Response wrapper:** `{"d": {"htmlWidget": {id, title, widgetType, widgetTypeId, sectionId, content, code, dateAdded, dateUpdated}}}`. SierraXhrClient.get_widget auto-unwraps the `htmlWidget` key.

**Notes:** widgetTypeId 1 = Component (uses `content` field), 4 = JavaScript/Style (uses `code` field). For widget #1252 ("1a General CTA"), content was 3814c of HTML/CSS.

### GetSavedSearchRecord — `POST /lead-detail.aspx/GetSavedSearchRecord`
Returns one saved search's record. **Note the URL** — despite reading a saved search, the endpoint lives under `/lead-detail.aspx` (Sierra's saved-search edit form is shared with the lead-detail filter UI).

**Request body:** `{"searchId": 6845061, "siteId": "4989"}` — note `siteId` is a STRING here, as on GetSavedSearchList.

Sample captured at `sierra-map/forms/savedsearch_edit_xhr.json`. Side-effects of the edit click: also fires `GetSiteMapDefaults` and `GetSearchFormData` (read-only config endpoints).

---

## WRITE endpoints (LIVE-VERIFIED 2026-05-19)

Every endpoint in this section was fired against the Gardner test environment under explicit user authorization and verified end-to-end (write → read-back → revert → read-back). Capture files in `sierra-map/forms/verify_*.json`.

**Universal calling convention:** ASP.NET WebMethods accept JSON body with `Content-Type: application/json; charset=UTF-8`. The Sierra JS occasionally builds bodies via Python-dict-style string concatenation (`"{'siteId':"+id+", ...}"`); this is purely a jQuery serialization artifact — clean JSON is accepted everywhere.

**Response shapes:** ASP.NET wraps every response as `{"d": "<stringified-JSON>"}`. The inner JSON uses one of two response-code casings — **be ready for both**:
- **Lowercase** `{"responseCode": 0, "data": {...}}`: AddContentLabel, RemoveContentLabel, SaveContentPage, UpdatePageComponentTitle, AddPageComponentLink, RemovePageComponentLink
- **Capital** `{"ResponseCode": 0, "Data": ..., "Message": "..."}`: UpdateContentLabel, SaveHtmlWidget

The `_call` helper in `scrapers/sierra_xhr.py` handles both via key fallback.

### AddContentLabel — `POST /content-page-form.aspx/AddContentLabel`
Create a new Content Label. If `pageId != -1`, the label is also attached to that page on creation.

**Body:** `{"siteId": <int>, "agentSiteId": <int>, "pageId": <int>, "name": "<str>"}`
**Response inner:** `{"responseCode": 0, "data": {"contentLabelId": <new_id>, "pageContentLabelId": -1}}` — returns new id directly.
**Verified:** A1 (label 19778 lifecycle on Gardner).

### UpdateContentLabel — `POST /content-pages.aspx/UpdateContentLabel`
Rename an existing label.

**Body:** `{"siteId": <int>, "agentSiteId": <int>, "contentLabelId": <int>, "name": "<str>"}`
**Response inner:** **capital-keyed** `{"ResponseCode": 0, "Data": null, "Message": ""}` — no payload.
**Verified:** A1.

### RemoveContentLabel — `POST /content-pages.aspx/RemoveContentLabel`
Delete a label.

**Body:** `{"contentLabelId": <int>}`
**Response inner:** `{"responseCode": 0}` — no data envelope.
**Verified:** A1.

### SaveHtmlWidget — `POST /shared-html-widgets.aspx/SaveHtmlWidget`
Upsert a shared HTML widget. `id=-1` = create, `id=<n>` = update — Sierra's universal "Save<Entity>" = upsert pattern.

**Body:** `{"widget": "<JSON.stringify(widget_obj)>", "siteId": <int>, "agentSiteId": <int>}` — **the `widget` field is a stringified JSON object**, not a nested dict. The inner widget shape: `{id, title, content, widgetTypeId, widgetType, sectionId, dateAdded, dateUpdated}`. Send back every field returned by GetHtmlWidget.
**Response inner:** capital-keyed `{"ResponseCode": 0, "Data": null, "Message": ""}` — Save does NOT return the saved widget. To discover the new id after create-upsert, call `GetHtmlWidgetList` and find by title.
**Verified:** A2 (widget 1603 'lazy' title edit + revert on Gardner).

### SaveContentPage — `POST /content-page-form.aspx/SaveContentPage`
Full-page upsert (meta + components + associations). The UI's main "Save Page" button calls this; React buffers all component edits client-side and commits everything in one call (per `content-page-form.js` source).

**Body:**
```json
{
  "page": { /* full page object from GetPage.page */ },
  "components": [ /* GetPage.pageComponents (NOTE: NAMING ASYMMETRY) */ ],
  "siteId": <int>, "agentSiteId": <int>,
  "associations": [ /* GetPage.associations */ ]
}
```
**CRITICAL FIELD-NAMING ASYMMETRY:** GetPage returns `pageComponents`, but SaveContentPage expects `components`. Re-key before sending.
**Response inner:** `{"responseCode": 0, "data": { ... saved page payload ... }}`.
**Verified:** A3 (page 218725 metaTitle edit + revert; all 19 preservable fields round-tripped cleanly).

### UpdatePageComponentTitle — `POST /content-page-form.aspx/UpdatePageComponentTitle`
Update one component's admin title. Bypasses SaveContentPage — used by inline rename modals.

**Body:** `{"componentId": <link_id>, "componentTitle": "<str>"}`
**CRITICAL FIELD-NAMING GOTCHA:** GetPage component objects have BOTH `.id` and `.componentId` fields. **The endpoint's `componentId` param == GetPage's `.id` field (the page-component LINK id), NOT its `.componentId` field (the widget template id).** Passing the wrong one returns `responseCode 1, "Content page not found."` and is a silent client-side bug if your verification lookup uses the wrong field.
**Response inner:** `{"responseCode": 0}` — no data envelope.
**Verified:** A4 (component 646467 on page 218725).

### AddPageComponentLink — `POST /content-page-form.aspx/AddPageComponentLink`
Attach a component (existing widget by id, or a fresh inline ad-hoc component) to a page. Used by inline-add modals (the main page-Save button uses `SaveContentPage` instead).

**Body:** `{"componentId": <int>, "data": "<str>", "pageId": <int>, "tags": [], "title": "<str>", "type": <int>}`
- `componentId = -1` for an ad-hoc inline component; otherwise the existing widget id
- `data` is the payload (HTML for type=1)
- `type`: 1=HTML, 2=ListLink, etc. (see `sierra-map/components/` for the type table)

**Response inner:** `{"responseCode": 0, "data": {"pageComponentId": <new_link_id>}}` — returns the new LINK id directly (this is the `.id` field you'd see in a subsequent GetPage).
**Verified:** B1 (component 1155651 created+deleted on page 218725).

### RemovePageComponentLink — `POST /content-pages.aspx/RemovePageComponentLink`
Detach a component from a page. **Note: lives on `/content-pages.aspx/`, NOT `/content-page-form.aspx/`** — Sierra splits Add and Remove across two domains.

**Body:** `{"componentId": <link_id>}` — where `componentId` is the LINK id (same gotcha as UpdatePageComponentTitle).
**Response inner:** `{"responseCode": 0}` — no data.
**Verified:** B1.

### AddPageContentLabelLink — `POST /content-page-form.aspx/AddPageContentLabelLink`
Attach an existing Content Label to a page.

**Body:** `{"pageId": <int>, "contentLabelId": <int>}`
**Response inner:** lowercase `{responseCode: 0}` on success (no body envelope returned beyond that).
**Note:** wrapped by `manage_site` NL intent `attach label "<name>" to page <id>` (resolves label name → id via `GetFilters.contentLabels` for uncapped lookup). Side effect: GetPage(`pageId`).page.labels gains an entry with the attached `contentLabelId`.
**Verified: E1 on 2026-05-22** via `scrapers/verify_pagecontentlabellink.py` (Gardner OnHold page 218725, throwaway test label).

### RemovePageContentLabelLink — `POST /content-pages.aspx/RemovePageContentLabelLink`
Detach a Content Label from a page. **Different .aspx from AddPageContentLabelLink** — same Add/Remove domain-split pattern as the component link endpoints.

**Body:** `{"pageId": <int>, "contentLabelId": <int>}`
**Response inner:** lowercase `{responseCode: 0}` on success.
**Note:** wrapped by `manage_site` NL intent `detach label "<name>" from page <id>`. Side effect: GetPage(`pageId`).page.labels loses the entry with `contentLabelId`.
**Verified: E1 on 2026-05-22** via `scrapers/verify_pagecontentlabellink.py`. Post-call GetPage round-trip confirmed label set returned to pre-attach state.

---

## Caveats and gotchas worth memorizing

1. **`GetContentLabels` is server-capped at 100.** Gardner has 102 labels. For full-list lookups (e.g. resolving a label name to its id), call `GetFilters` instead — it returns all 102 (plus a synthetic `id=-1` "All Content Labels" row to filter out).
2. **Response code key casing is per-endpoint, not consistent.** Capture handlers MUST check both `responseCode` (lowercase) and `ResponseCode` (capital). Same for `Data`/`data` and `Message`/`message`.
3. **Add and Remove endpoints can live on different `.aspx` paths** for the same logical object (component links, label links). Don't assume symmetry.
4. **Save\<Entity\> is upsert** (`id=-1` creates, `id=N` updates) and does NOT return the saved entity on create — re-list to discover new ids.
5. **GetPage `pageComponents` vs SaveContentPage `components`** — different field names for the same array. Re-key when round-tripping.
6. **Component link id ≠ component template id.** GetPage component objects have both `.id` (link/instance) and `.componentId` (template/widget). All write endpoints that target "a component on a page" want the `.id` field. This is the single biggest field-naming foot-gun in Sierra's API.
7. **Old-style `application.ajax` calls send Python-dict-style string bodies in the JS** but the ASP.NET WebMethod accepts clean JSON too. Don't replicate the string concat — send JSON.
8. **Authorization gating:** all write methods in `SierraXhrClient` require `allow_write=True` in the constructor. Default is False — every write raises a `RuntimeError` with the guard message if not enabled. Combined with `manage_site`'s `dry_run=True` default, three layers protect against unintended writes.

---

## WRITE endpoints discovered via JS-source grep (2026-05-18)

**Source:** `https://client3.sierrainteractivedev.com/dist/js/content-pages.js?v=1.0.0.1886`

Each Sierra admin domain ships a `<Domain>Service.prototype.<method> = function(params){var defaults={...}; ...url:"..."}` pattern. Grepping these gives URL + default param shape without firing any XHR. The corresponding script bundles to sweep next: `saved-searches.js`, `blog-manager.js`, `shared-html-widgets.js`, `content-page-form.js`, `sidebar.js`, `agent-sites.js`, `testimonials.js`, `seller-lead-sites.js`.

### Content Page lifecycle
| Endpoint | Method | Default params |
|---|---|---|
| `/content-pages.aspx/DeleteContentPage` | POST | `{pageId: -1}` |
| `/content-pages.aspx/DeleteContentPages` | POST | `{siteId, agentSiteId, pageIds}` (string-serialized) — bulk |
| `/content-pages.aspx/DuplicateContentPage` | POST | `{pageId, sectionId, pageName, fileName, checkCommunityPageLimit: true}` |
| `/content-pages.aspx/ReOrderContentPages` | POST | `{sectionId: -1, pageIds: []}` |

**`DeleteContentPage` LIVE-VERIFIED 2026-06-16** (Fetterplace site 6115): body `{"pageId": 124195}` → success inner `{"responseCode": 0}` (no data envelope; `_call` returns `{}`). Follow-up `GetPage(124195)` → `{"responseCode": 1, "message": "Page not found"}` and the public URL 404s (even with `?caa=true`). Hard-delete, irreversible — back up the full GetPage JSON first. Used to remove a retired OnHold duplicate; identity-lock by `id` + `fileName` + `status==2` before firing. Driver: `_delete_mason_hyphen.py`.

### Section CRUD
| Endpoint | Method | Default params |
|---|---|---|
| `/content-pages.aspx/AddSection` | POST | `{siteId, agentSiteId, name, fileName, type: 0, sidebarId: -1, excludeGlobalNav: true, allowDelete: true, allowNameChange: true, allowFileNameChange: true}` |
| `/content-pages.aspx/UpdateSection` | POST | `{sectionId, name, fileName, sidebarId, excludeGlobalNav, allowDelete, allowNameChange, allowFileNameChange}` |
| `/content-pages.aspx/DeleteSection` | POST | `{sectionId, newSectionId}` — `newSectionId` is where to migrate the section's pages |
| `/content-pages.aspx/ReorderSections` | POST | `{siteId, agentSiteId, sections: []}` |
| `/content-pages.aspx/UpdateSectionSettings` | POST | (see content-pages.js) |
| `/content-pages.aspx/UpdateSectionLinkSettings` | POST | (see content-pages.js) |
| `/content-pages.aspx/UpdateSectionMainNavVisibility` | POST | `{sectionId, enable}` |
| `/content-pages.aspx/UpdateSectionSubmenuVisibility` | POST | (see content-pages.js) |
| `/content-pages.aspx/GetSectionSettings` | POST | (read) |
| `/content-pages.aspx/GetSections` | POST | (read) |

### Content Label CRUD
| Endpoint | Method | Default params |
|---|---|---|
| `/content-page-form.aspx/AddContentLabel` | POST | `{siteId, agentSiteId, pageId, name}` — note: lives on content-page-form, not content-pages |
| `/content-pages.aspx/UpdateContentLabel` | POST | `{siteId, agentSiteId, contentLabelId, name}` |
| `/content-pages.aspx/RemoveContentLabel` | POST | `{contentLabelId: -1}` — deletes the label entity itself |
| `/content-pages.aspx/ApplyContentLabelToPages` | POST | `{contentLabelId: -1, pageIds: []}` — bulk attach to multiple pages |
| `/content-pages.aspx/RemovePageContentLabelLink` | POST | `{pageId: -1, contentLabelId: -1}` — detach a single label from a single page |
| `/content-pages.aspx/GetContentLabels` | POST | (read; we already use this) |
| `/content-pages.aspx/GetContentPagesByContentLabel` | POST | (read) |

### Page Component (live delete — may bypass SaveContentPage)
| Endpoint | Method | Default params |
|---|---|---|
| `/content-pages.aspx/RemovePageComponentLink` | POST | `{componentId: -1}` |

**Open question:** `verify_delete_via_save.py` (2026-05-18) concluded that component delete is client-side staged and committed via `SaveContentPage`. But the JS exposes a direct `RemovePageComponentLink(componentId)` endpoint. Possibly Sierra has two delete paths (per-component fast-path vs. multi-component-edit batched-save). Task #32 will test this on a new test page.

### Page-Sidebar association (separate domain)
| Endpoint | Method | Notes |
|---|---|---|
| `/sidebar.aspx/GetSidebar` | POST | read single sidebar |
| `/sidebar.aspx/SaveSidebar` | POST | create/update sidebar |
| `/sidebar.aspx/GetPagesAutocomplete` | POST | autocomplete for sidebar links |
| `/sidebars.aspx/CopySidebar` | POST | duplicate sidebar |

### Misc reads in /content-pages.aspx
| Endpoint | Notes |
|---|---|
| `/content-pages.aspx/GetAgentSites` | agent-site picker |
| `/content-pages.aspx/GetFilters` | (we already use this) |
| `/content-pages.aspx/GetPagesAutocomplete` | autocomplete |

### Content Page Form: per-component XHRs (BYPASS SaveContentPage)
Discovered in `content-page-form.js`. These DIRECTLY mutate a single component without a SaveContentPage roundtrip. They overturn the prior "all component edits are client-side staged" hypothesis from verify_delete_via_save.py — Sierra has BOTH paths.

| Endpoint | Method | Likely params |
|---|---|---|
| `/content-page-form.aspx/UpdatePageComponentData` | POST | `{componentId, data}` |
| `/content-page-form.aspx/UpdatePageComponentTitle` | POST | `{componentId, title}` |
| `/content-page-form.aspx/UpdateComponentParagraphs` | POST | `{componentId, paragraphs}` |
| `/content-page-form.aspx/ReOrderPageComponents` | POST | `{pageId, componentIds[]}` |
| `/content-page-form.aspx/AddPageContentLabelLink` | POST | `{pageId, contentLabelId}` |
| `/content-page-form.aspx/AddPagePropertyDetailLinkAssociation` | POST | property-detail page binding |
| `/content-page-form.aspx/RemovePagePropertyDetailLinkAssociation` | POST | (same) |
| `/content-page-form.aspx/SearchGeographicLocationsByRegion` | POST | for location-page autocomplete |
| `/content-page-form.aspx/CheckLocationPageAssociationUniqueness` | POST | dupe check |

### Saved Searches (24 endpoints in manage-saved-searches.js)
| Endpoint | Notes |
|---|---|
| `/saved-searches.aspx/CreateSavedSearchForAutomation` | **Automation-friendly create** — Sierra exposes this endpoint by name |
| `/saved-searches.aspx/GetRegionsForAutomation` | Automation-friendly region picker |
| `/saved-searches.aspx/DeleteSavedSearch` | single delete |
| `/saved-searches.aspx/DeleteSavedSearches` | bulk delete |
| `/saved-searches.aspx/DuplicateSavedSearchPage` | duplicate saved search with its page |
| `/saved-searches.aspx/CopySavedSearches` | bulk copy |
| `/saved-searches.aspx/MergeSavedSearches` | merge multiple into one |
| `/saved-searches.aspx/UpdateSavedSearchName` | rename only |
| `/saved-searches.aspx/CheckDuplicateSavedSearch` | uniqueness check |
| `/saved-searches.aspx/GetSavedSearchDependencies` | **Lists pages using this saved search — answers the relationship-mapping question directly without the graph build** |
| `/saved-searches.aspx/GetTotalPagesWithSavedSearchWidget` | count of pages |
| `/saved-searches.aspx/GetTotalPagesWithSavedSearchWidgetInBulk` | bulk count |
| `/saved-searches.aspx/AddSavedSearchesAsMarketSubsets` | adds market-subset analytics |
| `/saved-searches.aspx/CheckCanCreateCommunityPages` | capacity guard |
| `/saved-searches.aspx/GetSavedSearchStatistic` / `UpdateSavedSearchStatistic` | analytics |
| `/saved-searches.aspx/SetClientDeletionStatusForSavedSearches` | soft-delete |
| `/saved-searches.aspx/SetUpdateManuallyStatusForSavedSearches` | manual-update toggle |
| `/saved-searches.aspx/ValidateSavedSearchesForMerge` | merge precheck |

**Field-level Saved-Search CRUD lives under `/lead-detail.aspx/*`** (architecturally a saved search belongs to a lead). From `common-search-form.js`:

| Endpoint | Body | Notes |
|---|---|---|
| `/lead-detail.aspx/SaveSearchRecord` | `JSON.stringify(searchRecord)` | create+update — full record; `id` distinguishes |
| `/lead-detail.aspx/GetSavedSearchRecord` | `{searchId, siteId}` | read one |
| `/lead-detail.aspx/GetSearchFormData` | `{siteId, regionId}` | form defaults + dropdown options |
| `/lead-detail.aspx/GetPropertiesCount` | the search record | preview matching property count |
| `/saved-searches.aspx/GetLocationsByRegion` | `{siteId, mlsRegionIds, locationType, searchTerm, limit}` | location autocomplete |

**External (API-key) endpoint:** Sierra serves per-region form templates from `https://api.sierrainteractivedev.com/searchForms/{regionId}/?siteid={siteId}&displayOn=caa` with header `Sierra-ApiKey: 80C65D08-FB82-4FF8-86E8-0002DF0E8E31`. Treat as public — appears to be cached / CDN-fronted.

### Shared HTML Widgets (7 endpoints in html-widgets.js)
| Endpoint | Notes |
|---|---|
| `/shared-html-widgets.aspx/SaveHtmlWidget` | **Both create AND update — id=-1 = create, id=N = update** |
| `/shared-html-widgets.aspx/RemoveHtmlWidget` | delete |
| `/shared-html-widgets.aspx/GetHtmlWidget` | single read (we already use this) |
| `/shared-html-widgets.aspx/GetHtmlWidgetList` | full list |
| `/shared-html-widgets.aspx/GetActiveHtmlWidgets` | only active |
| `/shared-html-widgets.aspx/GetSiteHtmlWidgetPermissions` | permission read |
| `/shared-html-widgets.aspx/GetAgentSites` | agent-site picker |

### Blog Manager (17 endpoints in blog-manager.js)
| Endpoint | Notes |
|---|---|
| `/blog-manager.aspx/AddBlog` | create blog |
| `/blog-manager.aspx/DeleteBlogPosts` | bulk delete posts |
| `/blog-manager.aspx/DeleteCategories` | delete category |
| `/blog-manager.aspx/UpdateCategory` | edit category |
| `/blog-manager.aspx/UpdateBlogTag` | edit tag |
| `/blog-manager.aspx/UpdateBlogPostsCategories` | bulk category assign |
| `/blog-manager.aspx/BulkActionBlogPostsTags` | bulk tag apply |
| `/blog-manager.aspx/RemoveBlogTags` | remove tag |
| Plus reads: `GetBlogPostList`, `GetBlogPostContentSummary`, `GetCategories`, `GetCategoriesList`, `GetBlogTagList`, `GetPostsByTag`, `GetFilters`, `GetAgentSites`, `GetAllSites` |

### Blog Post Form (18 endpoints in blog-post-form.js)
Parallels content-page-form.js with `Post` instead of `Page`:
| Endpoint | Notes |
|---|---|
| `/blog-post-form.aspx/SaveBlogPost` | create + update (single endpoint, id=-1 for create) |
| `/blog-post-form.aspx/AddPostComponentLink` | add component to post |
| `/blog-post-form.aspx/RemovePostComponentLink` | remove component (direct endpoint, parallels Content Page) |
| `/blog-post-form.aspx/ReOrderPostComponents` | reorder |
| `/blog-post-form.aspx/UpdatePostComponentData` | direct per-component data update |
| `/blog-post-form.aspx/UpdatePostComponentTitle` | direct per-component title update |
| `/blog-post-form.aspx/AddBlogPostTagLink` / `RemoveBlogPostTagLink` | tag attach/detach |
| `/blog-post-form.aspx/AddBlogTag` / `AddBlogCategory` | tag/category creation |
| `/blog-post-form.aspx/DeleteBlogPostPhoto` / `UpdatePhotoFileName` | photo ops |
| `/blog-post-form.aspx/UpdateBlogPostStatus` | publish toggle |
| `/blog-post-form.aspx/AddPageContentArea` | shared with content-page-form |
| `/blog-post-form.aspx/UpdateBlogPostComment` / `GetBlogPostComments` | comment moderation |
| `/blog-post-form.aspx/GetBlogPostInfo` / `GetBlogTagsAndCategories` | reads |

### Integrations (17 endpoints) — important for the Mert / CRM-import work
| Endpoint | Notes |
|---|---|
| `/integrations.aspx/UpdateFub` | Follow Up Boss connection config |
| `/integrations.aspx/FubCheckApiKey` | FUB API key validation |
| `/integrations.aspx/ImportUsersFromFub` | **Direct FUB → Sierra user import** (built-in!) |
| `/integrations.aspx/UpdateZapier` | Zapier integration |
| `/integrations.aspx/UpdateBombbomb` | BombBomb video email |
| `/integrations.aspx/UpdateRealtorComIntegration` | Realtor.com leads |
| `/integrations.aspx/UpdateZillow` | Zillow leads |
| `/integrations.aspx/UpdateFacebook` / `UpdateFacebookLeadAds` | Facebook integrations |
| `/integrations.aspx/UpdateGoogleAdwords` / `UpdateGoogleAnalytics` / `UpdateBing` | ad networks |
| `/integrations.aspx/UpdateSierra` | core Sierra settings |
| `/integrations.aspx/UpdateShilo` | (unknown — Shilo is a Sierra integration partner) |
| `/integrations.aspx/ActivateEmailService` | email service |
| `/integrations.aspx/GetSettings` | read all settings |
| `/integrations.aspx/AcceptUsePolicy` | acceptance |

### Action Plans (drip/automation engine — 17 endpoints)
| Endpoint | Notes |
|---|---|
| `/action-plans.aspx/CreateActionPlan` / `UpdateActionPlan` / `DeleteActionPlan` / `CopyActionPlan` / `DeleteActionPlans` | full CRUD |
| `/action-plans.aspx/ImportActionPlans` / `ImportPersonalPlan` | **Cross-account import — relevant for CRM-migration tooling** |
| `/action-plans.aspx/PauseActionPlans` / `ResumeActionPlans` | runtime control on lead's plan |
| `/action-plans.aspx/FindActionPlans` / `LoadActionPlansChangelog` | discovery |
| `/action-plans.aspx/GetTemplates` / `GetResellers` / `GetLeadTags` | dropdown reads |
| `/action-plans.aspx/SetActionableTime` | scheduling override |
| `/action-plans.aspx/GetAutomationNamesUsedByActionPlanId` | dependency check |

### Lead surface (BIG — 65 endpoints in leads.aspx, 129 in lead-detail.aspx)
**Not documented per-endpoint here** — see `sierra-map/forms/js_bundle_endpoints.md` for the complete list. To inspect: `python -c "import json; d=json.loads(open('sierra-map/forms/js_bundle_endpoints.json',encoding='utf-8').read()); [print(u) for u in sorted(d['by_url']) if u.startswith('/lead')]"`.

### Other admin surfaces with endpoint counts
| Domain | Endpoint count |
|---|---|
| `lead-detail.aspx` | 129 |
| `leads.aspx` | 65 |
| `content-pages.aspx` | 25 |
| `saved-searches.aspx` | 24 |
| `inbox.aspx` | 20 |
| `agent-sites.aspx` | 19 |
| `email-templates.aspx` | 19 |
| `user-form.aspx` | 19 |
| `blog-post-form.aspx` | 18 |
| `action-plans.aspx` | 17 |
| `blog-manager.aspx` | 17 |
| `content-page-form.aspx` | 17 |
| `integrations.aspx` | 17 |
| `checklists.aspx` | 16 |
| `agents.aspx` | 15 |
| `lead-tags.aspx` | 15 |
| `testimonials.aspx` | 15 |
| `voice-and-text-settings.aspx` | 15 |
| `lead-ponds.aspx` | 14 |
| `revaluate.aspx` | 13 |
| `task-manager.aspx` | 12 |
| `user-manager.aspx` | 12 |
| `sierra-ai.aspx` | 11 |

**Total: 642 endpoints across 47 domains.** See `sierra-map/forms/js_bundle_endpoints.md` for the full table.

---

## Public (consumer-site) lead-capture endpoints — NOT the admin backend
Discovered 2026-05-31 by live recon of `www.gardnergrouprealtors.com` (homepage JS:
`assets/dist/js/common.js`, `content-components.js`, `common-above-fold.js`). These are the
**unauthenticated, public** classic-ASP handlers the IDX consumer site posts to. They write
leads straight into the same Sierra CRM you see at `leads.aspx` (and stamp a lead source).
Distinct from the admin `*.aspx/*` JSON endpoints above.

| Public endpoint | Triggered by | Creates/affects |
|---|---|---|
| `/shared/global/sicm/widgets/contact_form/process.asp` | **Contact form** submit (`form.js-contact-form`, AJAX; visible `action` is just the `/contact/thank-you/` redirect) | **New lead** + emails recipient |
| `/shared/global/sicm/widgets/contact_form/overlay.asp` | renders the contact widget (serves the per-load `token`) | — |
| `/property-search/sist_ajax/sign_in_register_process.asp` | **Register / sign up for account** | **New lead** (account) |
| `/property-search/sist_ajax/social_register_process.asp` | Google/Facebook social signup | **New lead** |
| `/property-search/sist_ajax/login_process.asp` · `social_login_process.asp` · `reset_password.asp` · `logout_process.asp` | account auth | session |
| `/property-search/sist_ajax/saved_search_process.asp` | save a search | lead + saved search |
| `/property-search/sist_ajax/save_new_listing_alert.asp` | "email me new listings" | lead + alert |
| `/property-search/sist_ajax/save_listing.asp` | favorite a listing | lead activity |
| `/property-search/sist_ajax/leadtag_process.asp` | lead tagging | tag |
| `/property-search/sist_ajax/save_qs_query.asp` · `get_search_count.asp` · `get_locations.asp` · `get_qs_target_url.asp` | quick-search | search (no lead) |

**Contact form payload (field names from live DOM, form id `sicmForm405589`):**
hidden `pageid`, `sectionid`, `recipient`, `Sent_From`, `token`, `subject`, `form_type`;
visible `First_Name`, `Last_Name`, `Email`, `Phone`, `Questions`. NOTE: the unlabeled
`your_questions` text input is a **honeypot** (leave blank). `token` is a per-page-load
anti-CSRF/anti-spam value served by `overlay.asp` — an external custom form must fetch a
fresh token first (or go server-to-server / admin `AddLead` / Zapier instead).

**Custom-form options** (see handoff for full analysis):
1. **Same-site widget** — build a Sierra content page with the native contact widget (token handled for us). Inherits existing lead routing automatically.
2. **External form → `contact_form/process.asp`** — must GET `overlay.asp` for a token first.
3. **Server-to-server admin `AddLead`** (`/lead-detail.aspx/AddLead`) — full field control; creds stay server-side (this is the `mert_transfer` path).
4. **Zapier** (`/integrations.aspx/UpdateZapier`) — no creds, supported, slight latency.

> Downstream CRM: client works leads in **Keller Williams Command**, not FUB. A custom form
> that deposits into Sierra (opts 1–3) inherits whatever Sierra→KW bridge already carries
> native website leads into Command. How that bridge works today (Zapier / parse-email /
> native source) is the next thing to confirm before wiring an external form.

---

## Calling pattern

```python
# Inside a SierraClient session (cookies live in the page context):
result = sierra.page.evaluate(
    """async (body) => {
        const r = await fetch('/content-page-form.aspx/GetPage', {
            method: 'POST', credentials: 'include',
            headers: {
                'Content-Type': 'application/json; charset=UTF-8',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest',
            },
            body,
        });
        return { status: r.status, text: await r.text() };
    }""",
    json.dumps(payload),
)
```

Outer-then-inner unwrap:

```python
outer = json.loads(result["text"])              # {"d": "..."}
inner = json.loads(outer["d"]) if isinstance(outer["d"], str) else outer["d"]
data = inner["data"] if inner.get("responseCode") == 0 else None
```
