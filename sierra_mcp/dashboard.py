"""LOCAL REVIEW dashboard for the identity-locked verified-delete flow.

⚠ LOCAL REVIEW BUILD — delete-capable, **no web auth**; do NOT expose.
================================================================================
This is a self-contained, server-rendered Starlette app (``dashboard_app``) that
drives the SAME guarded tools the MCP server exposes, so an operator can SEE and
shape the cleanup UX. It is a **first cut, flagged for review** — expected to
change.

It is deliberately a *separate* app:

* bound to ``127.0.0.1`` only,
* **NOT** imported by :mod:`sierra_mcp.server` (which is bearer-token / WorkOS
  protected), and
* carrying a loud banner on every page.

Why local-only: a delete-capable browser UI must never be served
unauthenticated. The deployed MCP server is bearer-token (WorkOS) protected; a
browser dashboard needs WorkOS **web-session** auth, which is a Phase-3 item.
Until then this runs on localhost for the operator's eyes only. All delete
*safety* (two-step preview→confirm, hash-pinned one-time tokens, the per-row
identity lock, recovery snapshots, volume caps, audit trail) already lives in
the tools this UI calls — the UI adds no new trust.

Routes
------
* ``GET  /`` (and ``GET /cleanup``) — two tables (content pages + saved
  searches), id + title side by side, a checkbox per row, one ``POST /preview``
  form per entity type.
* ``POST /preview`` — ``propose_deletions(entity_type, ids)``: a dry-run that
  live-fetches each record and mints a confirm token. Renders the candidates
  (id + stored_title side by side, a reversible/IRREVERSIBLE badge, fetch-error
  rows shown but not deletable) with the token + each stored_title echoed into a
  hidden ``expected_title`` field (the identity-lock echo).
* ``POST /confirm`` — ``confirm_deletions(token, entity_type, confirmations)``:
  per-row PASS / ABORTED / ERROR result.

Run::

    python -m sierra_mcp.dashboard          # then open http://127.0.0.1:8090
"""
from __future__ import annotations

import html
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from sierra_mcp import context, tools_read, tools_write

HOST = "127.0.0.1"
PORT = 8090

BANNER_TEXT = (
    "⚠ LOCAL REVIEW BUILD — delete-capable, no web auth; do not expose. "
    "Production auth = WorkOS web session (Phase 3)."
)


# --------------------------------------------------------------------------- #
# defensive row-shape helpers (Sierra list rows vary; render id + title safely)
# --------------------------------------------------------------------------- #

# Candidate keys, in priority order, that Sierra uses for a row's id / title /
# status across the content-page and saved-search list shapers. We render
# defensively because the exact key differs per endpoint.
_ID_KEYS = ("id", "pageId", "contentPageId", "savedSearchId", "searchId", "ID")
_TITLE_KEYS = (
    "name", "title", "searchName", "pageName", "pageTitle", "displayName", "fileName",
)
_STATUS_KEYS = ("statusName", "status", "statusText", "pageStatus", "statusId")


def _first(row: Any, keys: tuple[str, ...]) -> Any:
    """First non-empty value among ``keys`` in ``row`` (else ``None``)."""
    if isinstance(row, dict):
        for k in keys:
            v = row.get(k)
            if v not in (None, ""):
                return v
    return None


def esc(value: Any) -> str:
    """HTML-escape any value (``None`` -> empty string)."""
    return html.escape("" if value is None else str(value))


# --------------------------------------------------------------------------- #
# entity registry — the two delete-capable entity types
# --------------------------------------------------------------------------- #

def _list_content_pages() -> dict:
    return context.get_runtime().read(
        lambda c: tools_read.list_content_pages(c, page_size=500)
    )


def _list_saved_searches() -> dict:
    return context.get_runtime().read(
        lambda c: tools_read.list_saved_searches(c, page_size=500)
    )


