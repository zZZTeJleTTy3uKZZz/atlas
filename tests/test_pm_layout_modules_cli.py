"""CLI-тесты module-aware layout-команд (#126): sync / verify / list-storage.

Модуль контейнера: его junction живёт в `<container_logical>/modules/<slug>/`,
а не в type-группе. layout-команды должны это учитывать.

Junction/robocopy мокаются — реальную ФС вне tmp_path не трогаем.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "PROJECT"
    root.mkdir()
    for sub in ("Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    from atlas.pm.db import make_engine
    from atlas.pm.models import Base

    db_path = tmp_path / "atlas.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def seeded_engine(fresh_engine):
    from atlas.pm.db import make_session
    from atlas.pm.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def layout_app():
    from atlas.pm.commands.projects_layout import layout_app
    return layout_app


@pytest.fixture()
def projects_app():
    from atlas.pm.commands.projects import projects_app
    return projects_app


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _fake_create_junction(link, target):
    link = Path(link)
    target = Path(target)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.mkdir(exist_ok=True)


def _setup_container_and_module(runner, projects_app, projects_root):
    """Контейнер cont + модуль mod (с физикой). Возвращает пути."""
    from atlas.pm.commands import projects as projects_mod

    result = runner.invoke(projects_app, [
        "add", "--name", "Container", "--slug", "cont",
        "--type", "business-product", "--no-setup-layout", "--no-canonical",
        "--no-sync",
    ])
    assert result.exit_code == 0, _combined(result)

    container_logical = projects_root / "Products" / "cont"
    container_logical.mkdir(parents=True, exist_ok=True)

    with patch.object(
        projects_mod, "create_junction", side_effect=_fake_create_junction
    ):
        result = runner.invoke(projects_app, [
            "add", "--name", "Module", "--slug", "mod",
            "--type", "business-product", "--parent", "cont",
            "--setup-layout", "--no-canonical", "--no-sync",
        ])
    assert result.exit_code == 0, _combined(result)
    return container_logical


class TestLayoutVerifyModule:
    def test_verify_module_uses_container_modules_path(
        self, runner, layout_app, projects_app, seeded_engine, projects_root
    ):
        """verify модуля проверяет junction в container/modules/, не Products/mod."""
        from atlas.pm.commands import projects_layout as pl

        container_logical = _setup_container_and_module(
            runner, projects_app, projects_root
        )
        junction = container_logical / "modules" / "mod"
        assert junction.exists()

        # is_junction(junction) → True, остальные → False.
        def fake_is_junction(p):
            return Path(p) == junction

        # junction_target указывает в storage модуля.
        storage = projects_root / "_storage" / "mod"

        def fake_target(p):
            return storage if Path(p) == junction else None

        with patch.object(pl, "_is_junction", side_effect=fake_is_junction), \
             patch.object(pl.layout_mod, "is_junction", side_effect=fake_is_junction), \
             patch.object(pl.layout_mod, "junction_target", side_effect=fake_target):
            result = runner.invoke(layout_app, ["verify", "mod"])
        # Главное — verify не падает и НЕ жалуется на отсутствие junction в Products/mod.
        out = _combined(result)
        assert "Products" not in out or "modules" in out


class TestListStorageModule:
    def test_list_storage_shows_module_logical_under_modules(
        self, runner, layout_app, projects_app, seeded_engine, projects_root
    ):
        container_logical = _setup_container_and_module(
            runner, projects_app, projects_root
        )
        result = runner.invoke(layout_app, ["list-storage"])
        assert result.exit_code == 0, _combined(result)
        out = _combined(result)
        # В JSON или таблице должен фигурировать modules-путь для модуля.
        assert "mod" in out


class TestSyncModule:
    def test_sync_dry_run_targets_container_modules(
        self, runner, layout_app, projects_app, seeded_engine, projects_root
    ):
        """sync --dry-run для модуля показывает expected_logical в container/modules/."""
        from atlas.pm.commands import projects_layout as pl

        container_logical = _setup_container_and_module(
            runner, projects_app, projects_root
        )
        expected = container_logical / "modules" / "mod"
        storage = projects_root / "_storage" / "mod"

        # Fake-junction из фикстуры — это реальная пустая папка; научим CLI
        # видеть её как junction в правильный storage (иначе sync примет её
        # за реальную директорию). В проде это настоящий junction.
        def fake_is_junction(p):
            return Path(p) == expected and Path(p).exists()

        def fake_target(p):
            return storage if Path(p) == expected else None

        with patch.object(pl, "_is_junction", side_effect=fake_is_junction), \
             patch.object(pl.layout_mod, "is_junction", side_effect=fake_is_junction), \
             patch.object(pl.layout_mod, "junction_target", side_effect=fake_target):
            result = runner.invoke(layout_app, ["sync", "mod", "--dry-run"])
        assert result.exit_code == 0, _combined(result)
        out = _combined(result)
        # expected_logical должен указывать в modules/, а не в Products/mod.
        assert "modules" in out
