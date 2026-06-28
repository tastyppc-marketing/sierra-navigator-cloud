"""Catalogue loaders — read the shipped data/ files, robust to CWD."""
from sierra_mcp.catalogue import (
    load_catalogue,
    endpoint_paths,
    verified_endpoints_markdown,
)


def test_load_catalogue_has_by_url():
    cat = load_catalogue()
    assert isinstance(cat, dict)
    assert "by_url" in cat and isinstance(cat["by_url"], dict)
    assert "bundles" in cat


def test_endpoint_paths_count_is_642_and_sorted():
    paths = endpoint_paths()
    assert len(paths) == 642
    assert paths == sorted(paths)
    assert all(isinstance(p, str) for p in paths)


def test_verified_endpoints_markdown_nonempty():
    md = verified_endpoints_markdown()
    assert isinstance(md, str)
    assert len(md.strip()) > 0
    assert "Sierra" in md  # sanity: it's the real reference doc


def test_loaders_robust_to_cwd(tmp_path, monkeypatch):
    # Resolution is __file__-anchored, so changing CWD must not break loading.
    monkeypatch.chdir(tmp_path)
    assert len(endpoint_paths()) == 642
    assert verified_endpoints_markdown().strip()
