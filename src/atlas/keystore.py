"""#201: защищённое хранение api_key Atlas-стора через librarykit SecretStore.

Раньше admin-API-ключ лежал ОТКРЫТЫМ в ``config.toml`` (``AtlasConfig.api_key``).
Теперь он хранится в keyring ОС с прозрачным file-fallback (``tokens.toml``,
chmod 600) — через ``librarykit.secret_store.SecretStore``.

Ключ кладётся как ``access``-токен на пользователя ``portal_id`` (refresh пуст).
Namespace keyring — ``atlas`` + профиль из env ``ATLAS_PROFILE`` (то же, как
Atlas выбирает активный стор), поэтому ключ автоматически изолирован по профилю
и file-fallback ложится рядом с ``config.toml`` соответствующего профиля.

Pure-logic тонкая обёртка: вся реальная работа (keyring/файл/env-override) — в
SecretStore; здесь — лишь маппинг api_key↔access на portal_id.
"""
from __future__ import annotations

from librarykit.secret_store import SecretStore


def _store() -> SecretStore:
    """SecretStore бренда ``atlas`` (профиль сам подхватится из ATLAS_PROFILE)."""
    return SecretStore("atlas")


def save_api_key(portal_id: str, api_key: str) -> None:
    """Сохранить api_key стора (как access-токен; refresh пуст)."""
    _store().save_tokens(portal_id, api_key, "")


def load_api_key(portal_id: str) -> str | None:
    """Прочитать api_key стора. Пустой/отсутствующий → ``None``."""
    access, _ = _store().load_tokens(portal_id)
    return access or None


def clear_api_key(portal_id: str) -> None:
    """Удалить api_key стора из keyring и file-fallback (best-effort)."""
    _store().clear_tokens(portal_id)


__all__ = ["save_api_key", "load_api_key", "clear_api_key"]
