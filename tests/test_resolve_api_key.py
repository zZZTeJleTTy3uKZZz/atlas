"""#201: resolve_api_key — приоритет источников ключа + back-compat миграция.

Приоритет: env ATLAS_API_KEY > keystore (SecretStore) > legacy открытый
cfg.api_key из TOML. Legacy-ветка МИГРИРУЕТ ключ в keystore и обнуляет
открытое поле в config.toml. Тесты гоняют keystore через file-fallback
(keyring выключен, ATLAS_CONFIG_DIR на tmp_path).
"""
from __future__ import annotations

import tomllib

import pytest

from atlas import keystore
from atlas.appconfig import AtlasConfig, resolve_api_key


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Изолированный config-dir + keyring выключен (file-fallback)."""
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ATLAS_PROFILE", raising=False)
    monkeypatch.delenv("ATLAS_API_KEY", raising=False)
    monkeypatch.delenv("ATLAS_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        "librarykit.secret_store.SecretStore._keyring", lambda self: None
    )
    return tmp_path


def test_env_wins(isolated, monkeypatch):
    monkeypatch.setenv("ATLAS_API_KEY", "from-env")
    keystore.save_api_key("atlas-local", "from-keystore")
    cfg = AtlasConfig(api_key="from-toml", portal_id="atlas-local")
    assert resolve_api_key(cfg) == "from-env"


def test_keystore_over_legacy(isolated):
    keystore.save_api_key("atlas-local", "from-keystore")
    cfg = AtlasConfig(api_key="from-toml", portal_id="atlas-local")
    assert resolve_api_key(cfg) == "from-keystore"


def test_empty_returns_empty(isolated):
    cfg = AtlasConfig(api_key="", portal_id="atlas-local")
    assert resolve_api_key(cfg) == ""


def test_legacy_migrates_into_keystore_and_blanks_toml(isolated):
    """Открытый api_key из TOML мигрирует в keystore и обнуляется в config.toml."""
    # Положим открытый config.toml на диск, как будто он там уже лежал.
    AtlasConfig(
        base_url="http://hub", api_key="legacy-secret",
        portal_id="atlas-local", scope="all", timezone="+03:00",
    ).save("atlas")
    cfg = AtlasConfig.load("atlas")
    assert cfg.api_key == "legacy-secret"  # стартовое состояние: открытый ключ

    # resolve должен вернуть ключ И мигрировать его.
    assert resolve_api_key(cfg) == "legacy-secret"

    # 1) ключ теперь в keystore
    assert keystore.load_api_key("atlas-local") == "legacy-secret"
    # 2) config.toml переписан с пустым api_key, остальные поля сохранены
    cfg_file = isolated / "config.toml"
    data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["api_key"] == ""
    assert data["base_url"] == "http://hub"
    assert data["portal_id"] == "atlas-local"
    assert data["scope"] == "all"
    assert data["timezone"] == "+03:00"
    # 3) повторный resolve теперь идёт через keystore-ветку, не через legacy
    cfg2 = AtlasConfig.load("atlas")
    assert cfg2.api_key == ""
    assert resolve_api_key(cfg2) == "legacy-secret"


def test_legacy_migration_preserves_personal_scope(isolated):
    AtlasConfig(
        base_url="http://hub", api_key="sec",
        portal_id="lichka", scope="personal", timezone="-05:00",
    ).save("atlas")
    cfg = AtlasConfig.load("atlas")
    assert resolve_api_key(cfg) == "sec"
    data = tomllib.loads((isolated / "config.toml").read_text(encoding="utf-8"))
    assert data["scope"] == "personal"
    assert data["timezone"] == "-05:00"
    assert data["api_key"] == ""
