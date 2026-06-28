# Implementation notes

## Deferred: full create / edit content-page writes (live-testable follow-up)

The MCP write surface (Phase 2b-ii) exposes the **simple, known-payload** guarded
writes plus the identity-locked delete flow:

- `create_content_label` / `update_content_label` / `remove_content_label`
- `add_page_content_label_link` / `remove_page_content_label_link`
- `update_page_component_title`
- `propose_deletions` -> `confirm_deletions` (content pages: hard/irreversible;
  saved searches: soft/recoverable)

It does **not** yet expose full page create/edit (e.g. `create_listing_page` /
`edit_page`). Those map to `sierra_core.save_content_page` (and the analogous
`save_saved_search` / `save_html_widget`), whose request bodies are the rich
`SaveContentPage`-style page-with-components/associations payloads. Those methods
exist in `sierra_core/client.py` and are unit-tested against `FakeTransport`, but
their **exact live payload shape needs verification against a real Sierra tenant**
before we expose them as MCP tools. They are intentionally a separate,
live-testable follow-up (Phase 2c) with the rich payload builder.

## Guard layer

Write/delete safety (confirm tokens, scopes, volume caps) lives in
`sierra_mcp/guards.py`; the immutable audit log + recovery ledger + token store
live in `sierra_mcp/audit.py` (stdlib `sqlite3`). The tool layer
(`sierra_mcp/tools_write.py`) is the dry-run -> confirm machine that ties them to
`sierra_core`'s `allow_write=True` client via `SierraRuntime.write` / `.delete`.

## Follow-up: audit guard *rejections*

The guards correctly **enforce** every rule, and successes + identity-lock
*aborts* are audited. But guard **rejections** raised before any Sierra contact —
`ConfirmTokenError` (reuse/expiry/mutated payload), `ScopeError`, `VolumeCapError`
— currently propagate **without** an audit row. For full forensics (e.g.
"someone tried to replay a delete token"), wrap the guard calls (cleanest: a
single chokepoint in `server.py`'s tool wrappers) to write a `result="refused"`
audit row before re-raising. Enforcement is already correct; this is logging only.

(`sierra_call`'s own refusals — an un-catalogued path, or a locked-destructive
op — follow the same pattern: they raise `ValueError` before any Sierra contact
and are not yet audited.)

## Tier-2 generic caller (`sierra_call`)

`sierra_mcp/tools_generic.py` exposes `sierra_call(path, body, confirm_token)` —
a guarded escape hatch over the whole 642-endpoint catalogue, so a new Sierra op
is reachable without new code. Fences: (1) **allowlist** = `catalogue.endpoint_paths()`;
(2) **classification** by method prefix → scope (Get/List/Find/Check/Load/Validate/
Search/Count = read; Delete* = delete; else write); (3) **locked-destructive
refusal** for `DeleteContentPage`, `DeleteSavedSearch`, and any `Duplicate*`
(routed to the identity-locked Tier-1 `propose_deletions`/`confirm_deletions`);
(4) writes/deletes reuse the Tier-1 `guarded_write` (dry-run→confirm + audit +
caps). It rests on the one intentional `sierra_core` addition,
`SierraHttpClient.call(path, body, *, write=False)`. Bodies are posted **verbatim**
(no `siteId` coercion — the caller owns the exact field shapes); the result of a
generic call is the **raw unwrapped payload**, not a shaped value.
