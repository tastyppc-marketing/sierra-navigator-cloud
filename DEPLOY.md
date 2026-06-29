# Deploy runbook — Sierra Navigator Cloud → VPS

Target: Hostinger KVM4 `187.124.83.246`, access `ssh myvps` (key-based, root). Caddy 2.6.2
**native systemd**, file config `/etc/caddy/Caddyfile`; Docker 29.1.3 + `docker compose` v2.37.1
(no buildx; GHCR auth not yet configured → **first deploy builds on the box**). The container binds
**`127.0.0.1:8080`** (compose maps it host-loopback-only); Caddy reverse-proxies
`sierra.tastyautomations.com` → it (DNS already live).

## ⚠ Order matters — AUTH BEFORE PUBLIC EXPOSURE (#4)
The server exposes delete-capable tools, so it must NEVER be publicly reachable without auth. Two
guards make that automatic: (1) the container binds `0.0.0.0` inside, so it **refuses to boot**
unless `AUTHKIT_DOMAIN` is set — no-auth is honored only on a loopback bind (`sierra_mcp/auth.py`);
(2) this runbook adds the public Caddy route **only after** the auth-enforced container is confirmed
healthy and confirmed to return **401 without a token**. Do the steps in order; there is no
unauthenticated window.

## Credential-safety boundary (read first)
- The container needs `SIERRA_SITE` / `SIERRA_USERNAME` / `SIERRA_PASSWORD`. **The operator
  populates `SIERRA_PASSWORD`** on the box — the build process never transfers the Sierra password.
- `WORKOS_API_KEY` is **not** needed at runtime (JWKS validation only).

## 1. WorkOS prep — do FIRST (the container won't boot without it)
In the WorkOS dashboard (Staging → later Production): enable **Dynamic Client Registration**, and
copy your **AuthKit domain** (e.g. `https://<env>.authkit.app`) — it goes in the `.env` below.

## 2. Stage the code on the box
```bash
ssh myvps 'mkdir -p /root/sierra-mcp'
rsync -az --delete --exclude '.git' --exclude '__pycache__' --exclude '*.db' --exclude '.env' \
  ./ myvps:/root/sierra-mcp/
```

## 3. Create the env file on the box (mode 600) — AUTH-ENFORCED; operator fills the password
```bash
ssh myvps 'umask 077; cat > /root/sierra-mcp/.env' <<'EOF'
SIERRA_SITE=gardnergrouprealtors.com
SIERRA_USERNAME=<operator-fills>
SIERRA_PASSWORD=<operator-fills>
MCP_PUBLIC_BASE_URL=https://sierra.tastyautomations.com
# WorkOS auth — REQUIRED (the container refuses to boot without AUTHKIT_DOMAIN):
AUTHKIT_DOMAIN=https://<your-env>.authkit.app
WORKOS_CLIENT_ID=client_01KW5KZANVNA94SYQ209S75740
# Only YOUR WorkOS subject(s) may use the server (email or sub, comma-separated):
SIERRA_MCP_SUBJECT_ALLOWLIST=tastyppc@gmail.com
# Ledger encryption-at-rest (Phase B / #14) — set this or recovery snapshots are stored PLAINTEXT.
# Generate once:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SIERRA_MCP_LEDGER_KEY=<operator-generates>
EOF
ssh myvps 'chmod 600 /root/sierra-mcp/.env'
```
> Do NOT set `SIERRA_MCP_ALLOW_NO_AUTH` here. The entrypoint (`python -m sierra_mcp.server`) binds
> `SIERRA_MCP_BIND_HOST` (default `0.0.0.0`) — the **same** value the no-auth gate checks, so they can't
> diverge (#4/#13) — and the container leaves it at `0.0.0.0`, so no-auth is refused. No-auth is only for a
> local dev run with **both** `SIERRA_MCP_ALLOW_NO_AUTH=1` and `SIERRA_MCP_BIND_HOST=127.0.0.1` set.
> `SIERRA_MCP_SUBJECT_ALLOWLIST` is **required** with auth on — an empty allowlist refuses to boot (#5).

## 4. Build + start the container (auth-enforced, localhost-only) and PROVE auth is on
```bash
ssh myvps 'cd /root/sierra-mcp && docker compose up -d --build'
ssh myvps 'docker ps --filter name=sierra-mcp; docker logs --tail 20 sierra-mcp'
ssh myvps 'curl -sS -o /dev/null -w "health:        %{http_code}\n" http://127.0.0.1:8080/health'  # expect 200
ssh myvps 'curl -sS -o /dev/null -w "mcp (no token): %{http_code}\n" http://127.0.0.1:8080/mcp'     # expect 401
```
**If `/mcp` returns anything other than 401, STOP** — auth is not enforced; do not expose it. (If
`/health` itself 401s, the health route needs to be made auth-exempt — flag it before proceeding.)

## 5. Caddy site block — ADDITIVE, with backup / validate / verify / rollback
```bash
# 5a. BACK UP first (box convention: Caddyfile.bak-<note>)
ssh myvps 'cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-sierra'

# 5b. Append the block (mirrors siblings exactly)
ssh myvps 'cat >> /etc/caddy/Caddyfile' <<'EOF'

sierra.tastyautomations.com {
    reverse_proxy 127.0.0.1:8080
}
EOF

# 5c. VALIDATE before reload — abort if this fails
ssh myvps 'caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'

# 5d. Reload (graceful; does not drop :80/:443 or other sites)
ssh myvps 'systemctl reload caddy'

# 5e. VERIFY siblings still respond AND sierra now serves (expect 200/301/401/308, NOT connection errors)
ssh myvps 'for h in mcp gads analytics control sierra; do printf "%s " $h; curl -sS -o /dev/null -w "%{http_code}\n" https://$h.tastyautomations.com/ ; done'
ssh myvps 'systemctl is-active caddy; journalctl -u caddy -n 20 --no-pager | tail'

# 5f. SERVER smoke over HTTPS (through Caddy):
curl -sS -o /dev/null -w "health:        %{http_code}\n" https://sierra.tastyautomations.com/health        # expect 200
curl -sS https://sierra.tastyautomations.com/.well-known/oauth-protected-resource | head -c 400; echo      # expect JSON
curl -sS -o /dev/null -w "mcp (no token): %{http_code}\n" https://sierra.tastyautomations.com/mcp          # expect 401
```

### ROLLBACK (run on ANY doubt — sibling not responding, validate fail, cert error)
```bash
ssh myvps 'cp -a /etc/caddy/Caddyfile.bak-sierra /etc/caddy/Caddyfile && systemctl reload caddy'
```
If sibling health can't be confirmed after reload, roll back and leave the final reload for the
operator. Never disturb openclaw-gateway / XRDP / the other sites.

## 6. Connect the chat clients (see CONNECTORS.md)
Add the connector `https://sierra.tastyautomations.com/mcp` in Claude Desktop / ChatGPT and approve
the WorkOS sign-in. Only subjects in `SIERRA_MCP_SUBJECT_ALLOWLIST` are authorized.

## Phase 2c (later)
CI builds → GHCR → `ssh myvps 'cd /root/sierra-mcp && docker compose pull && up -d'`. At that point
drop `build: .` from compose and add `docker login ghcr.io` on the box (deploy token). Harden the
workflow (SHA-pinned actions + a GitHub Environment approval gate + a forced-command deploy key, #15)
before enabling auto-deploy.
