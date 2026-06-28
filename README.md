# Sierra Navigator Cloud

Hosted, OAuth-secured **MCP server** that drives the Sierra Interactive real-estate
admin backend over pure HTTP (no browser in the hot path). Wraps the browserless
`sierra_core` client and exposes it to MCP clients (Claude Desktop, ChatGPT, Claude
Code, …) behind WorkOS OAuth. The MCP layer is currently **read-only** (Tier-1);
guarded writes + identity-locked delete land in **Phase 2b** (the `sierra_core`
client already implements them behind `allow_write`, but no write/delete MCP tool
is exposed yet).

> Canonical home of `sierra_core` going forward. The local dev tree
> (`Scraper Creator`) keeps a copy with a headed-browser login fallback for
> interactive work; this cloud build is HTTP-login-only and ships in a tiny image.

## Layout

```
sierra_core/   # browserless Sierra HTTP client (errors, parsing, transport,
               # session broker, identity-lock, client) — 49 unit tests
sierra_mcp/    # the FastMCP server (tools, resources, auth, guardrails, audit)
data/          # the 642-endpoint catalogue (js_bundle_endpoints.json + API_ENDPOINTS.md)
tests/         # sierra_core/ (reused) + sierra_mcp/
```

## Configuration (env)

See `env.example` for a copy-paste template.

| Var | Purpose |
|-----|---------|
| `SIERRA_SITE` / `SIERRA_USERNAME` / `SIERRA_PASSWORD` | Sierra admin login (operator tenant) |
| `AUTHKIT_DOMAIN` | WorkOS AuthKit domain, e.g. `https://<env>.authkit.app` (enables OAuth) |
| `MCP_PUBLIC_BASE_URL` | This server's public URL, e.g. `https://sierra.tastyautomations.com` |
| `WORKOS_CLIENT_ID` | WorkOS public client id — reserved (not read at runtime yet) |
| `SIERRA_MCP_ALLOW_NO_AUTH` | Set to `1` to explicitly run unauthenticated when `AUTHKIT_DOMAIN` is unset (dev only) |

The server **fails closed**: if `AUTHKIT_DOMAIN` is unset it refuses to start unless
`SIERRA_MCP_ALLOW_NO_AUTH=1` is also set (**auth-disabled local mode**, dev only).

## Run

```bash
pip install -r requirements-dev.txt
pytest                                   # 49 sierra_core tests + sierra_mcp tests
uvicorn sierra_mcp.server:app --host 127.0.0.1 --port 8080   # serves MCP at /mcp/
```

## Deploy

Docker container behind the VPS Caddy at `sierra.tastyautomations.com` → `127.0.0.1:8080`.
Packaging is here — `Dockerfile`, `docker-compose.yml`, and the step-by-step `DEPLOY.md`
runbook (backup → validate → reload → verify → rollback). CI auto-deploy
(`.github/workflows/deploy.yml`, build → GHCR → ssh) lands in **Phase 2c**.
