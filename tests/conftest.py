"""Глобальная конфигурация pytest для всего test suite.

Защищает реальный `~/Documents/PROJECT/` от утечек тестов:
- ATLAS_PROJECTS_ROOT всегда указывает на изолированный tmp_path,
  если тест явно не переопределяет это.

Если когда-нибудь default behavior `atlas projects add` поменяется
(например `--setup-layout=True` будет создавать junction'ы), эта fixture
гарантирует что junction'ы попадут в tmp, а не в реальный fs.
"""
from __future__ import annotations

import os

import pytest

# Generic тест-конфиг для бренда atlas (раньше owner/namespaces были хардкодом
# 'owner'/'example-org' в коде; теперь — config-driven). Задаём на уровне МОДУЛЯ
# (до импорта atlas-модулей при сборе тестов), т.к. часть модульных констант
# (DEFAULT_ACTOR_SLUG = default_actor()) резолвится на import. Значения generic —
# тесты не зависят от личного config.toml разработчика и валидны в публичном CI.
os.environ["ATLAS_OWNER"] = "owner"
os.environ["ATLAS_ORG_NAMESPACE"] = "example-org"
os.environ["ATLAS_PERSONAL_NAMESPACE"] = "example-personal"
os.environ["ATLAS_PERSONAL_OWNER"] = "owner"
os.environ["ATLAS_TEAM_OWNER"] = "example-org"


@pytest.fixture(autouse=True)
def _isolate_projects_root_default(tmp_path, monkeypatch):
    """Безусловно подменяем ATLAS_PROJECTS_ROOT на tmp_path/_atlas_isolated_root.

    Защита от инцидента 2026-04-29: tests с `atlas projects add` без явного
    `--no-setup-layout` создавали junction'ы в реальном
    ~/Documents/PROJECT/Clients|Products. Теперь по умолчанию все atlas-команды
    в tests видят tmp_path как root.

    Тесты которым нужна **другая** структура (layout-тесты с их собственными
    projects_root fixture) — переопределяют env своим monkeypatch.setenv
    внутри своих fixtures: последний setenv побеждает.
    """
    # default_actor() кэширует config.owner на процесс — сбрасываем на каждый
    # тест, чтобы изменения конфига/env внутри теста подхватывались.
    try:
        from atlas.appconfig import default_actor
        default_actor.cache_clear()
    except Exception:
        pass
    root = tmp_path / "_atlas_isolated_root"
    root.mkdir(exist_ok=True)
    for sub in (
        "Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"
    ):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    yield
