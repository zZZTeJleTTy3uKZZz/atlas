"""Конфигурация Atlas CLI на clikit.AppConfig (F3a).

Наследует слоистый конфиг clikit (global/project/local + env ``ATLAS_*`` +
init-kwargs). Добавляет поля внешнего backend-сервиса. Секрет ``api_key`` берётся из env
``ATLAS_API_KEY`` или интерполяции ``${ATLAS_API_KEY}`` в TOML — в git не лежит.
"""
from __future__ import annotations

import os
from functools import lru_cache

from clikit import AppConfig


class AtlasConfig(AppConfig):
    """Конфиг Atlas CLI: адрес внешнего backend-сервиса, ключ, id Atlas-портала.

    - ``base_url``  — адрес внешнего backend-сервиса;
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
    # см. atlas._time). Формат: "+03:00" / "-05:30" / "+5" / "UTC". Дефолт —
    # MSK (UTC+3). Переопределяется слоями TOML или env ATLAS_TIMEZONE.
    timezone: str = "+03:00"

    # ── Идентичность владельца стора (раньше хардкод 'dmitry' по коду) ──
    # member-slug владельца этого Atlas-стора: дефолтный actor аудита и
    # владелец новых проектов. Пусто → команды требуют явный --owner/--actor.
    owner: str = ""

    # ── GitLab/Git namespacing (раньше хардкод приватные org/personal namespaces) ──
    # Бизнес/организационный top-level git-namespace (дефолт для проектов).
    org_namespace: str = ""
    # Личный top-level git-namespace (для проектов с owner-тегом personal_owner).
    personal_namespace: str = ""
    # Значение owner-тега, переключающее проект на personal_namespace.
    personal_owner: str = ""

    # Counterparty-владелец по умолчанию для командных (--team) проектов
    # (раньше хардкод орг-владельца). Generic-дефолт пуст.
    team_owner: str = ""

    # NB: знание о КОНКРЕТНЫХ внешних системах/порталах (Notion/Б24, таргеты
    # синка, notion_kind, маршрутизация фанаута) в CLI НЕ ЖИВЁТ — это зона
    # backend-сервиса. CLI знает только адрес backend (base_url) + ключ (api_key) и
    # шлёт/тянет события; куда их разложить по внешним системам — решает backend.


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


@lru_cache(maxsize=1)
def default_actor() -> str:
    """Дефолтный actor/owner member-slug стора из конфига (``AtlasConfig.owner``).

    Единая точка вместо хардкода имени по командам. Пусто, если владелец не
    задан в конфиге (тогда команды должны попросить явный ``--owner``/``--actor``).
    Кэшируется на процесс; в тестах сбрасывается ``default_actor.cache_clear()``.
    """
    try:
        return load_config().owner
    except Exception:  # pragma: no cover — конфиг недоступен/битый
        return ""


def owner_member_slug(portal_id: str | None = None) -> str:
    """Дефолтный владелец/руководитель нового проекта.

    Используется ``project add``: без ``--owner`` владельцем становится владелец
    стора (``AtlasConfig.owner``). ``portal_id`` оставлен для обратной
    совместимости сигнатуры, но владелец теперь конфиг-driven (не хардкод).
    """
    return default_actor()
