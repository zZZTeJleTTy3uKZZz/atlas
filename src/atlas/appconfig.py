"""Конфигурация Atlas CLI на clikit.AppConfig (F3a).

Наследует слоистый конфиг clikit (global/project/local + env ``ATLAS_*`` +
init-kwargs). Добавляет поля backend-хаба. Секрет ``api_key`` берётся из env
``ATLAS_API_KEY`` или интерполяции ``${ATLAS_API_KEY}`` в TOML — в git не лежит.
"""
from __future__ import annotations

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


def load_config() -> AtlasConfig:
    """Загрузить конфиг бренда ``atlas`` (слои + env)."""
    return AtlasConfig.load("atlas")
