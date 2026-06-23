"""F4d: CLI `atlas profile register <slug>` — онбординг нового Atlas-стора.

Профиль = отдельный стор: команда дёргает POST /admin/profiles текущим admin-
конфигом, получает ключ нового стора, сохраняет ЛОКАЛЬНЫЙ профиль
(profiles/<slug>/config.toml с выданным api_key/portal_id/scope) и создаёт его БД
(схема). HTTP замокан (monkeypatch на BackendClient.register_profile).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atlas.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    """#201: keystore-операции в тестах идут в file-fallback, не в реальный
    Credential Manager (изоляция + детерминизм)."""
    monkeypatch.setattr(
        "librarykit.secret_store.SecretStore._keyring", lambda self: None
    )
    monkeypatch.delenv("ATLAS_ACCESS_TOKEN", raising=False)


@pytest.fixture
def admin_env(monkeypatch, tmp_path):
    """Изолированный config-dir + active admin base_url/api_key через env."""
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_BASE_URL", "http://hub")
    monkeypatch.setenv("ATLAS_API_KEY", "adminsecret")
    monkeypatch.delenv("ATLAS_DB_URL", raising=False)
    monkeypatch.delenv("ATLAS_PROFILE", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def fake_register(monkeypatch):
    """Замокать сетевой вызов register_profile. Сигнатура и ответ — строго по
    контракту ядра ProfileIn (member_slug/portal_slug раздельны; ответ несёт
    portal_slug). captured хранит переданные аргументы для проверки контракта."""
    captured: dict = {}

    async def _fake(self, member_slug, portal_slug, name, scope, global_role=None):
        captured.update(member_slug=member_slug, portal_slug=portal_slug,
                        name=name, scope=scope, global_role=global_role)
        return {"member_slug": member_slug, "portal_slug": portal_slug, "api_key": "k2"}

    monkeypatch.setattr(
        "atlas.sync.backend_client.BackendClient.register_profile", _fake
    )
    return captured


def test_register_writes_profile_config(admin_env):
    res = runner.invoke(
        app, ["profile", "register", "admin2", "--name", "Админ2", "--scope", "all"]
    )
    assert res.exit_code == 0, res.output
    cfg_file = admin_env / "profiles" / "admin2" / "config.toml"
    assert cfg_file.is_file(), "config профиля не создан"
    data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    # #201: ключ НЕ в открытом config.toml — поле пустое.
    assert data["api_key"] == ""
    assert data["portal_id"] == "admin2"
    assert data["scope"] == "all"
    assert data["base_url"] == "http://hub"


def test_register_stores_key_in_keystore_not_plaintext(admin_env, monkeypatch):
    """#201: выданный ключ доступен через keystore/resolve, но НЕ открыт в TOML."""
    from atlas import keystore
    from atlas.appconfig import AtlasConfig, resolve_api_key

    res = runner.invoke(
        app, ["profile", "register", "admin2", "--name", "Админ2", "--scope", "all"]
    )
    assert res.exit_code == 0, res.output

    # config.toml профиля — без открытого ключа.
    data = tomllib.loads(
        (admin_env / "profiles" / "admin2" / "config.toml").read_text(encoding="utf-8")
    )
    assert data["api_key"] == ""

    # Под профилем admin2 ключ читается из keystore и через resolve_api_key.
    monkeypatch.setenv("ATLAS_PROFILE", "admin2")
    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    assert keystore.load_api_key("admin2") == "k2"
    cfg = AtlasConfig.load("atlas")
    assert resolve_api_key(cfg) == "k2"


def test_register_sends_core_contract_fields(admin_env, fake_register):
    """В ядро уходят member_slug + portal_slug (контракт ProfileIn), а не {slug}.
    По умолчанию member_slug = portal_slug = аргумент."""
    res = runner.invoke(
        app, ["profile", "register", "atlas-admin", "--name", "Админ", "--scope", "all"]
    )
    assert res.exit_code == 0, res.output
    assert fake_register["member_slug"] == "atlas-admin"
    assert fake_register["portal_slug"] == "atlas-admin"


def test_register_member_separates_from_store(admin_env, fake_register):
    """--member отделяет человека от стора: один член — несколько сторов."""
    res = runner.invoke(
        app, ["profile", "register", "atlas-admin", "--member", "dmitry",
              "--name", "Дмитрий-админ", "--scope", "all"]
    )
    assert res.exit_code == 0, res.output
    assert fake_register["member_slug"] == "dmitry"      # человек
    assert fake_register["portal_slug"] == "atlas-admin"  # стор


def test_register_creates_profile_db_with_tables(admin_env):
    res = runner.invoke(
        app, ["profile", "register", "admin2", "--name", "Админ2", "--scope", "all"]
    )
    assert res.exit_code == 0, res.output
    db_file = admin_env / "profiles" / "admin2" / "atlas.db"
    assert db_file.is_file(), "atlas.db профиля не создан"
    # схема создана (create_all отработал) — таблица projects есть
    import sqlite3
    con = sqlite3.connect(db_file)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    con.close()
    assert "projects" in names
    assert "tasks" in names


def test_register_personal_scope(admin_env):
    res = runner.invoke(
        app, ["profile", "register", "lichka", "--name", "Личка", "--scope", "personal"]
    )
    assert res.exit_code == 0, res.output
    data = tomllib.loads(
        (admin_env / "profiles" / "lichka" / "config.toml").read_text(encoding="utf-8")
    )
    assert data["scope"] == "personal"


def test_register_idempotent(admin_env):
    r1 = runner.invoke(app, ["profile", "register", "admin2", "--name", "Админ2"])
    r2 = runner.invoke(app, ["profile", "register", "admin2", "--name", "Админ2"])
    assert r1.exit_code == 0 and r2.exit_code == 0, (r1.output, r2.output)
    # повторный вызов не падает; config на месте
    assert (admin_env / "profiles" / "admin2" / "config.toml").is_file()


def test_register_does_not_change_active_profile(admin_env, monkeypatch):
    """env ATLAS_PROFILE восстанавливается после save (не протекает)."""
    import os
    monkeypatch.setenv("ATLAS_PROFILE", "outer")
    runner.invoke(app, ["profile", "register", "admin2", "--name", "Админ2"])
    assert os.environ.get("ATLAS_PROFILE") == "outer"


def test_register_requires_admin_config(monkeypatch, tmp_path):
    """Без api_key — внятная ошибка, ненулевой exit."""
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    monkeypatch.delenv("ATLAS_BASE_URL", raising=False)
    res = runner.invoke(app, ["profile", "register", "admin2", "--name", "Админ2"])
    assert res.exit_code != 0


def test_profile_help_lists_register():
    res = runner.invoke(app, ["profile", "--help"])
    assert res.exit_code == 0
    assert "register" in res.stdout


def test_register_help_has_options():
    res = runner.invoke(app, ["profile", "register", "--help"])
    assert res.exit_code == 0
    assert "--name" in res.stdout
    assert "--scope" in res.stdout
    assert "--global-role" in res.stdout