# Order here is the on-page render order (content pages first).
_ENTITIES: dict[str, dict[str, Any]] = {
    "content_page": {
        "label": "Content pages",
        "reversible": False,  # hard delete
        "list": _list_content_pages,
    },
    "saved_search": {
        "label": "Saved searches",
        "reversible": True,  # soft delete
        "list": _list_saved_searches,
    },
}


def _rev_badge(reversible: bool) -> str:
    """A reversible (soft) vs IRREVERSIBLE (hard) badge."""
    if reversible:
        return '<span class="badge rev">reversible (soft)</span>'
    return '<span class="badge irrev">IRREVERSIBLE (hard)</span>'


def _identity_badge(identity: str) -> str:
    cls = {"PASS": "pass", "ABORTED": "abort", "ERROR": "err"}.get(identity, "err")
    return f'<span class="badge {cls}">{esc(identity)}</span>'


# --------------------------------------------------------------------------- #
# page shell (static CSS; kept as a plain string so braces need no escaping)
# --------------------------------------------------------------------------- #

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font: 15px/1.5 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       margin: 0; color: #1a1a1a; background: #f4f5f7; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 0 20px 60px; }
.banner { background: #7a0010; color: #fff; font-weight: 700; letter-spacing: .2px;
          padding: 12px 20px; text-align: center; position: sticky; top: 0; z-index: 9;
          box-shadow: 0 2px 6px rgba(0,0,0,.2); }
