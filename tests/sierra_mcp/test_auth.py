"""WorkOS AuthKit wiring — fail-closed default, opt-in local mode, provider."""
import pytest

from sierra_mcp.auth import build_auth


def test_fails_closed_when_domain_unset_and_no_optin():
    # I2: no AUTHKIT_DOMAIN and no opt-in -> refuse to start (fail closed).
    with pytest.raises(RuntimeError):
        build_auth({})


def test_local_mode_returns_none_with_optin_on_loopback():
    # unset domain + opt-in + loopback bind -> None (auth-disabled dev mode).
    assert build_auth({
        "SIERRA_MCP_ALLOW_NO_AUTH": "1",
        "SIERRA_MCP_BIND_HOST": "127.0.0.1",
    }) is None


def test_no_auth_refused_on_non_loopback_bind_even_with_optin():
    # #13: the opt-in must NOT enable no-auth on a network-reachable bind.
    with pytest.raises(RuntimeError):
        build_auth({"SIERRA_MCP_ALLOW_NO_AUTH": "1", "SIERRA_MCP_BIND_HOST": "0.0.0.0"})
    # An UNSET bind host is treated as non-loopback (fail-closed).
    with pytest.raises(RuntimeError):
        build_auth({"SIERRA_MCP_ALLOW_NO_AUTH": "1"})


def test_domain_without_base_url_raises():
    with pytest.raises(RuntimeError):
        build_auth({"AUTHKIT_DOMAIN": "https://x.authkit.app"})


def test_authkit_provider_constructed_when_both_set():
    try:
        provider = build_auth({
            "AUTHKIT_DOMAIN": "https://x.authkit.app",
            "MCP_PUBLIC_BASE_URL": "https://sierra.tastyautomations.com",
            "SIERRA_MCP_SUBJECT_ALLOWLIST": "operator@firm.com",  # required when auth on
        })
    except RuntimeError as e:
        pytest.skip(f"AuthKitProvider unavailable in this env: {e}")
    assert provider is not None
    assert type(provider).__name__ == "AuthKitProvider"


def test_auth_enabled_requires_nonempty_subject_allowlist():
    # re-audit #5: with AUTHKIT_DOMAIN set, an EMPTY allowlist would grant every valid
    # WorkOS token full read+write+DELETE (fail-open). Refuse to boot (fail-closed).
    with pytest.raises(RuntimeError, match="ALLOWLIST"):
        build_auth({
            "AUTHKIT_DOMAIN": "https://x.authkit.app",
            "MCP_PUBLIC_BASE_URL": "https://sierra.tastyautomations.com",
            # SIERRA_MCP_SUBJECT_ALLOWLIST intentionally unset
        })
    # whitespace-only allowlist is also empty -> still refused
    with pytest.raises(RuntimeError, match="ALLOWLIST"):
        build_auth({
            "AUTHKIT_DOMAIN": "https://x.authkit.app",
            "MCP_PUBLIC_BASE_URL": "https://sierra.tastyautomations.com",
            "SIERRA_MCP_SUBJECT_ALLOWLIST": "   ,  ",
        })


# ========================================================================== #
# W4-T2 (re-audit #4/#13 CRITICAL): the no-auth loopback gate must key off the
# host uvicorn ACTUALLY binds, not an advisory var that can diverge from the real
# socket. resolved_bind_host() is the single source of truth shared by the server
# entrypoint and build_auth.
# ========================================================================== #

def test_resolved_bind_host_default_is_network_reachable():
    from sierra_mcp.auth import resolved_bind_host
    # Unset -> 0.0.0.0 (all interfaces) — the real uvicorn default, NOT a loopback
    # the gate would wrongly trust.
    assert resolved_bind_host({}) == "0.0.0.0"
    assert resolved_bind_host({"SIERRA_MCP_BIND_HOST": "127.0.0.1"}) == "127.0.0.1"
    assert resolved_bind_host({"SIERRA_MCP_BIND_HOST": "  0.0.0.0  "}) == "0.0.0.0"


def test_server_entrypoint_binds_the_same_host_the_auth_gate_checks(monkeypatch):
    # The defining fix for #4/#13: server.main() must bind resolved_bind_host() — the
    # EXACT value build_auth()'s loopback gate evaluates — so a `BIND_HOST=127.0.0.1 +
    # ALLOW_NO_AUTH=1` config can't bind 0.0.0.0 behind the gate's back.
    import uvicorn
    from sierra_mcp import server
    from sierra_mcp.auth import resolved_bind_host

    monkeypatch.setenv("SIERRA_MCP_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("SIERRA_MCP_PORT", "8080")
    captured = {}
    monkeypatch.setattr(
        uvicorn, "run",
        lambda app, host=None, port=None, **kw: captured.update(host=host, port=port),
    )
    server.main()
    assert captured["host"] == "127.0.0.1"
    assert captured["host"] == resolved_bind_host()  # bind == auth-gate source of truth
    assert captured["port"] == 8080
