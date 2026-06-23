"""#201: keystore — хранение api_key через librarykit SecretStore.

Хранит admin-api-ключ стора как access-токен SecretStore (keyring ОС с
прозрачным file-fallback). Тесты гоняем через file-fallback: подменяем
``SecretStore._keyring`` на ``None`` (keyring недоступен) → запись идёт в
``tokens.toml`` в profile-aware config_dir, заданный ``ATLAS_CONFIG_DIR``.
Изоляция по portal_id (user) проверяется round-trip'ом разных ключей.
"""
from __future__ import annotations

import pytest

from atlas import keystore


@pytest.fixture
def file_fallback(monkeypatch, tmp_path):
    """Изолированный config-dir + keyring выключен → SecretStore пишет в файл."""
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ATLAS_PROFILE", raising=False)
    # env-override <BRAND>_ACCESS_TOKEN читается ПЕРВЫМ в SecretStore — гасим его,
    # чтобы load_api_key читал именно сохранённое значение, а не env.
    monkeypatch.delenv("ATLAS_ACCESS_TOKEN", raising=False)
    # keyring недоступен → file-fallback (tokens.toml рядом с config.toml).
    monkeypatch.setattr(
        "librarykit.secret_store.SecretStore._keyring", lambda self: None
    )
    return tmp_path


def test_save_then_load_roundtrip(file_fallback):
    keystore.save_api_key("atlas-admin", "secret-key-123")
    assert keystore.load_api_key("atlas-admin") == "secret-key-123"


def test_load_missing_returns_none(file_fallback):
    assert keystore.load_api_key("nope") is None


def test_clear_removes_key(file_fallback):
    keystore.save_api_key("atlas-admin", "k")
    keystore.clear_api_key("atlas-admin")
    assert keystore.load_api_key("atlas-admin") is None


def test_empty_access_loads_as_none(file_fallback):
    """Пустой access-токен трактуется как отсутствие ключа (None, не '')."""
    keystore.save_api_key("atlas-admin", "")
    assert keystore.load_api_key("atlas-admin") is None


class _FakeKeyring:
    """In-memory keyring-бэкенд для проверки изоляции по (namespace, user)."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


def test_isolation_by_portal_id(monkeypatch):
    """Через keyring ключи разных порталов (user) не путаются между собой."""
    monkeypatch.delenv("ATLAS_PROFILE", raising=False)
    monkeypatch.delenv("ATLAS_ACCESS_TOKEN", raising=False)
    fake = _FakeKeyring()
    monkeypatch.setattr(
        "librarykit.secret_store.SecretStore._keyring", lambda self: fake
    )
    keystore.save_api_key("atlas-admin", "admin-key")
    keystore.save_api_key("atlas-lichka", "lichka-key")
    assert keystore.load_api_key("atlas-admin") == "admin-key"
    assert keystore.load_api_key("atlas-lichka") == "lichka-key"
    keystore.clear_api_key("atlas-admin")
    assert keystore.load_api_key("atlas-admin") is None
    # Очистка одного не задела другой.
    assert keystore.load_api_key("atlas-lichka") == "lichka-key"
