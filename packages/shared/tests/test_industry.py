"""Industry registry — config-driven catalogue built from packs/*/pack.json."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_os_shared import industry as I

# Resolve the repo's packs dir regardless of where pytest is invoked from.
_PACKS = Path(__file__).resolve().parents[3] / "packs"


@pytest.fixture(autouse=True)
def _use_repo_packs(monkeypatch):
    monkeypatch.setenv("AIOS_PACKS_DIR", str(_PACKS))
    I.reload()
    yield
    I.reload()


def test_industries_resolve_from_packs():
    keys = I.industry_keys()
    # All four login_source industries must resolve from their pack.json.
    assert {"construction", "accounting", "legal", "litigation"} <= keys


def test_generic_demo_pack_is_not_an_industry():
    # The demo pack (industry == "generic") must never appear as a selectable industry.
    assert "generic" not in I.industry_keys()
    assert "demo" not in I.industry_keys()


def test_get_industry_returns_workspace_config():
    con = I.get_industry("construction")
    assert con is not None
    assert con.name == "Construction AI OS"
    nav_keys = [n.key for n in con.workspace.nav]
    assert "rfis" in nav_keys and "chat" in nav_keys
    assert con.workspace.theme.get("primary")
    assert "rfi" in con.workflow_packs or "construction" in con.workflow_packs


def test_unknown_and_empty_industry_is_none():
    assert I.get_industry("aerospace") is None
    assert I.get_industry(None) is None
    assert I.get_industry("") is None


def test_summary_shape_for_public_list():
    summary = I.get_industry("accounting").summary()
    assert set(summary) == {"key", "name", "tagline", "theme"}
    assert summary["key"] == "accounting"


def test_industries_sorted_and_stable():
    keys = [i.key for i in I.list_industries()]
    assert keys == sorted(keys)


def test_packs_dir_override_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIOS_PACKS_DIR", str(tmp_path))  # empty dir
    I.reload()
    assert I.list_industries() == []
    assert I.industry_keys() == set()
