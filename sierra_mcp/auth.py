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

# Hosts where running unauthenticated is tolerated (local dev). Anything else —
# including an unset bind host — is treated as network-reachable (fail-closed).
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# Default uvicorn bind when SIERRA_MCP_BIND_HOST is unset: all interfaces (network-
# reachable), so the no-auth gate fails closed unless the operator explicitly pins a
# loopback bind. This is the value the server entrypoint ACTUALLY binds.
DEFAULT_BIND_HOST = "0.0.0.0"


def resolved_bind_host(env: dict | None = None) -> str:
    """The host uvicorn will actually bind — the SINGLE source of truth shared by the
    server entrypoint (:func:`sierra_mcp.server.main`) and :func:`build_auth`'s loopback
    gate. Tying both to this one value closes the #4/#13 divergence where the gate keyed
    off an advisory env var while the container hardcoded ``--host 0.0.0.0``.
    """
    environ = os.environ if env is None else env
    return (environ.get("SIERRA_MCP_BIND_HOST") or DEFAULT_BIND_HOST).strip()


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
    bind_host = resolved_bind_host(environ).lower()
    is_loopback = bind_host in _LOOPBACK

    if not authkit_domain:
        if not allow_no_auth:
            raise RuntimeError(
                "Refusing to start without WorkOS auth: AUTHKIT_DOMAIN is unset. "
                "Set AUTHKIT_DOMAIN (production), or set SIERRA_MCP_ALLOW_NO_AUTH=1 "
                "to explicitly run unauthenticated for local dev."
            )
        if not is_loopback:
            # The opt-in is honored ONLY when binding a loopback interface. An unset
            # or network-reachable bind host must never serve the delete-capable
            # tools unauthenticated (#13).
            raise RuntimeError(
                "Refusing to run unauthenticated on a non-loopback bind "
                f"(SIERRA_MCP_BIND_HOST={bind_host or '<unset>'}). "
                "SIERRA_MCP_ALLOW_NO_AUTH is honored only when binding 127.0.0.1/"
                "localhost/::1 (local dev). For any network-reachable bind set "
                "AUTHKIT_DOMAIN and run auth-enforced."
            )
        log.warning(
            "AUTHKIT_DOMAIN is unset and SIERRA_MCP_ALLOW_NO_AUTH is set with a "
            "loopback bind -- AUTH-DISABLED local mode (no OAuth; DEV ONLY)."
        )
        return None

    if not base_url:
        raise RuntimeError(
            "AUTHKIT_DOMAIN is set but MCP_PUBLIC_BASE_URL is not. The MCP resource "
            "server needs its own public base URL to advertise OAuth protected-"
            "resource metadata. Set MCP_PUBLIC_BASE_URL "
            "(e.g. https://sierra.tastyautomations.com)."
        )

    # #5: with auth enabled, an EMPTY subject allowlist is fail-OPEN — context.py grants
    # any valid WorkOS token the full read+write+DELETE set. For a system with irreversible
    # production deletes that is unacceptable, so refuse to boot until the operator names
    # the allowed subjects explicitly. (Parsed exactly like context._subject_allowlist so a
    # comma/whitespace-only value counts as empty.)
    allowlist = {
        s.strip()
        for s in (environ.get("SIERRA_MCP_SUBJECT_ALLOWLIST") or "").split(",")
        if s.strip()
    }
    if not allowlist:
        raise RuntimeError(
            "AUTHKIT_DOMAIN is set but SIERRA_MCP_SUBJECT_ALLOWLIST is empty. With auth "
            "enabled an empty allowlist grants EVERY valid WorkOS token full read+write+"
            "DELETE access (fail-open). Set SIERRA_MCP_SUBJECT_ALLOWLIST to a comma-"
            "separated list of allowed subject emails/sub-ids (fail-closed)."
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
