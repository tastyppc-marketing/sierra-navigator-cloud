"""WorkOS AuthKit wiring for the MCP server.

The server runs as an OAuth **resource server**: it validates WorkOS-issued
JWTs (JWKS only — no ``WORKOS_API_KEY`` at runtime) and serves the
``/.well-known/oauth-protected-resource`` metadata that MCP clients use to
discover the authorization server.

Env (see README):
  * ``AUTHKIT_DOMAIN``           WorkOS AuthKit domain, e.g. ``https://<env>.authkit.app``.
  * ``MCP_PUBLIC_BASE_URL``      This server's public URL, e.g. ``https://sierra.tastyautomations.com``.
  * ``SIERRA_MCP_ALLOW_NO_AUTH`` Explicit opt-in to run UNAUTHENTICATED for local dev.

The server **fails closed**: without ``AUTHKIT_DOMAIN`` it refuses to start unless
``SIERRA_MCP_ALLOW_NO_AUTH=1`` is set (local dev only).
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# Accepted truthy values for the auth-disabled opt-in.
_TRUTHY = {"1", "true", "yes", "on"}


def build_auth(env: dict | None = None) -> Any | None:
    """Return a configured ``AuthKitProvider`` or ``None`` (auth-disabled mode).

    Fail-closed semantics:

    * No ``AUTHKIT_DOMAIN`` and no ``SIERRA_MCP_ALLOW_NO_AUTH`` opt-in
      -> ``RuntimeError`` (refuse to start unauthenticated).
    * No ``AUTHKIT_DOMAIN`` **with** ``SIERRA_MCP_ALLOW_NO_AUTH=1``
      -> ``None`` + a loud warning (local dev only).
    * ``AUTHKIT_DOMAIN`` set but ``MCP_PUBLIC_BASE_URL`` missing -> ``RuntimeError``
      (the resource server can't advertise its metadata without a public URL).
    * Both set -> ``AuthKitProvider(authkit_domain=..., base_url=...)``.

    Import/construction failures are re-raised as ``RuntimeError`` with context.
    """
    environ = os.environ if env is None else env
    authkit_domain = (environ.get("AUTHKIT_DOMAIN") or "").strip()
    base_url = (environ.get("MCP_PUBLIC_BASE_URL") or "").strip()
    allow_no_auth = (
        (environ.get("SIERRA_MCP_ALLOW_NO_AUTH") or "").strip().lower() in _TRUTHY
    )

    if not authkit_domain:
        if not allow_no_auth:
            raise RuntimeError(
                "Refusing to start without WorkOS auth: AUTHKIT_DOMAIN is unset. "
                "Set AUTHKIT_DOMAIN (production), or set SIERRA_MCP_ALLOW_NO_AUTH=1 "
                "to explicitly run unauthenticated for local dev."
            )
        log.warning(
            "AUTHKIT_DOMAIN is unset and SIERRA_MCP_ALLOW_NO_AUTH is set -- starting "
            "in AUTH-DISABLED local mode (no OAuth; DEV ONLY). Never do this in prod."
        )
        return None

    if not base_url:
        raise RuntimeError(
            "AUTHKIT_DOMAIN is set but MCP_PUBLIC_BASE_URL is not. The MCP resource "
            "server needs its own public base URL to advertise OAuth protected-"
            "resource metadata. Set MCP_PUBLIC_BASE_URL "
            "(e.g. https://sierra.tastyautomations.com)."
        )

    try:
        from fastmcp.server.auth.providers.workos import AuthKitProvider
    except Exception as e:  # pragma: no cover - import-env dependent
        raise RuntimeError(
            f"Cannot import AuthKitProvider from fastmcp "
            f"(is fastmcp installed?): {e!r}"
        ) from e

    try:
        provider = AuthKitProvider(authkit_domain=authkit_domain, base_url=base_url)
    except Exception as e:
        raise RuntimeError(
            f"Failed to construct AuthKitProvider(authkit_domain={authkit_domain!r}, "
            f"base_url={base_url!r}): {e!r}"
        ) from e

    log.info(
        "WorkOS AuthKit enabled (authkit_domain=%s, base_url=%s)",
        authkit_domain,
        base_url,
    )
    return provider
