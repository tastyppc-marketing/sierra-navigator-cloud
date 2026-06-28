# Connecting chat clients to the Sierra MCP

The hosted server speaks **MCP over Streamable HTTP** at:

```
https://sierra.tastyautomations.com/mcp/
```

Auth is **WorkOS OAuth** (OAuth 2.1 + Dynamic Client Registration + PKCE). Each client
discovers the auth server from the protected-resource metadata the server publishes at
`/.well-known/oauth-protected-resource`, registers itself (DCR), and runs the browser
sign-in — you approve once per client.

## Prerequisites (one-time, operator)
1. Server deployed and reachable over HTTPS (see `DEPLOY.md`).
2. In WorkOS (Staging → later Production): **enable Dynamic Client Registration** and copy the
   **AuthKit domain** into the server's `AUTHKIT_DOMAIN` env, then recreate the container so it
   runs auth-enforced (drop `SIERRA_MCP_ALLOW_NO_AUTH`).
3. Sanity: `curl https://sierra.tastyautomations.com/.well-known/oauth-protected-resource`
   returns JSON pointing at your AuthKit domain; `curl …/mcp/` without a token returns **401**.

## Per-client setup (each is the same OAuth dance)
- **Claude Desktop / Claude web:** Settings → **Connectors** → *Add custom connector* → paste the
  `/mcp/` URL → it opens the WorkOS sign-in → **approve**. The Sierra tools then appear.
- **ChatGPT:** Settings → **Connectors / MCP** → add a custom MCP server → the `/mcp/` URL →
  approve the WorkOS sign-in.
- **Codex / CoWork:** add the remote MCP server (the `/mcp/` URL) to the client's MCP config →
  approve the WorkOS sign-in.

Because tools are discovered live (`tools/list` + `notifications/tools/list_changed`), once
connected, **new capabilities appear automatically — no reconnect, no retraining.**

## First-connect de-risk (do once)
Right after the first successful connect, decode one access token (jwt.io or `base64 -d` the
middle segment) and confirm `iss` = your AuthKit domain and `aud` = `https://sierra.tastyautomations.com`
(matching the server's `MCP_PUBLIC_BASE_URL`). WorkOS has a separate legacy session-token shape
(`iss=https://api.workos.com/`, no `aud`); the MCP path should use the AuthKit-domain shape. If the
audience doesn't match, fix `MCP_PUBLIC_BASE_URL` / the registered resource indicator so the three
agree byte-for-byte.

## Troubleshooting
- **401 on every call** → token `aud`/`iss` mismatch (see above), or `AUTHKIT_DOMAIN` unset.
- **Client can't register** → Dynamic Client Registration not enabled in WorkOS.
- **Tools missing after connect** → check the server logs; confirm `tools/list` returns the 19 tools.

> The existing Claude Code **plugin** (`tastyppc-marketing/sierra-navigator`) is repointed at this
> hosted server in Phase 4 (it currently runs the local scrapers); see
> `specs/architecture/sierra-cloud-phase4-design.md`.
