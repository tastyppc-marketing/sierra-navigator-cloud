# Deploy runbook — Sierra Navigator Cloud → VPS

Target: Hostinger KVM4 `187.124.83.246`, access `ssh myvps` (key-based, root). Caddy 2.6.2
**native systemd**, file config `/etc/caddy/Caddyfile`; Docker 29.1.3 + `docker compose` v2.37.1
(no buildx; GHCR auth not yet configured → **first deploy builds on the box**). Container binds
**`127.0.0.1:8080`** (free); Caddy reverse-proxies `sierra.tastyautomations.com` → it (DNS already live).

## Credential-safety boundary (read first)
- The container needs `SIERRA_SITE` / `SIERRA_USERNAME` / `SIERRA_PASSWORD`. **The operator populates
  `SIERRA_PASSWORD`** on the box — the build process does NOT transfer the Sierra password. The deploy
  brings the container up; full Sierra function begins once the operator adds the password.
- `WORKOS_API_KEY` is **not needed at runtime** (JWKS validation only).

## 1. Stage the code on the box
```bash
ssh myvps 'mkdir -p /root/sierra-mcp'
# from the local repo root (rsync excludes via .gitignore-ish):
rsync -az --delete --exclude '.git' --exclude '__pycache__' --exclude '*.db' --exclude '.env' \
  ./ myvps:/root/sierra-mcp/
```
(or `git clone` if the box gets repo auth later.)

## 2. Create the env file on the box (mode 600) — operator fills the password
```bash
ssh myvps 'umask 077; cat > /root/sierra-mcp/.env' <<'EOF'
SIERRA_SITE=gardnergrouprealtors.com
SIERRA_USERNAME=<operator-fills>
SIERRA_PASSWORD=<operator-fills>
MCP_PUBLIC_BASE_URL=https://sierra.tastyautomations.com
# WorkOS (fill once AuthKit domain captured + DCR enabled):
AUTHKIT_DOMAIN=
WORKOS_CLIENT_ID=client_01KW5KZANVNA94SYQ209S75740
# Until AUTHKIT_DOMAIN is set, opt into local no-auth so the container can boot:
SIERRA_MCP_ALLOW_NO_AUTH=1
EOF
ssh myvps 'chmod 600 /root/sierra-mcp/.env'
```

## 3. Build + start the container (localhost only)
```bash
ssh myvps 'cd /root/sierra-mcp && docker compose up -d --build'
ssh myvps 'docker ps --filter name=sierra-mcp; curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/health'
# expect: 200
```

## 4. Caddy site block — ADDITIVE, with backup/validate/verify/rollback
```bash
# 4a. BACK UP first (box convention: Caddyfile.bak-<note>)
ssh myvps 'cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-sierra'

# 4b. Append the block (mirrors siblings exactly)
ssh myvps 'cat >> /etc/caddy/Caddyfile' <<'EOF'

sierra.tastyautomations.com {
    reverse_proxy 127.0.0.1:8080
}
EOF

# 4c. VALIDATE before reload — abort if this fails
ssh myvps 'caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'

# 4d. Reload (graceful; does not drop :80/:443 or other sites)
ssh myvps 'systemctl reload caddy'

# 4e. VERIFY siblings still respond AND sierra now serves (expect 200/301/401/308, NOT connection errors)
ssh myvps 'for h in mcp gads analytics control sierra; do printf "%s " $h; curl -sS -o /dev/null -w "%{http_code}\n" https://$h.tastyautomations.com/ ; done'
ssh myvps 'systemctl is-active caddy; journalctl -u caddy -n 20 --no-pager | tail'

# 4f. SERVER smoke over HTTPS (through Caddy):
curl -sS -o /dev/null -w "health: %{http_code}\n" https://sierra.tastyautomations.com/health          # expect 200
curl -sS https://sierra.tastyautomations.com/.well-known/oauth-protected-resource | head -c 400; echo  # expect JSON (auth-enforced mode)
curl -sS -o /dev/null -w "mcp (no token): %{http_code}\n" https://sierra.tastyautomations.com/mcp/      # expect 401 once auth is enforced (or 200/406 in no-auth dev mode)
```

### ROLLBACK (run on ANY doubt — sibling not responding, validate fail, cert error)
```bash
ssh myvps 'cp -a /etc/caddy/Caddyfile.bak-sierra /etc/caddy/Caddyfile && systemctl reload caddy'
```
If sibling health can't be confirmed after reload, roll back and leave the final reload for the
operator (note it in the handoff). Never disturb openclaw-gateway / XRDP / the other sites.

## 5. Operator finishing steps (documented, not automated)
1. Fill `SIERRA_USERNAME` + `SIERRA_PASSWORD` in `/root/sierra-mcp/.env`, then
   `ssh myvps 'cd /root/sierra-mcp && docker compose up -d'` (recreate with creds).
2. WorkOS: enable **Dynamic Client Registration**; copy the **AuthKit domain** into `AUTHKIT_DOMAIN`,
   remove `SIERRA_MCP_ALLOW_NO_AUTH`, recreate the container (now auth-enforced).
3. Add the MCP connector in Claude Desktop / ChatGPT → `https://sierra.tastyautomations.com/mcp/`
   and approve the WorkOS sign-in.

## Phase 2c (later)
CI builds → GHCR → `ssh myvps 'cd /root/sierra-mcp && docker compose pull && up -d'`. At that point
drop `build: .` from compose and add `docker login ghcr.io` on the box (deploy token).
