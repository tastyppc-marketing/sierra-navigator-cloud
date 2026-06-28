# Phase 2 adversarial audit — 2026-06-28 (ultracode, 89 agents)

Find → 3-lens skeptic refutation → confirm. **21 confirmed of 27 raised** (6 refuted).
Run `wf_28fbb9d5-79d`. This is the pre-deploy hardening backlog; **must-fix items block the first deploy.**

## CRITICAL
1. **Identity-lock bypass via Tier-2 `sierra_call`** (`tools_generic.py`) — the locked set denylists only `DeleteContentPage`/`DeleteSavedSearch` (singular). Plural/batch + alternate paths (`DeleteContentPages`, `DeleteSection`, `DeleteSavedSearches`, lead `DeleteSavedSearch`) reach `client.call(write=True)` raw — **no title check, no snapshot** → can hard-delete the WRONG production page. The exact Gardner-incident class the lock exists to prevent. → **default-deny destructive in Tier-2.**
2. **Batch/`Bulk*Delete*` unbounded** (`tools_generic.py`) — `enforce_delete_call_cap` never runs on Tier-2; one `BulkDeleteLeads`/`DeleteContentPages` call destroys an unbounded set, counted as n=1 vs the session cap. (Same root as #1.)
3. **Shared sqlite conn bricks the safety subsystem under concurrency** (`audit.py:75` + `context.py:30-39`) — one process-wide `check_same_thread=True` connection, but FastMCP dispatches sync tools on a worker-thread pool → `sqlite3.ProgrammingError` on confirm-token mint/redeem, audit writes, ledger snapshots once two calls overlap; the conn singleton never resets, so once the owning worker dies the **entire write/delete/audit subsystem is permanently broken until restart.** → **thread-safe DB access.**
4. **DEPLOY.md exposes the unauthenticated server publicly while deletes are live** (`DEPLOY.md`) — runbook adds the public Caddy route BEFORE auth is enforced; anyone reaching `/mcp/` in that window can `propose_deletions`→`confirm_deletions` to irreversibly delete prod pages. → **reorder: auth first, expose second; no-auth only on localhost bind.**

## HIGH
5. **Authn ≠ authz** (`context.py:28,54-65`) — the validated token is never consulted; every caller gets a hardcoded read+write+delete grant, and the audit actor is the constant `"operator"` (no non-repudiation after an irreversible delete). → derive scopes + actor (sub/email) from the access token; require a `sierra:*` scope; subject allowlist.
6. **45/47 `Delete*` endpoints run via `sierra_call` with no snapshot** (delete scope granted unconditionally). (Closed by #1 default-deny.)
7. **`classify()` under-classifies `Remove*`/`Merge*` as write** (`RemoveHtmlWidget`, `MergeSavedSearches`, `MergeLeads`) — destructive, but only write scope, no lock, no snapshot, audited as benign "write". → destructive-verb set.
8. **Guard rejections write NO audit row** (`tools_write.py`, `tools_generic.py`) — payload-tamper, token replay, cap trip, scope denial, locked-destructive refusal all silently refused → an attacker can probe guardrails invisibly. → centralize reject-auditing.
9. **Swallowed Sierra errors reported as success** (`transport.py:27-29`, `parsing.py`) — `HttpxTransport` doesn't `raise_for_status`; a failed/500 `DeleteContentPage` is reported `deleted:True / PASS`, audited `ok`, ledger stamped deleted. Reads degrade a 500 to `{"rows":[],"count":0}` ("site has zero pages"). → raise on `r.is_error` + ASP.NET error envelope.
10. **`SessionBroker` cache unsynchronized** (`session.py:148-163`) — concurrent expiry → login stampede against the single Sierra credential (lockout risk) + `invalidate()`/`get_session` TOCTOU `AttributeError`. → lock + single-flight login.
11. **`VolumeTracker.check_and_reserve` non-atomic** (`guards.py:245-258`) — the cap bounding irreversible deletes is exceedable under concurrency (2/3). → lock.
12. **`Bulk*Delete*` classify as write** (`tools_generic.py`) — wrong scope/caps; also a Phase-3 privilege-escalation (write-not-delete tenant can still mass-delete). (Closed by #1/#7.)

## MEDIUM
13. No-auth + blank `AUTHKIT_DOMAIN` + leftover flag silently boots public no-auth (`auth.py:44-61`). → honor no-auth only on loopback bind.
14. Redaction covers only `args_redacted`; `before_json`/`after_json`/`payload_snapshot` stored VERBATIM and the regex misses `authorization|cookie|bearer|session|jwt|api_key` (`audit.py`). → redact all three + broaden regex.
15. CI auto-deploys to prod with a root SSH key via mutable-tag actions, no approval gate (`deploy.yml`). → SHA-pin + GitHub Environment review gate + forced-command key.

## LOW
16. `/mcp` vs `/mcp/` → 307 (docs/health curl mismatch) (`server.py:425`). → canonicalize.
17. FastMCP error masking off → raw internal exceptions to the client (`server.py:25`). → `mask_error_details=True`.

## Refuted (6, not acted on): post-effect audit-ordering (1/3), append-only tamper-evidence (1/3), 300-char expiry truncation (0/3), redeem BEGIN IMMEDIATE fold (0/3), Dockerfile WORKDIR ownership (0/3), .dockerignore creds (0/3).

## Remediation plan (subagent-driven, reviewed; before deploy)
- **HARDEN-1** — Tier-2 default-deny destructive + fail-closed classification (#1,2,6,7,12).
- **HARDEN-2** — core robustness: thread-safe sqlite/broker/tracker (#3,10,11) + error-surfacing (#9).
- **HARDEN-3** — authz from token + actor (#5), guard-rejection audit (#8), redaction (#14), no-auth-loopback + DEPLOY reorder (#4,13), `mask_error_details` + `/mcp` canonical (#16,17), CI SHA-pin/gate (#15).
- Re-run the audit to confirm criticals/highs closed.
