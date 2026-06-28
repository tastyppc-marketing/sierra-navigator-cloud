# Phase 2 RE-AUDIT #4 — 2026-06-28 (PARTIAL — Anthropic session limit hit mid-run)

Run `wf_7143fcfa-602` · 84 agents started · **INCOMPLETE**: ~22 refute agents + the
closure/synthesis agent failed on "You've hit your session limit · resets 9:30am PT", so
there is no synthesized report and several `not_closed` re-flags are low-confidence (their
refute panels never ran). A CLEAN re-audit #5 is required after the limit resets.

Counts (partial): 19 closed, #15 deferred-accepted. not_closed (low confidence): #1, #13,
#14, New-1, RA3-1, RA3-2 — note #14/New-1/RA3-1/RA3-2 were closed/deferred in re-audit #3.

## Fresh-confirmed (REAL, actionable) -> Wave 7
- **HIGH (tools_generic.py:124)** — W6-T1's exact base-form token match misses INFLECTED/
  gerund destructive verbs: `Disabling`!=`Disable`, `Expiring`!=`Expire`, `Releasing`!=
  `Release`. 3 more real catalogued endpoints classify `write` and commit via sierra_call
  with no snapshot/identity-lock: TestVoiceAndTextDisablingFinish,
  TestVoiceAndTextTrackManualDisabling, TestVoiceAndTextNotifyExpiringNumbers.
  Fix: match destructive STEMS (would-be-write branch); keep exact-token for reads.
- **MEDIUM (client.py:90)** — Tier-2 generic write commit `client.call(path, body,
  write=True)` never asserts a positive Sierra ack (W6-T2's _assert_mutation_ack is only on
  the typed write methods), so a generic-write soft-rejection reports committed.
  Fix: assert the ack in call() when write=True.
- **HIGH/deploy (auth.py:68)** — the documented `uvicorn ...:app --host <X>` launcher binds
  the socket from CLI --host independent of SIERRA_MCP_BIND_HOST that the no-auth gate reads;
  `ALLOW_NO_AUTH=1 BIND_HOST=127.0.0.1 uvicorn --host 0.0.0.0` exposes delete tools unauth.
  NOT the shipped container path (python -m sierra_mcp.server). Fix: make `python -m` the
  only documented launcher; drop/qualify uvicorn --host examples.

See handoff 015 (Wave 7) for the fix plan. Re-audit #5 (clean, full) pending the session reset.