h1 { font-size: 22px; margin: 26px 0 4px; }
h2 { font-size: 18px; margin: 30px 0 8px; }
.sub { color: #555; margin: 0 0 18px; }
.section { background: #fff; border: 1px solid #e1e4e8; border-radius: 8px;
           padding: 16px 18px; margin: 16px 0; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
table { width: 100%; border-collapse: collapse; margin: 8px 0 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #eceef1;
         vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: #6a737d;
     border-bottom: 2px solid #e1e4e8; }
tbody tr:hover { background: #fafbfc; }
td.id { font-variant-numeric: tabular-nums; color: #0b5; font-weight: 600; white-space: nowrap; }
td.title { font-weight: 600; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 12px;
         font-weight: 700; }
.badge.irrev { background: #fdecea; color: #a61b00; border: 1px solid #f5c6bd; }
.badge.rev  { background: #e7f6ec; color: #1a7f37; border: 1px solid #b6e2c4; }
.badge.pass { background: #e7f6ec; color: #1a7f37; }
.badge.abort{ background: #fff4e5; color: #9a5b00; border: 1px solid #f5d9a8; }
.badge.err  { background: #fdecea; color: #a61b00; }
.muted { color: #8a929b; }
button { font: inherit; font-weight: 700; cursor: pointer; border: 0; border-radius: 6px;
         padding: 9px 16px; }
button.go { background: #1f6feb; color: #fff; }
button.danger { background: #a61b00; color: #fff; }
a.cancel { display: inline-block; padding: 9px 14px; color: #1f6feb; text-decoration: none;
           font-weight: 600; }
.filter { width: 100%; max-width: 360px; padding: 8px 10px; border: 1px solid #ccd1d6;
          border-radius: 6px; margin: 4px 0 8px; }
.err-row td { background: #fffaf0; color: #9a5b00; }
.note { background: #eef4ff; border: 1px solid #cfe0ff; border-radius: 6px;
        padding: 10px 12px; margin: 10px 0; font-size: 14px; }
.actions { margin-top: 10px; display: flex; gap: 8px; align-items: center; }
pre { white-space: pre-wrap; background: #f6f8fa; padding: 10px; border-radius: 6px;
      border: 1px solid #e1e4e8; overflow:auto; }
code { background: #f0f2f4; padding: 1px 5px; border-radius: 4px; }
"""

_FILTER_JS = """
function __filter(q){
  q=(q||'').toLowerCase();
  document.querySelectorAll('table.rows tbody tr').forEach(function(tr){
    tr.style.display = tr.textContent.toLowerCase().indexOf(q)>=0 ? '' : 'none';
  });
}
"""


def _page(title: str, body: str) -> str:
    """Wrap ``body`` in the shared shell (banner + CSS)."""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{esc(title)} · Sierra cleanup (LOCAL)</title>"
        f"<style>{_CSS}</style></head><body>"
        f'<div class="banner">{esc(BANNER_TEXT)}</div>'
        f'<div class="wrap">{body}</div>'
        f"<script>{_FILTER_JS}</script>"
        "</body></html>"
    )


# --------------------------------------------------------------------------- #
# render: index
# --------------------------------------------------------------------------- #

def _render_list_section(entity_type: str, rows: list, error: str | None) -> str:
    meta = _ENTITIES[entity_type]
    label = meta["label"]
    rev_badge = _rev_badge(meta["reversible"])

    if error is not None:
        return (
            f'<div class="section"><h2>{esc(label)}</h2>'
            f'<div class="note">Could not load {esc(label).lower()}: '
            f"<code>{esc(error)}</code></div></div>"
        )

    body_rows: list[str] = []
    for row in rows:
        rid = _first(row, _ID_KEYS)
        title = _first(row, _TITLE_KEYS)
        status = _first(row, _STATUS_KEYS)
        checkbox = (
            f'<input type="checkbox" name="ids" value="{esc(rid)}">'
            if rid not in (None, "")
            else '<span class="muted">—</span>'
        )
        body_rows.append(
            "<tr>"
            f"<td>{checkbox}</td>"
            f'<td class="id">{esc(rid)}</td>'
            f'<td class="title">{esc(title) or "<span class=muted>(untitled)</span>"}</td>'
            f"<td>{esc(status) or '<span class=muted>—</span>'}</td>"
            f"<td>{rev_badge}</td>"
            "</tr>"
        )

    if not body_rows:
        table = '<p class="muted">No rows returned.</p>'
        submit = ""
    else:
        table = (
            '<table class="rows"><thead><tr>'
            "<th></th><th>id</th><th>title</th><th>status</th><th>reversible?</th>"
            "</tr></thead><tbody>" + "".join(body_rows) + "</tbody></table>"
        )
        submit = (
            '<div class="actions">'
            f'<button class="go" type="submit">Preview deletion of selected '
            f'{esc(label).lower()} →</button>'
            f'<span class="muted">{len(rows)} row(s)</span></div>'
        )

    return (
        f'<div class="section"><h2>{esc(label)} {rev_badge}</h2>'
        f'<form method="post" action="/preview">'
        f'<input type="hidden" name="entity_type" value="{esc(entity_type)}">'
        f"{table}{submit}</form></div>"
    )


def _render_index() -> str:
    sections: list[str] = []
    for entity_type, meta in _ENTITIES.items():
        try:
            res = meta["list"]()
            rows = res.get("rows", []) if isinstance(res, dict) else []
            error = None
        except Exception as e:  # a dead endpoint blanks ONE section, not the page
            rows, error = [], repr(e)
        sections.append(_render_list_section(entity_type, rows, error))

    head = (
        "<h1>Cleanup review</h1>"
        '<p class="sub">Pick rows, preview the identity-locked delete, then '
        "confirm. Nothing is sent to Sierra until you confirm.</p>"
        '<input class="filter" placeholder="Filter rows…" '
        'oninput="__filter(this.value)" aria-label="Filter rows">'
    )
    return _page("Cleanup", head + "".join(sections))


# --------------------------------------------------------------------------- #
# render: preview (candidates -> confirm form)
# --------------------------------------------------------------------------- #

def _render_preview(entity_type: str, proposal: dict) -> str:
    meta = _ENTITIES[entity_type]
    label = meta["label"]
    candidates = proposal.get("candidates", []) if isinstance(proposal, dict) else []
    token = proposal.get("confirm_token", "")
    expires = proposal.get("expires_at", "")

    body_rows: list[str] = []
    deletable = 0
    for cand in candidates:
        cid = cand.get("id")
        if "error" in cand:  # fetch failed -> shown, not deletable
            body_rows.append(
                '<tr class="err-row">'
                f'<td class="id">{esc(cid)}</td>'
                '<td class="muted">(could not fetch — not deletable)</td>'
                f"<td>{_identity_badge('ERROR')}</td>"
                f"<td colspan=1><code>{esc(cand['error'])}</code></td>"
                "</tr>"
            )
            continue
        deletable += 1
        stored_title = cand.get("stored_title")
        reversible = bool(cand.get("reversible", meta["reversible"]))
        # Hidden id + expected_title (the identity-lock echo), emitted one pair
        # per row in row order so confirm can zip getlist('ids')/getlist(...).
        hidden = (
            f'<input type="hidden" name="ids" value="{esc(cid)}">'
            f'<input type="hidden" name="expected_title" value="{esc(stored_title)}">'
        )
        body_rows.append(
            "<tr>"
            f'<td class="id">{esc(cid)}{hidden}</td>'
            f'<td class="title">{esc(stored_title) or "<span class=muted>(untitled)</span>"}</td>'
            f"<td>{_rev_badge(reversible)}</td>"
            "<td class=muted>echoed as <code>expected_title</code></td>"
            "</tr>"
        )

    table = (
        '<table class="rows"><thead><tr>'
        "<th>id</th><th>stored title</th><th>reversible?</th><th>identity lock</th>"
        "</tr></thead><tbody>" + "".join(body_rows) + "</tbody></table>"
    )

    irreversible_warn = (
        '<div class="note"><strong>These are content pages — the delete is '
        "HARD and IRREVERSIBLE.</strong> A recovery snapshot is written to the "
        "ledger first, but Sierra cannot restore the page.</div>"
        if not meta["reversible"]
        else '<div class="note">Saved-search deletes are soft / recoverable by id.</div>'
    )

    if deletable:
        confirm_form = (
            '<form method="post" action="/confirm">'
            f'<input type="hidden" name="entity_type" value="{esc(entity_type)}">'
            f'<input type="hidden" name="confirm_token" value="{esc(token)}">'
            f"{table}"
            '<div class="actions">'
            f'<button class="danger" type="submit">Confirm deletion of {deletable} '
            f'{esc(label).lower()}</button>'
            '<a class="cancel" href="/">Cancel</a></div></form>'
        )
    else:
        confirm_form = (
            f"{table}"
            '<div class="note">Nothing deletable in this selection.</div>'
            '<a class="cancel" href="/">Back</a>'
        )

    head = (
        f"<h1>Confirm: {esc(label)}</h1>"
        '<p class="sub">Step 2 of 2 — review each record’s stored title, '
        "then confirm. The title you see is re-fetched live and echoed back as the "
        "identity lock.</p>"
        f"<p class=muted>confirm token <code>{esc(token)}</code>"
        + (f" · expires <code>{esc(expires)}</code>" if expires else "")
        + "</p>"
    )
    return _page("Confirm", head + irreversible_warn + confirm_form)


# --------------------------------------------------------------------------- #
# render: results
# --------------------------------------------------------------------------- #

def _render_results(entity_type: str, result: dict) -> str:
    meta = _ENTITIES[entity_type]
    label = meta["label"]
    results = result.get("results", []) if isinstance(result, dict) else []

    body_rows: list[str] = []
    n_pass = n_abort = n_err = 0
    for r in results:
        identity = r.get("identity", "ERROR")
        if identity == "PASS":
            n_pass += 1
        elif identity == "ABORTED":
            n_abort += 1
        else:
            n_err += 1
        deleted = "yes" if r.get("deleted") else "no"
        reversible = r.get("reversible")
        rev_txt = (
            _rev_badge(bool(reversible)) if reversible is not None else "<span class=muted>—</span>"
        )
        detail = ""
        if identity == "ABORTED":
            detail = "stored title did not match <code>expected_title</code> — nothing deleted, no snapshot"
        elif identity == "ERROR":
            detail = esc(r.get("error", ""))
        elif identity == "PASS":
            detail = "identity matched — snapshot taken, then deleted"
        body_rows.append(
            "<tr>"
            f'<td class="id">{esc(r.get("id"))}</td>'
            f"<td>{_identity_badge(identity)}</td>"
            f"<td>{esc(deleted)}</td>"
            f"<td>{rev_txt}</td>"
            f"<td>{detail}</td>"
            "</tr>"
        )

    table = (
        '<table class="rows"><thead><tr>'
        "<th>id</th><th>identity</th><th>deleted?</th><th>reversible?</th><th>outcome</th>"
        "</tr></thead><tbody>" + "".join(body_rows) + "</tbody></table>"
    )
    summary = (
        f'<div class="note"><strong>{n_pass} deleted</strong> · '
        f"{n_abort} aborted (identity lock) · {n_err} error(s).</div>"
    )
    head = (
        f"<h1>Result: {esc(label)}</h1>"
        '<p class="sub">PASS = identity matched and the record was deleted. '
        "ABORTED = the echoed title did not match the live stored title, so that "
        "row was skipped (no delete, no snapshot). ERROR = the delete call itself "
        "failed.</p>"
    )
    return _page("Result", head + summary + table + '<a class="cancel" href="/">← Back to cleanup</a>')


# --------------------------------------------------------------------------- #
# error helper
# --------------------------------------------------------------------------- #

def _error_page(title: str, message: str, status: int = 400) -> HTMLResponse:
    body = (
        f"<h1>{esc(title)}</h1><pre>{esc(message)}</pre>"
        '<a class="cancel" href="/">← Back to cleanup</a>'
    )
    return HTMLResponse(_page(title, body), status_code=status)


# --------------------------------------------------------------------------- #
# routes (async handlers call the guarded tools synchronously: in production the
# single asyncio thread keeps the lazily-created sqlite conn thread-consistent)
# --------------------------------------------------------------------------- #

async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(_render_index())


async def preview(request: Request) -> HTMLResponse:
    form = await request.form()
    entity_type = str(form.get("entity_type", ""))
    ids = [str(v) for v in form.getlist("ids")]

    if entity_type not in _ENTITIES:
        return _error_page("Unknown entity type", f"entity_type={entity_type!r}")
    if not ids:
        return HTMLResponse(
            _page(
                "Preview",
                "<h1>Nothing selected</h1>"
                '<p class="sub">Tick at least one row to preview a deletion.</p>'
                '<a class="cancel" href="/">← Back to cleanup</a>',
            )
        )
    try:
        proposal = tools_write.propose_deletions(entity_type, ids)
    except Exception as e:
        return _error_page("Preview failed", repr(e))
    return HTMLResponse(_render_preview(entity_type, proposal))


async def confirm(request: Request) -> HTMLResponse:
    form = await request.form()
    entity_type = str(form.get("entity_type", ""))
    token = str(form.get("confirm_token", ""))
    ids = [str(v) for v in form.getlist("ids")]
    titles = [str(v) for v in form.getlist("expected_title")]

    if entity_type not in _ENTITIES:
        return _error_page("Unknown entity type", f"entity_type={entity_type!r}")
    confirmations = [
        {"id": i, "expected_title": t} for i, t in zip(ids, titles)
    ]
    try:
        result = tools_write.confirm_deletions(token, entity_type, confirmations)
    except Exception as e:
        return _error_page("Confirm failed", repr(e))
    return HTMLResponse(_render_results(entity_type, result))


routes = [
    Route("/", index, methods=["GET"]),
    Route("/cleanup", index, methods=["GET"]),
    Route("/preview", preview, methods=["POST"]),
    Route("/confirm", confirm, methods=["POST"]),
]

# The local-only ASGI app. NOTE: intentionally NOT mounted by sierra_mcp.server.
dashboard_app = Starlette(routes=routes)


def main() -> None:
    """Run the LOCAL review dashboard on 127.0.0.1:8090."""
    import uvicorn

    print("=" * 72)
    print(BANNER_TEXT)
    print(f"  -> http://{HOST}:{PORT}   (bound to localhost; do NOT expose)")
    print("=" * 72)
    uvicorn.run(dashboard_app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
