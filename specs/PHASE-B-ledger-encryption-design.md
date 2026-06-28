# Phase B — Ledger encryption-at-rest design (for #14 + New-5)

> Source: ultraplan-local research (encryption-design agent), 2026-06-28. Plan ref: `C:\Dev\Scraper Creator\specs\todo\016-2026-06-28-phase2-deploy-and-residuals-plan.md` §Phase B. This is the execution-ready spec; the only USER decision is the key location (Option 1/2/3) + generating one Fernet key for Option 1.

## Why encryption (not redaction)
`ledger.payload_snapshot` is the full VERBATIM pre-delete backup (`audit.py` `ledger_record` `_as_json(payload_snapshot)`, no `_redact`; via `make_snapshot_sink`). New-5 made it verbatim on purpose — it's the sole recovery record for an irreversible delete (page password, widget token, metaKeywords). Redaction breaks recovery (W4-T4 / `test_ledger_snapshot_fidelity.py` locks). Encryption-at-rest keeps full fidelity while removing plaintext secrets from the `.db`.

## Key facts (verified in-repo)
- ONE write chokepoint: all snapshot writes funnel through `ledger_record` → encrypt there covers everything.
- Almost no readers today: only the two fidelity tests read the column; dashboard renders status not body; `restore_from_snapshot` doesn't exist yet → near-zero-risk to add decryption.
- `cryptography` not declared in requirements.txt (likely transitive via fastmcp→authlib); declare it directly.
- Env-injection precedent: `SIERRA_PASSWORD` (sierra_core/config.py) — a ledger key fits the same mold.
- Keep `audit.py` stdlib-only in the no-key path → LAZY import of cryptography.

## Threat model
Defends the realistic single-VPS leak paths: `.db` exfiltration, backups, disk/VM snapshots, accidental commit. Does NOT defend root-on-box (the key is co-located). That's the correct scope for "encryption at rest" and exactly the #14 residual. KMS (Option 3) is the only one resisting box compromise — overkill for the operator MVP, the Phase-3 target.

## KEY-MANAGEMENT DECISION (USER) — gates implementation
All three share the same code seam (`_encrypt_snapshot`/`_decrypt_snapshot`/`_ledger_cipher`); only where `_ledger_cipher` sources the key differs (~8 lines + runbook).
1. **Env var `SIERRA_MCP_LEDGER_KEY` (Fernet) ⭐ RECOMMENDED** — injected via box `.env` like `SIERRA_PASSWORD`; zero new infra; rotatable via MultiFernet; trivially testable. Con: key readable via /proc/environ, co-located with DB.
2. **Key file `/root/sierra-mcp/ledger.key` (chmod 600)** — slightly smaller exposure; but diverges from the env-secret pattern and easily lands in the same backup as the `.db`.
3. **External KMS / cloud secret** — strongest (resists box compromise), central rotation/audit; heavy (cloud acct, IAM, KMS-unreachable failure mode). The Phase-3 answer, not the MVP.

Recommendation: **Option 1.** Consistent with `SIERRA_PASSWORD`, closes the realistic threat model, ~40 lines + one dep, clean upgrade path to KMS later (swap only `_ledger_cipher` key source, bump marker to `enc:kms:v1:`).

## Implementation (Option 1)
1. `requirements.txt`: `cryptography>=43`.
2. `audit.py` seam (lazy import; cached on the raw env string; MultiFernet for comma-list = rotation; malformed key RAISES at build = fail loud):
   - `_LEDGER_KEY_ENV="SIERRA_MCP_LEDGER_KEY"`, `_ENC_PREFIX="enc:fernet:v1:"`.
   - `_ledger_cipher()` → Fernet|MultiFernet|None (None when unset). `_reset_ledger_cipher()` for tests.
   - `_encrypt_snapshot(text)` → `_ENC_PREFIX + Fernet.encrypt` when key set, else passthrough (plaintext default = current behavior); None→None.
   - `_decrypt_snapshot(stored)` → inverse; a value WITHOUT `_ENC_PREFIX` is a legacy plaintext row, returned as-is (backward-compat, no migration). Encrypted row + no key → RuntimeError.
3. The ONLY write change — `ledger_record` (audit.py ~423): `_as_json(payload_snapshot)` → `_encrypt_snapshot(_as_json(payload_snapshot))`. `make_snapshot_sink` inherits it.
4. NEW read path `ledger_snapshot(conn, ledger_id)` — SELECT + `_decrypt_snapshot` + json.loads; the single column reader the future `restore_from_snapshot` + the fidelity tests go through.
5. Startup validation: call `_ledger_cipher()` once at the end of `connect()` (bad key fails at boot, loud). When `AUTHKIT_DOMAIN` set AND key empty → log a WARNING (recovery snapshots in plaintext) — WARN, don't fail-closed (plaintext is the accepted MEDIUM; bricking deletes would be worse).
6. Operator: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` → box `.env` `SIERRA_MCP_LEDGER_KEY=<that>`. Rotation: `"<new>,<old>"`.

## Does this fully close #14? NO — two independent surfaces:
1. Ledger `payload_snapshot` (full un-redacted) — the BIG surface; THIS closes it (+ New-5).
2. Immutable `audit_log` args/before/after — ALREADY redacted; residual = a short non-token secret under an arbitrary non-secret key (e.g. `{"vault_pin":4821}`) persists verbatim. Re-audit #5 accepted this MEDIUM.
**Recommendation: keep `audit_log` redact-only + accepted; do NOT blanket-encrypt it** (it must stay browsable forensics). If wanted later, a SEPARATE opt-in `SIERRA_MCP_AUDIT_ENCRYPT=1` behind the same seam — keep the two decisions independent.

## Tests (`tests/sierra_mcp/test_ledger_encryption.py`)
- encrypt→store→decrypt round-trip + assert ciphertext-on-disk (`enc:fernet:v1:`, secret absent) + full-fidelity recovery via `ledger_snapshot`.
- legacy plaintext row still reads (key active).
- no-key writes plaintext (default preserved) + reads back.
- wrong-key → `InvalidToken`.
- (optional) MultiFernet rotation decrypts an old-key row.
Existing `test_ledger_snapshot_fidelity.py` stays as-is (no-key posture). Change is INERT until the key is set → 341 existing tests stay green.

## Bottom line
Encrypt ONLY `payload_snapshot` at the `ledger_record` chokepoint via a Fernet seam keyed by `SIERRA_MCP_LEDGER_KEY` (Option 1). Closes New-5 + the dominant half of #14 under the standard at-rest threat model. Leaves the `audit_log` short-secret residual as a distinct accepted MEDIUM (or separate opt-in). USER decides key location + (Option 1) generates one Fernet key.
