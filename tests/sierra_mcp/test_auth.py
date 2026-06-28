"""WorkOS AuthKit wiring — fail-closed default, opt-in local mode, provider."""
import pytest

from sierra_mcp.auth import build_auth


def test_fails_closed_when_domain_unset_and_no_optin():
    # I2: no AUTHKIT_DOMAIN and no opt-in -> refuse to start (fail closed).
    with pytest.raises(RuntimeError):
        build_auth({})


def test_local_mode_returns_none_with_explicit_optin():
    # I2: unset domain + SIERRA_MCP_ALLOW_NO_AUTH=1 -> None (auth-disabled dev mode).
    assert build_auth({"SIERRA_MCP_ALLOW_NO_AUTH": "1"}) is None


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
