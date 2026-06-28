# Sierra Navigator Cloud

Hosted, OAuth-secured **MCP server** that drives the Sierra Interactive real-estate
admin backend over pure HTTP (no browser in the hot path). Wraps the browserless
`sierra_core` client and exposes it to MCP clients (Claude Desktop, ChatGPT, Claude
Code, …) behind WorkOS OAuth, with **reads + guarded writes + identity-locked delete**.

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

| Var | Purpose |
|-----|---------|
| `SIERRA_SITE` / `SIERRA_USERNAME` / `SIERRA_PASSWORD` | Sierra admin login (operator tenant) |
| `AUTHKIT_DOMAIN` | WorkOS AuthKit domain, e.g. `https://<env>.authkit.app` (enables OAuth) |
| `MCP_PUBLIC_BASE_URL` | This server's public URL, e.g. `https://sierra.tastyautomations.com` |
| `WORKOS_CLIENT_ID` | WorkOS public client id |

If `AUTHKIT_DOMAIN` is unset the server runs in **auth-disabled local mode** (for dev only).

## Run

```bash
pip install -r requirements-dev.txt
pytest                                   # 49 sierra_core tests + sierra_mcp tests
uvicorn sierra_mcp.server:app --host 127.0.0.1 --port 8080   # serves MCP at /mcp/
```

## Deploy

Docker image behind the VPS Caddy at `sierra.tastyautomations.com` → `127.0.0.1:8080`.
See `docker-compose.yml` and `.github/workflows/deploy.yml`.
