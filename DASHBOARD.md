# Cleanup dashboard (LOCAL REVIEW BUILD)

> ⚠ **LOCAL REVIEW ONLY — NOT FOR PRODUCTION.**
> This is a delete-capable web UI with **no web authentication**. It is bound to
> `127.0.0.1` and must **never** be exposed on a network. This is a *first cut*,
> flagged for visual review, and is **expected to change**.

A small, self-contained Starlette app that renders a server-side UI over the
**same guarded delete tools** the MCP server already exposes
(`propose_deletions` / `confirm_deletions`). Its only job is to let the operator
*see and shape* the identity-locked verified-delete UX. It adds no new trust:
every delete safety control already lives in the tools it calls.

## Run it

```bash
python -m sierra_mcp.dashboard
# then open http://127.0.0.1:8090
```

(Interpreter used in this repo:
`C:/Users/mjfos/AppData/Local/Microsoft/WindowsApps/python.exe`.)

It binds to `127.0.0.1:8090`. Reading the two lists requires a working Sierra
session (the same `SierraRuntime` the MCP server uses); deletes go through the
identical guard stack.

## Why local-only (the safety framing)

A browser dashboard that can delete records must be behind real auth. The
deployed MCP server is **bearer-token (WorkOS)** protected — fine for an MCP
client, but a browser needs a WorkOS **web session**, which is a **Phase-3**
item. Until that exists, this dashboard is a *separate, local-only* app:

- its own entrypoint (`python -m sierra_mcp.dashboard`),
- bound to `127.0.0.1`,
- **not** imported by or mounted on `sierra_mcp/server.py` (the public MCP
  server), and
- a loud banner on every page:
  *"⚠ LOCAL REVIEW BUILD — delete-capable, no web auth; do not expose.
  Production auth = WorkOS web session (Phase 3)."*

A test (`tests/sierra_mcp/test_dashboard.py`) asserts `server.py` never
references `dashboard`, so the public server can never accidentally pull it in.

## What it does

Three server-rendered pages (no SPA, no JS build step; one tiny inline filter
helper). All dynamic text is HTML-escaped.

| Route | Method | Tool it calls | Page |
|-------|--------|---------------|------|
| `/`, `/cleanup` | GET | `list_content_pages`, `list_saved_searches` (via the runtime) | Two tables — **Content pages** and **Saved searches** — each `id · title · status · reversible?` with a checkbox per row, inside a `POST /preview` form. |
| `/preview` | POST | `propose_deletions(entity_type, ids)` | Confirmation page: candidates with **id + stored_title side by side**, a **reversible vs IRREVERSIBLE** badge per row, fetch-error rows shown but not deletable, the one-time `confirm_token` + `expires_at` in hidden fields, and each `stored_title` echoed into a hidden `expected_title` (the identity-lock echo). |
| `/confirm` | POST | `confirm_deletions(token, entity_type, confirmations)` | Results page: per-row **PASS / ABORTED / ERROR**, deleted?, reversible, and a plain-language explanation of the identity-lock outcome. |

### Delete semantics (unchanged from the tools)

- **Content pages** → **HARD, IRREVERSIBLE** delete. A recovery snapshot is
  written to the ledger first, but Sierra cannot restore the page.
- **Saved searches** → **soft / recoverable** delete.
- Every row is **identity-locked**: the live stored title must match the echoed
  `expected_title`, or that row aborts (no delete, no snapshot) without failing
  the rest of the batch.
- Nothing is sent to Sierra until you click **Confirm** on the preview page.

## Notes for the reviewer

- This is a **first cut for visual review** — styling and flow are expected to
  change. The point is to react to the UX.
- Row id/title fields are read **defensively** (Sierra list rows vary by
  endpoint): id from `id|pageId|contentPageId|savedSearchId|searchId`, title
  from `name|title|searchName|pageName|…`, status from `statusName|status|…`.
  If a column shows `(untitled)` / `—`, the row simply lacked that key.
- Production hardening still owed: **WorkOS web-session auth** (Phase 3), then
  this could be mounted (or kept separate) behind that gate.
