"""Конфигурация Atlas CLI на clikit.AppConfig (F3a).

Наследует слоистый конфиг clikit (global/project/local + env ``ATLAS_*`` +
init-kwargs). Добавляет поля backend-хаба. Секрет ``api_key`` берётся из env
``ATLAS_API_KEY`` или интерполяции ``${ATLAS_API_KEY}`` в TOML — в git не лежит.
"""
from __future__ import annotations

import os

from clikit import AppConfig


class AtlasConfig(AppConfig):
    """Конфиг Atlas CLI: адрес backend-хаба, ключ, id Atlas-портала.

    - ``base_url``  — адрес backend-хаба (notion-api-b24);
    - ``api_key``   — admin-API-ключ (X-API-Key); пустой → команды синка
      потребуют его явно;
    - ``portal_id`` — id Atlas-портала на бэке (seed ``atlas-local``).
    """

    base_url: str = "http://localhost:8000"
    api_key: str = ""
    portal_id: str = "atlas-local"
    # видимость входящего синка (профиль): all — все задачи (admin);
    # personal — только задачи, где я в составе участников ("мои задачи").
    scope: str = "all"
    # Часовой пояс PM-БД как фиксированный offset (без DST — канон naive-времени,
    # см. atlas.pm._time). Формат: "+03:00" / "-05:30" / "+5" / "UTC". Дефолт —
    # MSK (UTC+3). Переопределяется слоями TOML или env ATLAS_TIMEZONE.
    timezone: str = "+03:00"


def load_config() -> AtlasConfig:
    """Загрузить конфиг бренда ``atlas`` (слои + env)."""
    return AtlasConfig.load("atlas")


def resolve_api_key(cfg: AtlasConfig) -> str:
    """Достать admin-api-ключ стора с приоритетом источников + back-compat (#201).

    Приоритет:
    1. env ``ATLAS_API_KEY`` (если задан и непустой) — явный оверрайд;
    2. keystore (``SecretStore`` keyring/file-fallback) по ``cfg.portal_id``;
    3. legacy: открытый ``cfg.api_key`` из TOML — если непустой, ОДНОРАЗОВО
       мигрируется: ключ кладётся в keystore, а ``config.toml`` переписывается с
       пустым ``api_key`` (прочие поля сохраняются). Возвращается мигрированный
       ключ.

    Иначе — пустая строка (ключа нет; команды синка попросят его задать).

    ``keystore`` импортируется лениво (избегаем цикла appconfig↔keystore).
    """
    env_key = os.environ.get("ATLAS_API_KEY")
    if env_key:
        return env_key

    from atlas import keystore

    stored = keystore.load_api_key(cfg.portal_id)
    if stored:
        return stored

    legacy = cfg.api_key
    if legacy:
        # Миграция: ключ → keystore, открытое поле в config.toml обнуляем.
        keystore.save_api_key(cfg.portal_id, legacy)
        AtlasConfig(
            base_url=cfg.base_url,
            api_key="",
            portal_id=cfg.portal_id,
            scope=cfg.scope,
            timezone=cfg.timezone,
        ).save("atlas")
        return legacy

    return ""


# Соответствие портала-стора → дефолтный member-slug владельца нового проекта.
# Единая точка (без хардкода имён в командах): atlas-dmitry/atlas-admin — Дмитрий.
_PORTAL_OWNER = {"atlas-dmitry": "dmitry", "atlas-admin": "dmitry"}


def owner_member_slug(portal_id: str) -> str:
    """Дефолтный владелец/руководитель нового проекта, исходя из портала-стора.

    Используется ``project add``: без ``--owner`` владельцем становится тот, чей
    это стор. Неизвестный портал → ``dmitry`` (единственный человек-владелец MVP).
    """
    return _PORTAL_OWNER.get(portal_id, "dmitry")
