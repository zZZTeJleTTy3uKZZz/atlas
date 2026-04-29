"""Глобальная конфигурация pytest для всего test suite.

Защищает реальный `~/Documents/PROJECT/` от утечек тестов:
- ATLAS_PROJECTS_ROOT всегда указывает на изолированный tmp_path,
  если тест явно не переопределяет это.

Если когда-нибудь default behavior `atlas projects add` поменяется
(например `--setup-layout=True` будет создавать junction'ы), эта fixture
гарантирует что junction'ы попадут в tmp, а не в реальный fs.
"""
from __future__ import annotations

import pytest


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
    root = tmp_path / "_atlas_isolated_root"
    root.mkdir(exist_ok=True)
    for sub in (
        "Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"
    ):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    yield
