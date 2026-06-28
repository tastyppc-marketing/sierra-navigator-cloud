"""Sierra credential loader for the cloud build — env-first, self-contained.

NO hardcoded credentials and NO dependency on the local `scrapers/` package
(the local dev tree's config.py delegates to scrapers; the cloud build is
standalone so the Docker image stays tiny and browserless).

Resolution order (first hit wins):
  1. Environment variables SIERRA_SITE / SIERRA_USERNAME / SIERRA_PASSWORD
     (the canonical path on the VPS/container — injected as container env).
  2. A .env file (default ./.env or $SIERRA_NAVIGATOR_ENV), user format:
        Site = www.yoursite.com
        Username = your-username
        Password = your-password
     (kept only as a local-dev convenience; production uses env vars.)
Raises RuntimeError with setup guidance if neither yields all three.

Phase 3 replaces this with the multi-tenant encrypted vault, keeping a
get_credentials(tenant_id) shape.
"""
from __future__ import annotations
import os
from pathlib import Path

_KEYMAP = {"site": "site", "username": "username", "password": "password"}


def _parse_env_file(path: Path) -> dict:
    out: dict = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")               # split on FIRST '='
        k = key.strip().lower()
        if k in _KEYMAP:
            out[_KEYMAP[k]] = val.strip()
    return out


def get_credentials(env_path: Path | None = None, _environ: dict | None = None) -> dict:
    """Return {'site','username','password'} from env vars, then a .env file."""
    environ = os.environ if _environ is None else _environ
    # 1) env vars (the production path)
    ev = {"site": environ.get("SIERRA_SITE"), "username": environ.get("SIERRA_USERNAME"),
          "password": environ.get("SIERRA_PASSWORD")}
    if all(ev.values()):
        return ev
    # 2) .env file (local-dev convenience)
    ep = env_path or Path(environ.get("SIERRA_NAVIGATOR_ENV") or ".env")
    filevals = _parse_env_file(ep)
    if all(filevals.get(k) for k in ("site", "username", "password")):
        return {k: filevals[k] for k in ("site", "username", "password")}
    raise RuntimeError(
        "No Sierra credentials found. Set SIERRA_SITE / SIERRA_USERNAME / "
        "SIERRA_PASSWORD in the environment (production), or create a .env file:\n"
        "    Site = www.yoursite.com\n    Username = your-username\n    Password = your-password\n"
        "See env.example.")
