"""Loaders for the shipped endpoint catalogue under ``data/``.

The catalogue is the 642-endpoint map of Sierra's admin XHR surface
(``js_bundle_endpoints.json``) plus the human-written verified-endpoints
reference (``API_ENDPOINTS.md``). Paths resolve relative to the repo root via
``__file__`` so loading is robust to the current working directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# sierra_mcp/ lives at the repo root, so data/ is one level up from this file.
_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent
_DATA_DIR = _REPO_ROOT / "data"


def _resolve(path: str | Path | None, default_name: str) -> Path:
    """Resolve a data file: explicit ``path`` wins, else ``data/<default_name>``.

    Falls back to ``<cwd>/data/<default_name>`` if the ``__file__``-anchored
    location is missing (defensive against unusual deployment layouts).
    """
    if path is not None:
        return Path(path)
    primary = _DATA_DIR / default_name
    if primary.exists():
        return primary
    fallback = Path.cwd() / "data" / default_name
    return fallback if fallback.exists() else primary


def load_catalogue(path: str | Path | None = None) -> dict[str, Any]:
    """Parse ``data/js_bundle_endpoints.json`` (keys: ``bundles``, ``by_url``)."""
    p = _resolve(path, "js_bundle_endpoints.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def endpoint_paths(path: str | Path | None = None) -> list[str]:
    """Sorted list of the catalogue's ``by_url`` endpoint paths (642 of them)."""
    cat = load_catalogue(path)
    by_url = cat.get("by_url") if isinstance(cat, dict) else None
    return sorted(by_url.keys()) if isinstance(by_url, dict) else []


def verified_endpoints_markdown(path: str | Path | None = None) -> str:
    """Raw text of ``data/API_ENDPOINTS.md`` (the verified-endpoints reference)."""
    p = _resolve(path, "API_ENDPOINTS.md")
    return Path(p).read_text(encoding="utf-8")
