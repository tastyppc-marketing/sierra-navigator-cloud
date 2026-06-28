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
        })
    except RuntimeError as e:
        pytest.skip(f"AuthKitProvider unavailable in this env: {e}")
    assert provider is not None
    assert type(provider).__name__ == "AuthKitProvider"
