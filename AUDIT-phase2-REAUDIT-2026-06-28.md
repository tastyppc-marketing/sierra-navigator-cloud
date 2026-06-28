# Phase 2 RE-AUDIT — 2026-06-28 (adversarial, post-remediation)

Run `wf_fe6095fd-859` · **107 agents** · 7.95M tokens · 1396 tool calls · ~42 min.
Method: closure-verify each of 17 prior findings (read code, try to bypass) -> independent skeptic on non-closed verdicts; 8-dimension fresh sweep -> 3-lens refutation (correctness/security/concurrency, survive on >=2/3). Inputs were the remediated tree at commits e35aa6f..5a31fd2 (268 tests green).

**Outcome: NO-GO.** 5 prior findings fully closed (#3 #6 #11 #12 #16), 1 deferred-accepted (#15), 11 partial; 8 fresh-confirmed findings (1 CRITICAL, 4 HIGH, incl. dups by dimension). See blockers + GO checklist below. This drives remediation **Wave 4**.

---

## Executive summary

No — not all CRITICAL/HIGH findings are closed, and this re-audit surfaces a new CRITICAL plus three new HIGH findings. The remediation pass is real and load-bearing: 5 prior findings are fully closed and adversarially un-bypassable (#3 concurrency/DB-lock, #6 Delete* default-deny, #11 VolumeTracker atomicity, #12 Bulk*Delete* refusal, #16 /mcp canonicalization), and several partials (notably the bulk-delete cap and the documented single-flag no-auth case) are genuinely fixed. However, a single classifier-soundness defect — `classify()` using `str.startswith()` over short verb prefixes (`tools_generic.py:85`) — survives the remediation and is the root cause behind a CRITICAL in-band bypass: the `"Can"` read-prefix swallows `Cancel*`, so two catalogued production mutation endpoints execute live with only `read` scope, no confirm token, no volume cap, and a falsified `scope='read'` audit row. The same loose-matching class also lets `SetClientDeletionStatusForSavedSearches` ("Deletion" ≠ substring "Delete") soft-delete saved searches as a plain `write`, bypassing delete scope, identity-lock, snapshot and the recovery ledger. Independently, the **two safety nets for the irreversible content-page delete are both compromised** — failed deletes still report `identity:"PASS"` (#9) and the sole recovery snapshot is silently over-redacted, destroying `metaKeywords` and the page password (New-5). The no-auth gate also keys off a decorative env var rather than the enforced socket (#4/#13), so a documented `.env` recipe yields a public, unauthenticated, delete-capable server. **Blocker:** the destruction/authz/recovery residuals below must be fixed before any network-exposed deploy; most are small, localized code changes.

## Prior findings — closure status

| id | residual severity | status | one-line evidence |
|----|------|--------|-------------------|
| #1 | CRITICAL | partial | Core Delete-refusal closed; residual = `Can` prefix swallows `Cancel*` → mutation classified `read` (`tools_generic.py:33,85`). |
| #2 | HIGH | partial | Bulk-delete cap robustly closed (all 49 Delete*/19 Bulk* refused); residual = same `Cancel*`-as-read leak. |
| #3 | — (was HIGH) | **closed** | `transaction()` holds module RLock across BEGIN IMMEDIATE→commit; WAL+busy_timeout; 20×8-thread stress passes (`audit.py:73-94,115-121`). |
| #4 | CRITICAL | partial | No-auth gate uses advisory `SIERRA_MCP_BIND_HOST` (`auth.py:53-54`), never the real `--host 0.0.0.0` (`Dockerfile:26`) → public unauth delete. |
| #5 | HIGH | partial | Empty `SIERRA_MCP_SUBJECT_ALLOWLIST` (shipped default) fails OPEN: any valid WorkOS token → full `{read,write,delete}` (`context.py:61-65,74,85`). |
| #6 | — (was HIGH) | **closed** | `_is_destructive` checked first; all 49 Delete*-bearing catalogue paths refused before Sierra contact (`tools_generic.py:70-73,83`). |
| #7 | HIGH | partial | New destructive verbs added & refused; root-cause loose `startswith` prefix match unfixed → `Cancel*` lands on the less-guarded read path. |
| #8 | MEDIUM | partial | `require_scope(...,"read")` at `tools_generic.py:142` is outside `_audit_guard_rejections` → ScopeError/PermissionError refusal leaves no audit row. |
| #9 | HIGH | partial | Delete success = absence-of-raise; HTTP-200 non-`d` ASP.NET fault & business-rule rejections don't raise → false `identity:"PASS"` + ledger flipped deleted (`client.py:403/438`). |
| #10 | MEDIUM | partial | TOCTOU/cold-start stampede closed; `force_refresh=True` skips both fast path & double-check (`session.py:160,168`) → N relogins/expiry on one shared credential → lockout. |
| #11 | — (was HIGH) | **closed** | `check_and_reserve` does read-check-write under one `threading.Lock` (`guards.py:253-260`); no reachable bypass. |
| #12 | — (was HIGH) | **closed** | Destructive verb refused before write branch; `Bulk*Delete*` priv-esc to write-scope unreachable (`tools_generic.py:83,38`). |
| #13 | CRITICAL | partial | Same class as #4: asserted loopback bind diverges from hardcoded `0.0.0.0`; `ALLOW_NO_AUTH=1`+`BIND_HOST=127.0.0.1` (env.example recipe) → unauth delete. |
| #14 | MEDIUM | partial | `_redact` matches dict KEY names only (`audit.py:222`); secrets under innocuous keys persisted verbatim into immutable, trigger-locked audit_log. |
| #15 | HIGH | **deferred-accepted** | Unpinned mutable-tag actions, unrestricted root deploy key, no approval gate — intentionally deferred, documented (`deploy.yml:16-17`, `DEPLOY.md:101-105`). |
| #16 | — (was MEDIUM) | **closed** | Server canonicalizes `/mcp` (no-token → 401 directly); test-locked via real ASGI routing (`test_server_smoke.py:72-79`). |
| #17 | LOW | partial | `mask_error_details=True` masks raised exceptions only; `propose_/confirm_deletions` embed `repr(e)` into returned dicts (`tools_write.py:332,459,478`) — short class+message disclosure. |

## Blockers

Everything below is NOT closed (all partials) plus every new CONFIRMED CRITICAL/HIGH. Grouped by root cause; severity tagged.

### A. Verb-classifier unsoundness — `startswith()` over short prefixes (root cause of #1, #2, #7, New-1, New-2)

- **New-1 / #1 / #2 / #7 — CRITICAL.** Residual risk: `_READ_VERBS` contains `"Can"` and `classify()` uses `method.startswith(...)` (`tools_generic.py:33,85`), so `/lead-detail.aspx/CancelScheduledMessage` and `/facebook-relogin.aspx/CancelNotification` (both in the 642-path allowlist) classify `read`. The read branch calls `runtime.read(lambda c: c.call(path, body))` with default `write=False`, which skips `_ensure_write` (`client.py:59-61`) so the POST fires to production. A caller with only `read` scope cancels a queued outbound lead message / dismisses a notification — no dry-run, no confirm token, no VolumeTracker cap — and it is logged as `scope='read', result='ok'`, hiding the mutation. This is reachable in-band regardless of deploy config and auto-extends to any future `Can*/Cancel*` catalogue entry.
  - **Fix:** match verbs on exact CamelCase token boundaries (require end-of-string or an uppercase char after the matched prefix) or use an explicit verb→method map; add a mutating/destructive denylist (`Cancel, Disable, Release, Revoke, Void, Expire, Terminate, Archive, Deactivate, Unpublish`) checked before the read/write branches. This flips both `Cancel*` endpoints to `refused`.

- **New-2 — HIGH.** Residual risk: `_is_destructive` gates on `"Delete" in method`, but `"Delete"` is not a substring of `"Deletion"`, and the method starts with `Set` (a write verb), so `/saved-searches.aspx/SetClientDeletionStatusForSavedSearches` — documented as a soft-delete (`data/API_ENDPOINTS.md:380`) — classifies `write`. A caller with `write` (not `delete`) scope soft-deletes saved searches in a plural/batch commit through `guarded_write`, with NO delete-scope check, NO title identity-lock, NO pre-delete snapshot, and NO recovery-ledger row — the exact protections the dedicated delete flow exists to provide.
  - **Fix:** the same denylist (include `Deletion`/`SetClientDeletionStatus*`) routes this to `refused` (or to the delete flow). Treat name-fragment soft-delete verbs as destructive, not write.

### B. Auth/identity gate not bound to the enforced socket; fail-open defaults (#4, #13, #5, New-3)

- **#4 / #13 — CRITICAL.** Residual risk: `is_loopback` is derived from operator-asserted `SIERRA_MCP_BIND_HOST` (`auth.py:53-54`), never from uvicorn's hardcoded `--host 0.0.0.0` (`Dockerfile:26`). Setting `ALLOW_NO_AUTH=1` + `BIND_HOST=127.0.0.1` (the dev pairing in `env.example:23-24`) returns `None` auth while binding `0.0.0.0`; `context.py:92-93` then grants `{read,write,delete}` to every caller, exposing unauthenticated `confirm_deletions`. Caddy `reverse_proxy 127.0.0.1:8080` (`DEPLOY.md:70`) makes the loopback port-map non-containing, and a Docker-network sibling reaches `0.0.0.0:8080` directly. The `DEPLOY.md:11-12` "no-auth honored only on loopback bind" guarantee is false.
  - **Fix:** make `SIERRA_MCP_BIND_HOST` the single source of truth actually passed to `uvicorn --host` (or have `build_auth()` inspect the real bound socket) and refuse to start no-auth unless the *bound* host is loopback. Stop documenting `ALLOW_NO_AUTH=1 + BIND_HOST=127.0.0.1` as a copyable block.

- **#5 — HIGH.** Residual risk: with AuthKit enabled but allowlist unset (shipped default, `env.example:17`), `_require_allowed`'s `if allow and not(...)` short-circuits to no denial and `_scopes_from_claims` always returns full `{read,write,delete}` — so any subject WorkOS will mint a token for gets irreversible delete. Only operator discipline (`DEPLOY.md:44`) closes it.
  - **Fix:** when `AUTHKIT_DOMAIN` is set, treat an empty allowlist as fail-closed — refuse to boot, or downgrade to read-only.

- **New-3 — HIGH.** Residual risk: the 10 Tier-1 read tools (`server.py:53-233`) call `runtime.read(...)` directly and never invoke `granted_scopes()`/`actor()`/`_require_allowed()`. The subject allowlist — the server's headline per-identity gate — is enforced on writes/deletes and on the `sierra_call` read path but NOT on the dedicated read tools. Any non-allowlisted principal with a valid WorkOS token reads the entire backend (content pages, saved-search lead criteria, widget HTML/JS, blog posts, taxonomy). The identical read via `sierra_call` is refused at `tools_generic.py:142`, proving the gate exists but covers only one of two equivalent read paths.
  - **Fix:** route every Tier-1 read through a common guarded path that calls `granted_scopes()`/`_require_allowed()` (and records `actor()`), mirroring the write/delete and `sierra_call` read sites.

### C. Delete correctness & recovery integrity — both safety nets for the irreversible delete are compromised (#9, New-5)

- **#9 — HIGH.** Residual risk: `client.py:403/438` hardcode `{"deleted": id}` and discard `_call`'s return; only an exception prevents `identity:"PASS"`. Two unraised failure shapes survive: (1) a top-level non-`d` ASP.NET fault `{Message,StackTrace,ExceptionType}` at HTTP 200 (`parsing.py:28` → `d=None`, guard at `:51-52` unreachable); (2) HTTP-200 business-rule rejection (`Message`-only returns the dict; `responseCode:0` strips message → `{}`) — exactly the modal-body delete errors CLAUDE.md rule 4 warns about. Result: a failed delete reports PASS, flips the recovery ledger to `deleted=True`, and writes `result='ok'`.
  - **Fix:** verify a positive success marker in the unwrapped delete response inside `client.py` delete methods; do not treat absence-of-raise as success.

- **New-5 — HIGH.** Residual risk: the recovery `payload_snapshot` (the sole record before an irreversible content-page hard delete) is stored as `_redact(...)` (`audit.py:402,468`), whose broad key-name regex (`audit.py:46-49`) stars `metaKeywords` (matches `key`) and `password`. Per `data/API_ENDPOINTS.md:72` both are real content-page fields, contradicting the docstring claim that snapshots are stored verbatim. A hard-deleted page can't be fully reconstructed (keywords gone) and restoring a password-protected page yields one with no access password (confidentiality regression).
  - **Fix:** store the recovery `payload_snapshot` verbatim (it is the recovery record), or apply value-aware redaction that excludes recovery-critical fields like `metaKeywords`/`password`.

### D. Audit / non-repudiation gaps — guard rejections probeable invisibly (#8, New-4, #14)

- **#8 + New-4 — MEDIUM.** Residual risk: `require_scope(granted_scopes(),"read")` at `tools_generic.py:142` sits outside `_audit_guard_rejections`, and `actor()/granted_scopes()` run before/outside the wrapper at every write/delete site (`tools_write.py:114,313,408`); the wrapper catches only `GuardError` while the allowlist denial is a builtin `PermissionError` (`context.py:74`). Net: an authenticated-but-not-allowlisted subject can hammer every write/delete tool and the destructive generic caller and leave ZERO rows in the immutable audit_log — defeating the "guardrails can't be probed invisibly" invariant.
  - **Fix:** wrap `tools_generic.py:142` in an auditing try, move `actor()/granted_scopes()` inside the audited block, and broaden the audited-rejection catch to include `PermissionError`.

- **#14 — MEDIUM.** Residual risk: `_redact` scans dict KEY names only (`audit.py:222`); a secret under a non-matching key (`credentials`, `pwd`, `signature`, `pin`, etc.) is persisted verbatim into the trigger-locked, unscrubable audit_log. Reachable with only `read` scope via `sierra_call` body and via pre-delete `payload_snapshot`/write-response payloads carrying embed/widget code.
  - **Fix:** add value-level secret scanning (high-entropy / token-pattern detection) and broaden the key list; document that pre-serialised JSON strings bypass redaction.

### E. Availability (#10)

- **#10 — MEDIUM.** Residual risk: concurrent server-side session expiry drives N back-to-back `_login_fn()` calls against one shared Sierra credential (`session.py:172-175`), serialized but not coalesced — rate-limit/lockout (whole-server outage) plus N×login-RTT tail latency; routes through reads, writes, and irreversible deletes (`runtime.py:79`).
  - **Fix:** in the `force_refresh` path, capture the stale session observed and, inside the lock, return `self._session` if a peer already replaced it (object-identity/generation check) instead of re-logging-in.

### F. Information disclosure (#17)

- **#17 — LOW.** Residual risk: `propose_deletions`/`confirm_deletions` embed `repr(e)` (upstream ASP.NET `Message`, JSON-parse internals, identity-lock detail, sqlite errors) into returned result dicts, which `mask_error_details` does not cover. `EndpointError.raw` (≤300-char body) is NOT exposed, so this is short class+message disclosure only.
  - **Fix:** sanitize the `error` field in returned candidate/result dicts to a generic message (preserve full detail only in the audit DB).

## New findings

- **New-1 — CRITICAL (3/3 lenses real; tier2-destruction / delete-safety / input-validation).** `Cancel*` endpoints classify as `read` via the `"Can"` prefix collision and execute live with only `read` scope, no confirm token, no volume cap, and a falsified `scope='read'` audit row (`tools_generic.py:33,85`; reachable at `/lead-detail.aspx/CancelScheduledMessage`, `/facebook-relogin.aspx/CancelNotification`). This is the residual keeping #1/#2/#7 partial. **Fix:** exact CamelCase-token verb matching + a `Cancel`-family mutating/destructive denylist.
- **New-2 — HIGH (3/3).** `SetClientDeletionStatusForSavedSearches` ("Deletion" not caught by substring "Delete") classifies `write`, soft-deleting saved searches in batch via `guarded_write` with no delete scope, identity-lock, snapshot, or ledger row (`tools_generic.py:73`). **Fix:** add `Deletion`/`SetClientDeletionStatus*` to the destructive set; route to refused or the delete flow.
- **New-3 — HIGH (3/3).** Tier-1 read tools never consult the subject allowlist (`server.py:53-233`), so a non-allowlisted WorkOS principal reads the whole production backend; the same read via `sierra_call` is gated (`tools_generic.py:142`). **Fix:** gate all Tier-1 reads through `_require_allowed()`/`granted_scopes()`.
- **New-5 — HIGH (3/3).** The sole recovery snapshot for an irreversible content-page delete over-redacts `metaKeywords` and `password` to `***` (`audit.py:402,46-49`), making the irreversible delete non-reconstructable and dropping page access passwords on restore. **Fix:** store recovery `payload_snapshot` verbatim or value-aware-redact excluding recovery-critical fields.
- **New-4 — MEDIUM (2/2).** Subject-allowlist `PermissionError` denials are never written to audit_log on any mutating or refused path (`context.py:74`; wrappers catch only `GuardError`, and `actor()/granted_scopes()` run outside them) — authenticated-but-unauthorized callers probe the full delete-capable surface with no forensic trace. **Fix:** audit `PermissionError` denials (see #8 fix).

## Deploy recommendation

**NO-GO** for any network-exposed supervised manual first deploy in the current code state. (#15 CI hardening is an accepted deferral and is explicitly NOT counted here — but no `VPS_*`/auto-deploy secret may be added until it is closed.)

The system's defining risk is LIVE, IRREVERSIBLE production deletes, and this re-audit shows **both** delete safety nets compromised (failed deletes report PASS, #9; the only recovery snapshot is silently lossy, New-5), an **in-band unguarded production mutation reachable with read scope regardless of config** (New-1), a **write-scope destructive soft-delete bypass** (New-2), a **read-surface authz bypass of the headline allowlist** (New-3), and a **public-unauthenticated-delete misconfiguration reachable from a documented `.env` recipe** (#4/#13). These are small, localized fixes; none require redesign.

**Exact blockers to clear before GO (in priority order):**
1. Fix `classify()` to exact CamelCase-token verb matching + explicit mutating/destructive denylist (`Cancel*`, `Deletion`/`SetClientDeletionStatus*`, `Disable/Release/Revoke/Void/Expire/Terminate/Archive`). Closes **New-1, New-2** and the residuals of **#1/#2/#7**. (CRITICAL/HIGH)
2. Tie the no-auth gate to the actual uvicorn bind (or refuse `ALLOW_NO_AUTH` unless the bound host is verifiably loopback). Closes **#4/#13**. (CRITICAL)
3. Require a positive success marker in `client.py` delete methods. Closes **#9**. (HIGH)
4. Store the recovery `payload_snapshot` verbatim (or value-aware-redact excluding `metaKeywords`/`password`). Closes **New-5**. (HIGH)
5. Gate all Tier-1 read tools through the subject allowlist. Closes **New-3**. (HIGH)
6. Audit `PermissionError`/scope denials at every tool entry. Closes **#8 / New-4**. (MEDIUM)

**Mandatory config preconditions even after the code fixes (operator-verified during the supervised run):** set `AUTHKIT_DOMAIN`; set a non-empty `SIERRA_MCP_SUBJECT_ALLOWLIST`; never set `ALLOW_NO_AUTH=1` in production; and pass the `DEPLOY.md:55-58` 401 auth-proof gate before exposing any public route.

**Conditional narrow exception:** a loopback-isolated (no reverse-proxy, no public route) smoke test that performs ZERO destructive actions and exercises only Tier-1 reads could proceed before fixes, since the destruction/recovery blockers (#9, New-5, New-2) bite only on the delete path and the auth blockers are moot when nothing but localhost can reach the port. Anything beyond that — any public ingress or any delete — is NO-GO until items 1–5 land.

**Lower-priority (track, not first-deploy blockers):** #10 (relogin coalescing — availability), #14 (value-aware redaction), #17 (sanitize returned error dicts), and #15 (CI SHA-pinning + environment approval gate + forced-command deploy key — close before enabling auto-deploy).