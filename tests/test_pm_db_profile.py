"""F4e: resolve_db_url — путь БД зависит от активного профиля.

Профиль = отдельный Atlas-стор → отдельная atlas.db. Приоритет:
1. ATLAS_DB_URL (явный override) — всегда побеждает;
2. активный профиль (env ATLAS_PROFILE) → <config_dir профиля>/atlas.db,
   т.е. profiles/<p>/atlas.db через clikit config._config_dir('atlas');
3. без профиля → ~/.atlas/atlas.db (обратная совместимость, DEFAULT_DB_PATH).
"""
from __future__ import annotations

from pathlib import Path

from atlas.pm.db import DEFAULT_DB_PATH, resolve_db_url


def test_explicit_db_url_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_DB_URL", "sqlite:///custom.db")
    monkeypatch.setenv("ATLAS_PROFILE", "admin2")
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    assert resolve_db_url() == "sqlite:///custom.db"


def test_no_profile_uses_default_home_db(monkeypatch):
    monkeypatch.delenv("ATLAS_DB_URL", raising=False)
    monkeypatch.delenv("ATLAS_PROFILE", raising=False)
    assert resolve_db_url() == f"sqlite:///{DEFAULT_DB_PATH}"


def test_profile_routes_db_under_profile_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLAS_DB_URL", raising=False)
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PROFILE", "admin2")
    url = resolve_db_url()
    # БД лежит в profiles/admin2/atlas.db (clikit config_dir с профилем)
    expected = tmp_path / "profiles" / "admin2" / "atlas.db"
    assert url == f"sqlite:///{expected}"
    assert "profiles" in url and "admin2" in url


def test_two_profiles_get_distinct_db(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLAS_DB_URL", raising=False)
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_PROFILE", "admin")
    url_admin = resolve_db_url()
    monkeypatch.setenv("ATLAS_PROFILE", "lichka")
    url_lichka = resolve_db_url()
    assert url_admin != url_lichka
