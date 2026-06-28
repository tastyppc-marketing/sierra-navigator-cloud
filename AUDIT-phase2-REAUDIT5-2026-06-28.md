# Phase 2 RE-AUDIT #5 — 2026-06-28 (adversarial, post-Wave-7) — GO

Run `wf_7b139e9e-039` (after the session-limit reset) · **78 agents** · 6.25M tokens · ~37 min. CLEAN run.

**Outcome: GO for the SUPERVISED MANUAL first deploy.** 25/29 closed, #15 deferred-accepted; ALL CRITICAL/HIGH from re-audits #1-#5 CLOSED, no new CRIT/HIGH, no Wave-4..7 regression. Wave 7 verified (inflected verbs, generic-write-ack, launcher docs). 4 MEDIUM residuals remain, none a deploy blocker: NEW catalogue-resources-bypass-allowlist (FIXED W8-T1), #10 force-refresh login stampede (availability), #14 short-secret value-residual (durable fix = deferred encryption-at-rest), RA4-3 raw-uvicorn dev-mode footgun (doc-complete, no runtime guard).

This is the convergence point after 5 audits (~420 agents) + Waves 4-8. See handoff 015.

---

All three decision-critical claims verify against the code: server.py:421-430 (resource handlers call no `authorize`, unlike `_guarded_read` at :55-64), session.py:158-176 (both cache checks gated on `if not force_refresh:`, so the force path logs in unconditionally), and auth.py:37-93 (gate keys off `resolved_bind_host()` env var, independent of uvicorn's CLI `--host`). Writing the report.

## Executive summary

All CRITICAL and HIGH findings from re-audits #1–#5 are CLOSED, and no new CRITICAL/HIGH blocker was introduced. Wave 7's three targets are verified: W7-T1 (inflected destructive forms — `Disabling`/`Expiring`/`Releasing` now caught mid-name; the 3 voice-and-text endpoints refuse) is closed via RA4-1/RA3-1; W7-T2 (`_assert_mutation_ack` now runs in the generic `call(..., write=True)` commit path) is closed via RA4-2/RA3-2/#9; W7-T3 (single documented launcher; misleading `uvicorn --host` examples removed) is **doc-complete but only doc-mitigated** — the underlying gate/socket divergence survives as RA4-3 (PARTIAL), a no-auth **dev-mode-only** footgun that does not touch the documented production deploy path. No Wave-4..7 change regressed any prior CLOSED finding (mint_token's migration into `transaction()` strengthened #3; #14 is strictly more redaction; the CI gate reduced #15's surface). Three findings remain PARTIAL — #10 (session-refresh stampede, availability), #14 (short-secret redaction residual, durable fix = deferred encryption-at-rest), RA4-3 (raw-uvicorn no-auth dev exposure) — all MEDIUM. One new finding is confirmed: catalogue MCP resources bypass the subject allowlist and the audit trail (MEDIUM, static shipped data only). None of these is a CRITICAL/HIGH blocker for the supervised manual deploy.

## Prior findings — closure status

| id | severity | status | one-line evidence |
|----|----------|--------|-------------------|
| #1 | CRITICAL | closed | Denylist→allowlist inversion + fail-closed default-deny (tools_generic.py:150-165); 642-endpoint replay = 0 delete-substring methods in read/write; 44 adversarial probes all refused. |
| #2 | HIGH | closed | Bulk*/Delete* refused at classification before any commit; cap-never-ran concern moot (refused, not capped); 19 Bulk* + all delete-containing methods refused. |
| #3 | HIGH | closed | One process-wide RLock + WAL + busy_timeout; all writers inside `transaction()`; mint_token migrated in; no off-lock shared-conn access. |
| #4 | HIGH | closed | DEPLOY.md proves 401 before public Caddy exposure; fail-closed on missing AUTHKIT_DOMAIN; no-auth only on loopback; gate+bind share `resolved_bind_host()`. |
| #5 | HIGH | closed | Authz derives from validated WorkOS token, fail-closed allowlist, enforced on every read/write/delete; boot gate blocks auth-enabled empty allowlist. |
| #6 | HIGH | closed | `sierra_call` sole arbitrary-path tool; all 47 Delete*-prefix + 49 contains-delete endpoints refused before Sierra contact; real deletes only via identity-locked propose/confirm. |
| #7 | HIGH | closed | Remove/Merge are leading destructive verbs → refused (18/18, 0 as write); Get/Check/Validate carve-out preserved. |
| #8 | MEDIUM | closed | All four guard-denial categories write `result="rejected"` audit row before re-raising at every tool entry. |
| #9 | HIGH | closed | Three-layer raise (transport 4xx/5xx, parsing fault/nonzero rc, `_assert_mutation_ack`) on typed + Tier-2 writes; ack failure → `result="error"`, ledger not flipped. |
| #10 | MEDIUM | **partial** | `force_refresh=True` skips both cache checks (session.py:160,168) → unconditional login per caller; serialized login storm on shared-session server-side expiry (availability DoS on one credential). |
| #11 | MEDIUM | closed | `check_and_reserve` does read→check→write inside `self._lock` (guards.py:244-260); TOCTOU closed; 100-thread race grants exactly cap. |
| #12 | HIGH | closed | Bulk*/Delete*/Deletion* refused first → never reach weaker write scope/caps; 71 such endpoints refused, 0 as write. |
| #13 | HIGH | closed | No-auth (return None) only when domain unset AND opt-in AND loopback (auth.py:71-93); gate+uvicorn bind share `resolved_bind_host()`; container default 0.0.0.0 fails closed. |
| #14 | MEDIUM | **partial** | `_redact` now covers all three state columns + value scrub; residual = short non-token secret under arbitrary key stored verbatim (accepted; durable fix = deferred encryption-at-rest). |
| #15 | LOW | deferred-accepted | deploy.yml gated by `DEPLOY_ENABLED` + `production` environment + no PR trigger; mutable-tag actions / root SSH remain as tracked pre-enable hardening. |
| #16 | LOW | closed | Canonical `/mcp` is exact Route (no slash-redirect, 401 with auth); `/mcp/`→307→`/mcp`; docs consistent; smoke-curl 401 gate reliable. |
| #17 | MEDIUM | closed | `mask_error_details=True` masks all raised exceptions; returned dicts carry only `type(e).__name__`; zero ToolError channel; verbatim repr only to immutable audit DB. |
| New-1 | CRITICAL | closed | CamelCase token-boundary so `Can`≠`Cancel`; Cancel+Disable/Release added as leading destructive verbs → refused; both Cancel* endpoints refused. |
| New-2 | HIGH | closed | `"Deletion"` fragment is load-bearing — SetClientDeletionStatusForSavedSearches refused; without it leading `Set` would classify write. |
| New-3 | HIGH | closed | All 10 Tier-1 read tools route through `_guarded_read`→`authorize` before `runtime.read`; allowlist enforced fail-closed on reads. |
| New-4 | MEDIUM | closed | `authorize()` audits denial via `token_subject()` (never raises) before re-raising; every tool path leaves a `rejected` row on denial. |
| New-5 | MEDIUM | closed | Ledger `payload_snapshot` stored verbatim (no `_redact`) for recovery fidelity; audit_log still scrubbed; independent tables. |
| RA2-1 | MEDIUM | closed | New top-level fault guard raises when `d` is None + outer fault, before degrade-to-None; surfaced as error on all paths, never ok/empty; no regression to d-wrapped envelopes. |
| RA3-1 | HIGH | closed | Destructive verb token ANYWHERE in a would-be write → refused; 116 destructive endpoints all refused (incl. 6 voice-and-text); read-led queries correctly kept read. |
| RA3-2 | HIGH | closed | `_assert_mutation_ack` on all 9 non-delete writes + both deletes + Tier-2; both Message casings preserved → ack raises; reaches `result="error"` not `"ok"`. |
| RA3-3 | MEDIUM | closed | `_capturing_sink` short-circuits on captured `ledger_id` → exactly one ledger row across the re-auth retry; no orphan. |
| RA4-1 | HIGH | closed | `_verb_forms` inflections catch Disabling/Expiring mid-name in would-be writes; 3 targets now refused; 0 destructive reach write; exact-member match prevents over-refusal. |
| RA4-2 | MEDIUM | closed | Generic `call(write=True)` runs `_assert_mutation_ack` (same helper as typed); responseCode:0+Message → EndpointError → `result="error"`; parity achieved. |
| RA4-3 | MEDIUM | **partial** | Doc fix complete (no runnable raw-uvicorn launcher), but no runtime guard — raw `uvicorn …:app --host 0.0.0.0` with BIND_HOST=127.0.0.1+ALLOW_NO_AUTH=1 binds a public no-auth socket (dev mode only; production AUTHKIT_DOMAIN path unaffected). |

## Blockers

No CRITICAL/HIGH blockers remain — all CRITICAL/HIGH findings are closed. Three findings persist as PARTIAL (all MEDIUM); none blocks the supervised manual deploy, but each is a tracked follow-up:

- **#10 — Session-refresh stampede (MEDIUM, availability).** Verified at session.py:158-176: when `force_refresh=True`, line 160 skips the fast path AND line 168 skips the under-lock double-check, so line 173 `self._login_fn()` runs unconditionally per caller. Via the one shared `SessionBroker` (context.py:46-55 → server.py:52) and concurrent sync-tool worker threads, a burst of K requests sharing a session that expires server-side each take `call_with_refresh`'s `invalidate()` + `get_session(force_refresh=True)` branch (runtime.py:78-79), producing K serialized full logins against the single shared Sierra credential — risk of provider rate-limit/lockout (total-service blast radius). The lock serializes them (not simultaneous) and later threads pick up the fresh session, so it is mitigated, not eliminated. **Fix:** in the force_refresh branch, capture the stale session reference and, under the lock, return `self._session` if it is non-None and not the stale one (object-identity/generation check) instead of re-logging-in.

- **RA4-3 — Raw-uvicorn no-auth bind divergence (MEDIUM, dev-mode only).** Verified at auth.py:37-44,62-93: the no-auth gate keys off `resolved_bind_host()` (the `SIERRA_MCP_BIND_HOST` env var), independent of uvicorn's CLI `--host`. With the documented dev combo (AUTHKIT_DOMAIN unset, `ALLOW_NO_AUTH=1`, `BIND_HOST=127.0.0.1`), `build_auth()` returns None (auth off) at import while `uvicorn sierra_mcp.server:app --host 0.0.0.0` binds the real socket on all interfaces — exposing the delete-capable surface unauthenticated. **Not reachable on the production deploy path** (docker compose → Dockerfile:28 CMD → `main()` → `resolved_bind_host()`; AUTHKIT_DOMAIN set ⇒ real AuthKitProvider regardless of bind), and W7-T3 removed the misleading examples, so this is confined to a deliberate dev-mode operator deviation. **Fix:** add a fail-closed ASGI startup/lifespan guard that inspects the actually-bound socket(s) and aborts if any is non-loopback while auth is disabled (or refuse to import `app` in no-auth mode outside `main()`) — a cheap runtime control replacing operator doc-discipline for an irreversible-delete system.

- **#14 — Short-secret audit redaction residual (MEDIUM, accepted; fix deferred).** The fix is present and strictly increases redaction (all three state columns + value-level scrub). The reachable residual is the finding's own accepted limitation: a short, non-token secret passed under an arbitrary key (e.g. `sierra_call(body={"vault_pin":4821})`) matches neither `_SECRET_KEY_RE` nor `_SECRET_VALUE_RE` and is stored verbatim in the immutable audit_log. The only durable fix is **encryption-at-rest**, which is an explicitly DEFERRED user key-mgmt decision; over-redacting all short strings was deliberately rejected. Tracked MEDIUM, not a deploy blocker.

## New findings

**Catalogue resources bypass the subject allowlist AND the audit trail (MEDIUM, authz-identity).** Confirmed against code: `sierra_endpoints` (server.py:421-424) and `sierra_endpoints_verified` (server.py:427-430) return the full 642-endpoint Sierra admin XHR map and the verified request-body reference with **no `context.authorize()` call** — unlike `_guarded_read` (server.py:55-64), which is the New-3 (allowlist-on-reads) and #8/New-4 (denial-is-audited) remediation. WorkOS AuthKit only validates the JWT; per-subject authorization is exclusively `context.authorize`'s job. So an authenticated-but-non-allowlisted principal — the exact actor #8/New-4 defends against — is correctly denied every tool (audited `rejected`) yet can issue `resources/read` for `resource://sierra/endpoints[/verified]` and exfiltrate the complete internal admin API surface with **zero audit rows**, defeating both named controls for the resource surface.

Severity calibration: the leaked artifact is STATIC shipped data (the API map + documented request bodies), not live records, PII, or secrets, and no mutation is reachable this way — hence MEDIUM (a reviewer could argue LOW; the lens votes were LOW/LOW/none). It is nonetheless an unaudited reconnaissance map of every destructive endpoint, reachable in the production config (AUTHKIT_DOMAIN + allowlist set).

**Recommendation:** wrap both `@mcp.resource` handlers with the same `context.authorize(get_conn(), tool=…, action="read", scope="read")` gate used by `_guarded_read`, so the allowlist is consulted and a denial writes a `rejected` audit row. Cheap, localized, and closes the last ungated read surface.

## Deploy recommendation

**GO — for the SUPERVISED MANUAL first deploy.**

Rationale: every CRITICAL/HIGH finding is closed and verified, and the documented production path is fail-closed end-to-end — docker compose → Dockerfile:28 CMD → `main()` → `resolved_bind_host()`, with DEPLOY.md §3 requiring AUTHKIT_DOMAIN (auth ON), the §4 `curl …/mcp` ⇒ 401 proof-gate before any public exposure, and docker-compose.yml binding only `127.0.0.1:8080`. None of the three PARTIALs or the new finding is reachable as a CRITICAL/HIGH compromise on that path:

- RA4-3 requires no-auth **dev** mode (AUTHKIT_DOMAIN unset) + a raw `uvicorn --host` deviation — excluded by the production config and the single documented launcher.
- #10 is an availability concern under high concurrent session-expiry — low risk for a watched, low-concurrency first deploy.
- #14 and the new catalogue-resource finding are MEDIUM information-handling issues over static/non-secret data.

Conditions to attach to the supervised deploy (not blockers): (1) deploy ONLY via `python -m sierra_mcp.server` / docker compose with AUTHKIT_DOMAIN set and a non-empty `SIERRA_MCP_SUBJECT_ALLOWLIST` — never raw `uvicorn …:app --host`; (2) confirm the §4 401 proof-gate before appending the public reverse-proxy; (3) prioritize the four MEDIUM follow-ups before unsupervised/scaled operation — authorize-gate the two catalogue resources (new finding), add the fail-closed bound-socket startup guard (RA4-3), coalesce the force_refresh login path (#10), and enable audit/ledger encryption-at-rest (#14, the deferred key-mgmt item). #15 (CI hardening — SHA-pinned actions, Environment approval gate, forced-command deploy key) remains an accepted deferral for the manual deploy and is NOT a blocker.